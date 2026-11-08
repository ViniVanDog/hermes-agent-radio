#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <Preferences.h>
#include <esp_err.h>
#include <esp_timer.h>
#include <driver/adc.h>
#include <driver/uart.h>
#include <driver/gpio.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <soc/soc.h>
#include <math.h>

#include "../BaofengRx/secrets.h"

// ESP32-C3-MINI-1 RX: S+ -> existing coupling/bias circuit -> GPIO3 / ADC1_CH3.
constexpr uint8_t RX_ADC_PIN = 3;
constexpr adc1_channel_t RX_ADC_CHANNEL = ADC1_CHANNEL_3;
constexpr uint16_t ADC_MAX_VALUE = 4095;
constexpr uint16_t ADC_CLIP_GUARD_COUNTS = 64;

// TX hardware. GPIO10 only drives the BC548 base through its 2.2k resistor;
// the radio's PT line is never connected directly to the ESP32.
// ESP32-C3 has no DAC; IO1 generates the TX waveform as high-rate PWM.
constexpr uint8_t TX_PWM_PIN = 1;
constexpr uint8_t PTT_PIN = 10;
constexpr uint8_t PTT_ACTIVE_LEVEL = HIGH;
constexpr uint8_t PTT_INACTIVE_LEVEL = LOW;
constexpr uint32_t PTT_LEAD_MS = 1500;
constexpr uint32_t PTT_TAIL_MS = 150;
constexpr uint32_t PTT_TEST_DURATION_MS = 3000;
constexpr uint32_t TONE_TEST_DURATION_MS = 1000;
constexpr uint32_t TX_TONE_HZ = 1000;
// The C3 LEDC clock supports an exact 8-bit PWM carrier of 156.25 kHz.
// This remains far above the speech band and is within the existing
// attenuation/coupling circuit's intended TX path to M+.
constexpr uint8_t TX_PWM_CHANNEL = 0;
constexpr uint32_t TX_PWM_CARRIER_HZ = 156250;
constexpr uint8_t TX_PWM_RESOLUTION_BITS = 8;
// Full-scale signed PCM maps to an 8-bit PWM duty around midpoint 128.
constexpr uint8_t TX_PWM_AMPLITUDE = 128;
constexpr uint8_t TX_TONE_PWM_AMPLITUDE = 128;
constexpr uint32_t TX_UDP_PORT = 7778;
// The PC intentionally waits through PTT_LEAD_MS after TX_BEGIN before it
// sends PCM. Keep this comfortably longer than that lead so the watchdog
// distinguishes normal radio key-up from a stalled stream.
constexpr uint32_t TX_TIMEOUT_MS = 2000;
constexpr uint8_t TX_SAMPLE_TIMER_NUMBER = 0;
constexpr uint16_t TX_SAMPLE_TIMER_DIVIDER = 2;
constexpr uint32_t TX_SAMPLE_TIMER_TICKS = 2500;  // 80 MHz / 2 / 2500 = 16 kHz.
constexpr uint32_t TX_START_BUFFER_TIMEOUT_MS = 1500;
constexpr uint32_t TX_MAX_TRANSMIT_MS = 60000;
constexpr size_t TX_FRAME_QUEUE_DEPTH = 24;  // 480 ms of 20 ms PCM frames.
constexpr size_t TX_MIN_BUFFER_FRAMES = 6;   // 120 ms before starting audio.
constexpr uint32_t TX_FRAME_WAIT_MS = 30;
constexpr uint8_t TX_MAX_CONSECUTIVE_UNDERRUNS = 3;

constexpr uint32_t STATS_WINDOW_MS = 100;
constexpr uint32_t STREAM_SERIAL_REPORT_MS = 1000;
constexpr uint32_t DC_CALIBRATION_MS = 1000;

// Voice wire format: 16 kHz mono, signed 16-bit, 20 ms packets.
constexpr uint32_t PCM_SAMPLE_RATE_HZ = 16000;
constexpr size_t PCM_FRAME_SAMPLES = 320;
constexpr uint32_t PCM_PACKET_MAGIC = 0x37374642UL;  // Bytes: "BF77".

// PC -> ESP32 TX packets share the Wi-Fi/UDP transport used by RX, but use a
// separate destination port so the two directions are unambiguous.
constexpr uint32_t TX_PACKET_MAGIC = 0x58544642UL;  // Bytes: "BFTX".
constexpr uint8_t TX_PACKET_BEGIN = 1;
constexpr uint8_t TX_PACKET_AUDIO = 2;
constexpr uint8_t TX_PACKET_END = 3;

// This Arduino/ESP-IDF 4.4 ESP32 driver accepts ADC DMA rates no lower than
// 20 kHz. Capture 400 samples in each 20 ms DMA frame and retain 4 of every 5
// samples to produce the required 320-sample / 16 kHz PCM packet. This is
// deterministic rate conversion, not an audio-processing feature.
constexpr uint32_t ADC_DMA_SAMPLE_RATE_HZ = 20000;
constexpr size_t ADC_DMA_FRAME_SAMPLES =
    (PCM_FRAME_SAMPLES * ADC_DMA_SAMPLE_RATE_HZ) / PCM_SAMPLE_RATE_HZ;
static_assert(ADC_DMA_FRAME_SAMPLES == 400, "20 kHz to 16 kHz frame size must be exact");
// C3 TYPE2 DMA records are 32-bit: 12 ADC bits plus channel/unit metadata.
// Do not use uint16_t here; that splits every hardware result in half.
static_assert(sizeof(adc_digi_output_data_t) == SOC_ADC_DIGI_RESULT_BYTES,
              "Unexpected C3 ADC DMA result size");
constexpr uint32_t ADC_DMA_FRAME_BYTES =
    ADC_DMA_FRAME_SAMPLES * sizeof(adc_digi_output_data_t);
constexpr uint32_t ADC_DMA_STORE_BUFFER_BYTES = ADC_DMA_FRAME_BYTES * 8;

struct AdcStats {
  uint16_t minimum = ADC_MAX_VALUE;
  uint16_t maximum = 0;
  uint64_t total = 0;
  uint32_t count = 0;
  uint32_t lowRailSamples = 0;
  uint32_t highRailSamples = 0;

  void add(uint16_t sample) {
    minimum = min(minimum, sample);
    maximum = max(maximum, sample);
    total += sample;
    ++count;
    if (sample <= ADC_CLIP_GUARD_COUNTS) {
      ++lowRailSamples;
    }
    if (sample >= ADC_MAX_VALUE - ADC_CLIP_GUARD_COUNTS) {
      ++highRailSamples;
    }
  }

  uint16_t average() const {
    return count == 0 ? 0 : static_cast<uint16_t>(total / count);
  }

  uint16_t peakToPeak() const {
    return count == 0 ? 0 : maximum - minimum;
  }
};

struct TimingStats {
  uint32_t frames = 0;
  uint32_t framePeriods = 0;
  uint64_t totalAdcDmaReadUs = 0;
  uint64_t totalFramePeriodUs = 0;
  uint32_t maximumAdcDmaReadUs = 0;
  uint32_t maximumFramePeriodUs = 0;

  void add(uint32_t adcDmaReadUs, uint32_t framePeriodUs) {
    ++frames;
    totalAdcDmaReadUs += adcDmaReadUs;
    maximumAdcDmaReadUs = max(maximumAdcDmaReadUs, adcDmaReadUs);
    if (framePeriodUs != 0) {
      ++framePeriods;
      totalFramePeriodUs += framePeriodUs;
      maximumFramePeriodUs = max(maximumFramePeriodUs, framePeriodUs);
    }
  }

  uint32_t frameStartRateHz() const {
    if (totalFramePeriodUs == 0) {
      return 0;
    }
    return static_cast<uint32_t>((static_cast<uint64_t>(framePeriods) * 1000000ULL) /
                                 totalFramePeriodUs);
  }

  uint32_t averageAdcDmaReadUs() const {
    return frames == 0 ? 0 : static_cast<uint32_t>(totalAdcDmaReadUs / frames);
  }

  uint32_t averageFramePeriodUs() const {
    return framePeriods == 0 ? 0 : static_cast<uint32_t>(totalFramePeriodUs / framePeriods);
  }
};

// Header and samples are little-endian, as documented in bridge.py.
struct __attribute__((packed)) UdpPcmPacket {
  uint32_t magic;
  uint16_t sequence;
  uint16_t sampleCount;
  int16_t samples[PCM_FRAME_SAMPLES];
};
static_assert(sizeof(UdpPcmPacket) == 648, "Unexpected UDP packet size");

// All TX UDP fields are little-endian. Audio payloads are exactly one 20 ms
// frame of signed 16-bit 16 kHz mono PCM.
struct __attribute__((packed)) TxUdpHeader {
  uint32_t magic;
  uint8_t type;
  uint8_t reserved;
  uint16_t sequence;
  uint16_t payloadBytes;
};
static_assert(sizeof(TxUdpHeader) == 10, "Unexpected TX header size");

struct TxAudioFrame {
  uint16_t sequence;
  int16_t samples[PCM_FRAME_SAMPLES];
};
static_assert(sizeof(TxAudioFrame) == 642, "Unexpected TX frame size");

enum RadioState : uint8_t {
  RADIO_IDLE,
  RADIO_TX_STARTING,
  RADIO_TRANSMITTING,
  RADIO_TX_ENDING,
};

enum TxCommand : uint8_t {
  TX_COMMAND_STREAM_BEGIN,
  TX_COMMAND_PTT_TEST,
  TX_COMMAND_TONE_TEST,
};

enum TxAbortReason : uint8_t {
  TX_ABORT_NONE,
  TX_ABORT_WIFI_DISCONNECT,
  TX_ABORT_MALFORMED_PACKET,
  TX_ABORT_STREAM_TIMEOUT,
  TX_ABORT_MAX_DURATION,
  TX_ABORT_BUFFER_OVERFLOW,
  TX_ABORT_DAC_ERROR,
  TX_ABORT_START_BUFFER_TIMEOUT,
};

WiFiUDP udp;
UdpPcmPacket packet{};
adc_digi_output_data_t rawAdcFrame[ADC_DMA_FRAME_SAMPLES];
AdcStats windowStats;
TimingStats timingStats;
uint16_t dcCenter = 0;  // Measured at runtime; never hard-coded as 2048.
uint16_t sequenceNumber = 0;
uint32_t windowStartedAtMs = 0;
uint32_t lastSerialReportAtMs = 0;
uint32_t adcDmaOverruns = 0;
uint32_t shortDmaFrames = 0;
uint32_t adcDmaReadErrors = 0;
int64_t previousFrameStartedAtUs = 0;
bool wifiConnected = false;
bool streamAudio = false;
Preferences profilePreferences;
bool receiveOnlyProfile = false;

QueueHandle_t txAudioQueue = nullptr;
QueueHandle_t txCommandQueue = nullptr;
TaskHandle_t txTaskHandle = nullptr;
volatile RadioState radioState = RADIO_IDLE;
volatile bool txEndReceived = false;
volatile bool txAbortRequested = false;
volatile bool txStreamActive = false;
volatile TxAbortReason txAbortReason = TX_ABORT_NONE;
volatile uint32_t lastTxPacketAtMs = 0;
volatile bool txSequenceInitialized = false;
volatile uint16_t txBeginSequence = 0;
volatile uint16_t expectedTxSequence = 0;
bool txSubsystemReady = false;
uint8_t txReceiveBuffer[sizeof(TxUdpHeader) + PCM_FRAME_SAMPLES * sizeof(int16_t)];
uint8_t dacFrame[PCM_FRAME_SAMPLES];
uint8_t txDacBuffers[2][PCM_FRAME_SAMPLES];
hw_timer_t* txSampleTimer = nullptr;
portMUX_TYPE txDacMux = portMUX_INITIALIZER_UNLOCKED;
volatile uint8_t txDacBufferState[2] = {0, 0};  // 0=free, 1=filling, 2=queued, 3=active.
volatile int8_t txDacActiveBuffer = -1;
volatile uint16_t txDacSampleIndex = 0;

constexpr uint8_t DAC_BUFFER_FREE = 0;
constexpr uint8_t DAC_BUFFER_FILLING = 1;
constexpr uint8_t DAC_BUFFER_QUEUED = 2;
constexpr uint8_t DAC_BUFFER_ACTIVE = 3;

bool isTransmitting() {
  return radioState != RADIO_IDLE;
}

void fatalAdcDma(const char* operation, esp_err_t error) {
  Serial.print("FATAL ADC DMA error in ");
  Serial.print(operation);
  Serial.print(": ");
  Serial.println(esp_err_to_name(error));
  while (true) {
    delay(1000);
  }
}

void initializeAdcDma() {
  // This is deliberately not I2S's legacy built-in-ADC mode. On this ESP32 it
  // ran at ~89.6 kHz despite its nominal I2S clock setting. The ADC digital
  // controller below has a real, explicit 20,000 Hz conversion clock.
  adc_digi_init_config_t initConfig = {};
  initConfig.max_store_buf_size = ADC_DMA_STORE_BUFFER_BYTES;
  initConfig.conv_num_each_intr = ADC_DMA_FRAME_BYTES;
  initConfig.adc1_chan_mask = (1U << RX_ADC_CHANNEL);
  initConfig.adc2_chan_mask = 0;

  esp_err_t result = adc_digi_initialize(&initConfig);
  if (result != ESP_OK) {
    fatalAdcDma("adc_digi_initialize", result);
  }

  static adc_digi_pattern_config_t pattern[1];
  pattern[0].atten = ADC_ATTEN_DB_12;
  pattern[0].channel = RX_ADC_CHANNEL;
  // The ESP32's legacy adc_digi driver encodes ADC1 as hardware index 0.
  // (ADC_UNIT_1 is 1 in the public enum and fails this driver's validation.)
  pattern[0].unit = 0;
  // DMA patterns use the physical output width (12), not the legacy
  // ADC_WIDTH_BIT_12 single-read enum value (3).
  pattern[0].bit_width = SOC_ADC_DIGI_MAX_BITWIDTH;

  adc_digi_configuration_t config = {};
  // Unlike the original ESP32, the C3 really stops converting once this limit
  // is reached. Keeping it disabled is required for a continuous RX stream;
  // with a 255-sample limit the old setting produced one partial DMA frame and
  // then no further samples, so no UDP packets could be assembled.
  config.conv_limit_en = false;
  config.conv_limit_num = 0;
  config.pattern_num = 1;
  config.adc_pattern = pattern;
  config.sample_freq_hz = ADC_DMA_SAMPLE_RATE_HZ;
  config.conv_mode = ADC_CONV_SINGLE_UNIT_1;
  config.format = ADC_DIGI_OUTPUT_FORMAT_TYPE2;
  result = adc_digi_controller_configure(&config);
  if (result != ESP_OK) {
    fatalAdcDma("adc_digi_controller_configure", result);
  }

  result = adc_digi_start();
  if (result != ESP_OK) {
    fatalAdcDma("adc_digi_start", result);
  }
}

void stopAdcDmaForBlockingWork() {
  const esp_err_t result = adc_digi_stop();
  if (result != ESP_OK) {
    fatalAdcDma("adc_digi_stop", result);
  }
}

void restartAdcDmaAfterBlockingWork() {
  const esp_err_t result = adc_digi_start();
  if (result != ESP_OK) {
    fatalAdcDma("adc_digi_start after blocking work", result);
  }
}

bool readDmaFrame(uint32_t* readDurationUs) {
  // The C3 DMA driver may return a valid prefix of the requested data. Keep
  // collecting until one complete 20 ms frame is assembled; discarding those
  // prefixes produced a 14 kHz stream and PC playback underruns.
  uint8_t* destination = reinterpret_cast<uint8_t*>(rawAdcFrame);
  size_t bytesCollected = 0;
  const int64_t readStartedAtUs = esp_timer_get_time();

  while (bytesCollected < sizeof(rawAdcFrame)) {
    uint32_t bytesRead = 0;
    const size_t bytesRemaining = sizeof(rawAdcFrame) - bytesCollected;
    const esp_err_t result = adc_digi_read_bytes(destination + bytesCollected,
                                                  bytesRemaining, &bytesRead, 1000);
    if (result != ESP_OK) {
      if (result == ESP_ERR_INVALID_STATE) {
        ++adcDmaOverruns;
      } else {
        ++adcDmaReadErrors;
      }
      *readDurationUs = static_cast<uint32_t>(esp_timer_get_time() - readStartedAtUs);
      return false;
    }
    if (bytesRead == 0 || bytesRead > bytesRemaining) {
      ++shortDmaFrames;
      *readDurationUs = static_cast<uint32_t>(esp_timer_get_time() - readStartedAtUs);
      return false;
    }
    bytesCollected += bytesRead;
  }

  *readDurationUs = static_cast<uint32_t>(esp_timer_get_time() - readStartedAtUs);
  return true;
}

const char* clippingStatus(const AdcStats& stats) {
  if (stats.lowRailSamples && stats.highRailSamples) {
    return "LOW+HIGH";
  }
  if (stats.lowRailSamples) {
    return "LOW";
  }
  if (stats.highRailSamples) {
    return "HIGH";
  }
  return "none";
}

void printClippingWarning(const AdcStats& stats) {
  if (!stats.lowRailSamples && !stats.highRailSamples) {
    return;
  }
  Serial.print("  WARNING: ADC clipping near ");
  Serial.print(clippingStatus(stats));
  Serial.print(" rail (guard = ");
  Serial.print(ADC_CLIP_GUARD_COUNTS);
  Serial.println(" counts).");
}

void calibrateDcCenter() {
  Serial.print("Measuring idle DC center for ");
  Serial.print(DC_CALIBRATION_MS);
  Serial.println(" ms. Keep the radio quiet during this measurement.");

  AdcStats calibration;
  const uint32_t startedAt = millis();
  while (millis() - startedAt < DC_CALIBRATION_MS) {
    uint32_t ignoredReadDurationUs = 0;
    if (!readDmaFrame(&ignoredReadDurationUs)) {
      continue;
    }
    for (size_t index = 0; index < ADC_DMA_FRAME_SAMPLES; ++index) {
      // C3 TYPE2 DMA records include unit/channel metadata as well as the
      // conversion code. Extract only the conversion code.
      calibration.add(rawAdcFrame[index].type2.data);
    }
  }

  dcCenter = calibration.average();
  Serial.print("DC center measured: ");
  Serial.print(dcCenter);
  Serial.print(" (min=");
  Serial.print(calibration.minimum);
  Serial.print(", max=");
  Serial.print(calibration.maximum);
  Serial.print(", p-p=");
  Serial.print(calibration.peakToPeak());
  Serial.println(")");
  printClippingWarning(calibration);
}

int16_t adcToSignedPcm(uint16_t rawSample) {
  const int32_t scaled = (static_cast<int32_t>(rawSample) - dcCenter) * 16;
  return static_cast<int16_t>(constrain(scaled, -32768, 32767));
}

void printWindowStats(uint32_t reportIntervalMs) {
  Serial.print("RX window_ms=");
  Serial.print(STATS_WINDOW_MS);
  Serial.print(" report_ms=");
  Serial.print(reportIntervalMs);
  Serial.print(" min=");
  Serial.print(windowStats.minimum);
  Serial.print(" max=");
  Serial.print(windowStats.maximum);
  Serial.print(" avg=");
  Serial.print(windowStats.average());
  Serial.print(" dc=");
  Serial.print(dcCenter);
  Serial.print(" p-p=");
  Serial.print(windowStats.peakToPeak());
  Serial.print(" n=");
  Serial.print(windowStats.count);
  Serial.print(" clipping=");
  Serial.print(clippingStatus(windowStats));
  Serial.print(" udp=");
  Serial.print(streamAudio ? "on" : "off");
  Serial.print(" frames=");
  Serial.print(timingStats.frames);
  Serial.print(" frame_start_hz=");
  Serial.print(timingStats.frameStartRateHz());
  Serial.print(" adc_dma_read_us_avg=");
  Serial.print(timingStats.averageAdcDmaReadUs());
  Serial.print(" adc_dma_read_us_max=");
  Serial.print(timingStats.maximumAdcDmaReadUs);
  Serial.print(" frame_period_us_avg=");
  Serial.print(timingStats.averageFramePeriodUs());
  Serial.print(" frame_period_us_max=");
  Serial.print(timingStats.maximumFramePeriodUs);
  Serial.print(" adc_dma_ovf=");
  Serial.print(adcDmaOverruns);
  Serial.print(" short_frames=");
  Serial.print(shortDmaFrames);
  Serial.print(" adc_dma_errors=");
  Serial.println(adcDmaReadErrors);
  printClippingWarning(windowStats);
}

void sendAudioPacket() {
  if (!streamAudio || WiFi.status() != WL_CONNECTED || isTransmitting()) {
    return;
  }
  packet.magic = PCM_PACKET_MAGIC;
  packet.sequence = sequenceNumber++;
  packet.sampleCount = PCM_FRAME_SAMPLES;
  udp.beginPacket(HOST_PC_IP, UDP_AUDIO_PORT);
  udp.write(reinterpret_cast<const uint8_t*>(&packet), sizeof(packet));
  udp.endPacket();
}

const char* txAbortReasonName(TxAbortReason reason) {
  switch (reason) {
    case TX_ABORT_WIFI_DISCONNECT:
      return "Wi-Fi disconnect";
    case TX_ABORT_MALFORMED_PACKET:
      return "malformed packet";
    case TX_ABORT_STREAM_TIMEOUT:
      return "stream timeout";
    case TX_ABORT_MAX_DURATION:
      return "maximum transmit duration";
    case TX_ABORT_BUFFER_OVERFLOW:
      return "TX buffer overflow";
    case TX_ABORT_DAC_ERROR:
      return "PWM timer error";
    case TX_ABORT_START_BUFFER_TIMEOUT:
      return "start buffer timeout";
    case TX_ABORT_NONE:
    default:
      return "unknown";
  }
}

void setPtt(bool enabled) {
  if (enabled && receiveOnlyProfile) return;
  digitalWrite(PTT_PIN, enabled ? PTT_ACTIVE_LEVEL : PTT_INACTIVE_LEVEL);
  Serial.println(enabled ? "PTT ON" : "PTT OFF");
}

void requestTxAbort(TxAbortReason reason) {
  if (!isTransmitting() || txAbortRequested) {
    return;
  }
  txAbortReason = reason;
  txAbortRequested = true;
  // This is intentionally immediate, even though the audio task will do its
  // normal cleanup shortly afterwards. A missing PC must never hold PTT down.
  digitalWrite(PTT_PIN, PTT_INACTIVE_LEVEL);
  Serial.print("TX abort requested: ");
  Serial.println(txAbortReasonName(reason));
}

void IRAM_ATTR onTxSampleTimer() {
  uint8_t value = 128;
  portENTER_CRITICAL_ISR(&txDacMux);
  int8_t active = txDacActiveBuffer;
  if (active < 0) {
    for (uint8_t index = 0; index < 2; ++index) {
      if (txDacBufferState[index] == DAC_BUFFER_QUEUED) {
        txDacBufferState[index] = DAC_BUFFER_ACTIVE;
        txDacActiveBuffer = static_cast<int8_t>(index);
        txDacSampleIndex = 0;
        active = static_cast<int8_t>(index);
        break;
      }
    }
  }
  if (active >= 0) {
    value = txDacBuffers[active][txDacSampleIndex++];
    if (txDacSampleIndex == PCM_FRAME_SAMPLES) {
      txDacBufferState[active] = DAC_BUFFER_FREE;
      txDacActiveBuffer = -1;
      txDacSampleIndex = 0;
      for (uint8_t index = 0; index < 2; ++index) {
        if (txDacBufferState[index] == DAC_BUFFER_QUEUED) {
          txDacBufferState[index] = DAC_BUFFER_ACTIVE;
          txDacActiveBuffer = static_cast<int8_t>(index);
          break;
        }
      }
    }
  }
  portEXIT_CRITICAL_ISR(&txDacMux);
  ledcWrite(TX_PWM_CHANNEL, value);
}

void resetDacBuffers() {
  portENTER_CRITICAL(&txDacMux);
  txDacBufferState[0] = DAC_BUFFER_FREE;
  txDacBufferState[1] = DAC_BUFFER_FREE;
  txDacActiveBuffer = -1;
  txDacSampleIndex = 0;
  portEXIT_CRITICAL(&txDacMux);
}

bool queueDacFrame() {
  while (true) {
    int8_t freeBuffer = -1;
    portENTER_CRITICAL(&txDacMux);
    for (uint8_t index = 0; index < 2; ++index) {
      if (txDacBufferState[index] == DAC_BUFFER_FREE) {
        txDacBufferState[index] = DAC_BUFFER_FILLING;
        freeBuffer = static_cast<int8_t>(index);
        break;
      }
    }
    portEXIT_CRITICAL(&txDacMux);
    if (freeBuffer >= 0) {
      memcpy(txDacBuffers[freeBuffer], dacFrame, sizeof(dacFrame));
      portENTER_CRITICAL(&txDacMux);
      txDacBufferState[freeBuffer] = DAC_BUFFER_QUEUED;
      portEXIT_CRITICAL(&txDacMux);
      return true;
    }
    if (txAbortRequested) {
      return false;
    }
    vTaskDelay(pdMS_TO_TICKS(1));
  }
}

bool dacFramesPending() {
  bool pending = false;
  portENTER_CRITICAL(&txDacMux);
  pending = txDacActiveBuffer >= 0 ||
            txDacBufferState[0] != DAC_BUFFER_FREE ||
            txDacBufferState[1] != DAC_BUFFER_FREE;
  portEXIT_CRITICAL(&txDacMux);
  return pending;
}

bool writeDacFrame() {
  return queueDacFrame();
}

bool writeDacCenterFrame() {
  memset(dacFrame, 128, sizeof(dacFrame));
  return writeDacFrame();
}

bool startDacOutput() {
  if (txSampleTimer == nullptr) {
    return false;
  }
  resetDacBuffers();
  ledcWrite(TX_PWM_CHANNEL, 128);
  if (!writeDacCenterFrame()) {
    return false;
  }
  timerAlarmEnable(txSampleTimer);
  return true;
}

void stopDacOutput() {
  // Queue one final midpoint frame before stopping the PWM sample timer.
  writeDacCenterFrame();
  const uint32_t startedAtMs = millis();
  while (dacFramesPending() && millis() - startedAtMs < 100) {
    vTaskDelay(pdMS_TO_TICKS(1));
  }
  timerAlarmDisable(txSampleTimer);
  ledcWrite(TX_PWM_CHANNEL, 128);
  resetDacBuffers();
}

bool waitWithAbort(uint32_t durationMs) {
  const uint32_t startedAtMs = millis();
  while (millis() - startedAtMs < durationMs) {
    if (txAbortRequested) {
      return false;
    }
    vTaskDelay(pdMS_TO_TICKS(5));
  }
  return true;
}

void finishTxSession(bool dacWasStarted, bool completed) {
  radioState = RADIO_TX_ENDING;
  if (dacWasStarted) {
    stopDacOutput();
  }

  if (completed && !txAbortRequested) {
    Serial.print("TX END; PTT tail_ms=");
    Serial.println(PTT_TAIL_MS);
    vTaskDelay(pdMS_TO_TICKS(PTT_TAIL_MS));
  } else {
    Serial.print("TX aborted: ");
    Serial.println(txAbortReasonName(txAbortReason));
  }

  setPtt(false);
  xQueueReset(txAudioQueue);
  txEndReceived = false;
  txAbortRequested = false;
  txAbortReason = TX_ABORT_NONE;
  txSequenceInitialized = false;
  txStreamActive = false;
  radioState = RADIO_IDLE;
}

uint8_t pcmToDac(int16_t pcm) {
  // Signed 16-bit PCM becomes an 8-bit PWM duty around midpoint 128. The
  // explicit clamp prevents duty-cycle wraparound.
  const int32_t scaled = (static_cast<int32_t>(pcm) * TX_PWM_AMPLITUDE) / 32768;
  return static_cast<uint8_t>(constrain(128 + scaled, 0, 255));
}

bool playPcmFrame(const TxAudioFrame& frame) {
  for (size_t index = 0; index < PCM_FRAME_SAMPLES; ++index) {
    dacFrame[index] = pcmToDac(frame.samples[index]);
  }
  return writeDacFrame();
}

void runPttDiagnostic() {
  Serial.print("TX PTT test: on for ");
  Serial.print(PTT_TEST_DURATION_MS);
  Serial.println(" ms.");
  setPtt(true);
  const bool completed = waitWithAbort(PTT_TEST_DURATION_MS);
  finishTxSession(false, completed);
}

void runToneDiagnostic() {
  Serial.print("TX tone test: ");
  Serial.print(TX_TONE_HZ);
  Serial.print(" Hz, PWM duty 128 +/- ");
  Serial.println(TX_TONE_PWM_AMPLITUDE);
  setPtt(true);
  if (!waitWithAbort(PTT_LEAD_MS)) {
    finishTxSession(false, false);
    return;
  }

  if (!startDacOutput()) {
    requestTxAbort(TX_ABORT_DAC_ERROR);
    finishTxSession(false, false);
    return;
  }

  radioState = RADIO_TRANSMITTING;
  const uint32_t frames = (TONE_TEST_DURATION_MS * PCM_SAMPLE_RATE_HZ) /
                          (PCM_FRAME_SAMPLES * 1000UL);
  float phase = 0.0F;
  const float phaseStep = (2.0F * PI * TX_TONE_HZ) / PCM_SAMPLE_RATE_HZ;
  bool completed = true;
  for (uint32_t frame = 0; frame < frames && !txAbortRequested; ++frame) {
    for (size_t index = 0; index < PCM_FRAME_SAMPLES; ++index) {
      const int32_t value = 128 + static_cast<int32_t>(
          lroundf(sinf(phase) * static_cast<float>(TX_TONE_PWM_AMPLITUDE)));
      dacFrame[index] = static_cast<uint8_t>(constrain(value, 0, 255));
      phase += phaseStep;
      if (phase >= 2.0F * PI) {
        phase -= 2.0F * PI;
      }
    }
    if (!writeDacFrame()) {
      requestTxAbort(TX_ABORT_DAC_ERROR);
      completed = false;
      break;
    }
  }
  if (txAbortRequested) {
    completed = false;
  }
  finishTxSession(true, completed);
}

void runStreamTransmission() {
  bool dacStarted = false;
  bool completed = false;
  setPtt(true);
  Serial.print("TX BEGIN; PTT lead_ms=");
  Serial.println(PTT_LEAD_MS);

  if (!waitWithAbort(PTT_LEAD_MS)) {
    finishTxSession(false, false);
    return;
  }

  const uint32_t bufferingStartedAtMs = millis();
  while (uxQueueMessagesWaiting(txAudioQueue) < TX_MIN_BUFFER_FRAMES) {
    if (txAbortRequested) {
      finishTxSession(false, false);
      return;
    }
    if (millis() - bufferingStartedAtMs > TX_START_BUFFER_TIMEOUT_MS ||
        (txEndReceived && uxQueueMessagesWaiting(txAudioQueue) == 0)) {
      requestTxAbort(TX_ABORT_START_BUFFER_TIMEOUT);
      finishTxSession(false, false);
      return;
    }
    vTaskDelay(pdMS_TO_TICKS(5));
  }

  Serial.print("TX playback started; buffered frames=");
  Serial.println(uxQueueMessagesWaiting(txAudioQueue));
  if (!startDacOutput()) {
    requestTxAbort(TX_ABORT_DAC_ERROR);
    finishTxSession(false, false);
    return;
  }
  dacStarted = true;
  radioState = RADIO_TRANSMITTING;

  const uint32_t playbackStartedAtMs = millis();
  uint8_t consecutiveUnderruns = 0;
  while (!txAbortRequested) {
    if (millis() - playbackStartedAtMs > TX_MAX_TRANSMIT_MS) {
      requestTxAbort(TX_ABORT_MAX_DURATION);
      break;
    }

    TxAudioFrame frame{};
    if (xQueueReceive(txAudioQueue, &frame, pdMS_TO_TICKS(TX_FRAME_WAIT_MS)) == pdPASS) {
      consecutiveUnderruns = 0;
      if (!playPcmFrame(frame)) {
        requestTxAbort(TX_ABORT_DAC_ERROR);
      }
      continue;
    }

    if (txEndReceived && uxQueueMessagesWaiting(txAudioQueue) == 0) {
      completed = true;
      break;
    }
    if (millis() - lastTxPacketAtMs > TX_TIMEOUT_MS) {
      requestTxAbort(TX_ABORT_STREAM_TIMEOUT);
      break;
    }

    ++consecutiveUnderruns;
    Serial.print("TX underrun: ");
    Serial.println(consecutiveUnderruns);
    if (consecutiveUnderruns > TX_MAX_CONSECUTIVE_UNDERRUNS || !writeDacCenterFrame()) {
      requestTxAbort(TX_ABORT_STREAM_TIMEOUT);
      break;
    }
  }

  finishTxSession(dacStarted, completed && !txAbortRequested);
}

void txAudioTask(void*) {
  while (true) {
    TxCommand command = TX_COMMAND_STREAM_BEGIN;
    if (xQueueReceive(txCommandQueue, &command, portMAX_DELAY) != pdPASS) {
      continue;
    }
    switch (command) {
      case TX_COMMAND_STREAM_BEGIN:
        runStreamTransmission();
        break;
      case TX_COMMAND_PTT_TEST:
        runPttDiagnostic();
        break;
      case TX_COMMAND_TONE_TEST:
        runToneDiagnostic();
        break;
    }
  }
}

void initializeTxSubsystem() {
  digitalWrite(PTT_PIN, PTT_INACTIVE_LEVEL);
  pinMode(PTT_PIN, OUTPUT);
  digitalWrite(PTT_PIN, PTT_INACTIVE_LEVEL);  // PTT is released before Wi-Fi work.

  txAudioQueue = xQueueCreate(TX_FRAME_QUEUE_DEPTH, sizeof(TxAudioFrame));
  txCommandQueue = xQueueCreate(2, sizeof(TxCommand));
  if (txAudioQueue == nullptr || txCommandQueue == nullptr) {
    Serial.println("TX disabled: unable to allocate FreeRTOS queues.");
    return;
  }

  txSampleTimer = timerBegin(TX_SAMPLE_TIMER_NUMBER, TX_SAMPLE_TIMER_DIVIDER, true);
  if (txSampleTimer == nullptr) {
    Serial.println("TX disabled: unable to allocate the PWM sample timer.");
    return;
  }
  timerAttachInterruptFlag(txSampleTimer, &onTxSampleTimer, true, ESP_INTR_FLAG_IRAM);
  timerAlarmWrite(txSampleTimer, TX_SAMPLE_TIMER_TICKS, true);
  timerAlarmDisable(txSampleTimer);
  if (ledcSetup(TX_PWM_CHANNEL, TX_PWM_CARRIER_HZ, TX_PWM_RESOLUTION_BITS) == 0) {
    timerEnd(txSampleTimer);
    txSampleTimer = nullptr;
    Serial.println("TX disabled: unable to configure IO1 PWM carrier.");
    return;
  }
  ledcAttachPin(TX_PWM_PIN, TX_PWM_CHANNEL);
  ledcWrite(TX_PWM_CHANNEL, 128);

  if (xTaskCreatePinnedToCore(txAudioTask, "txAudio", 6144, nullptr, 2,
                              &txTaskHandle, 0) != pdPASS) {
    timerEnd(txSampleTimer);
    txSampleTimer = nullptr;
    Serial.println("TX disabled: unable to start audio task; RX remains available.");
    return;
  }
  txSubsystemReady = true;
  Serial.println("TX ready: GPIO1 PWM audio and GPIO10 BC548 PTT; PTT is OFF.");
}

void finishStatsWindowIfNeeded() {
  const uint32_t nowMs = millis();
  if (nowMs - windowStartedAtMs < STATS_WINDOW_MS) {
    return;
  }

  // Keep genuine 100 ms measurement windows. Streaming only reports once a
  // second because long UART prints must not perturb the audio task.
  const uint32_t reportIntervalMs = streamAudio ? STREAM_SERIAL_REPORT_MS : STATS_WINDOW_MS;
  if (nowMs - lastSerialReportAtMs >= reportIntervalMs) {
    printWindowStats(reportIntervalMs);
    timingStats = TimingStats{};
    adcDmaOverruns = 0;
    shortDmaFrames = 0;
    adcDmaReadErrors = 0;
    lastSerialReportAtMs = nowMs;
  }

  windowStats = AdcStats{};
  windowStartedAtMs = nowMs;
}

bool queueTxCommand(TxCommand command) {
  if (receiveOnlyProfile || !txSubsystemReady || radioState != RADIO_IDLE) {
    Serial.println("TX command rejected: transmitter is unavailable or busy.");
    return false;
  }
  radioState = RADIO_TX_STARTING;
  txAbortRequested = false;
  txAbortReason = TX_ABORT_NONE;
  txEndReceived = false;
  if (xQueueSend(txCommandQueue, &command, 0) != pdPASS) {
    radioState = RADIO_IDLE;
    Serial.println("TX command rejected: control queue is full.");
    return false;
  }
  return true;
}

void handleMalformedTxPacket(const char* reason) {
  Serial.print("TX malformed packet: ");
  Serial.println(reason);
  requestTxAbort(TX_ABORT_MALFORMED_PACKET);
}

bool acceptTxSequence(uint16_t sequence) {
  if (!txSequenceInitialized) {
    handleMalformedTxPacket("packet before TX_BEGIN");
    return false;
  }
  if (sequence == expectedTxSequence) {
    expectedTxSequence = static_cast<uint16_t>(sequence + 1);
    return true;
  }

  const uint16_t gap = static_cast<uint16_t>(sequence - expectedTxSequence);
  if (gap >= 0x8000U) {
    // A late/reordered old datagram must not be played after newer audio.
    Serial.println("TX stale/out-of-order packet discarded.");
    return false;
  }
  Serial.print("TX sequence gap; lost packets=");
  Serial.println(gap);
  expectedTxSequence = static_cast<uint16_t>(sequence + 1);
  return true;
}

void beginTxStream(uint16_t sequence) {
  if (receiveOnlyProfile) return;
  if (!txSubsystemReady) {
    Serial.println("TX_BEGIN ignored: TX subsystem is unavailable.");
    return;
  }
  if (radioState != RADIO_IDLE) {
    // UDP does not guarantee delivery. A PC sender may repeat the same BEGIN
    // sequence to make session startup reliable; it must not abort this TX.
    if (txStreamActive && txSequenceInitialized && !txEndReceived &&
        sequence == txBeginSequence) {
      lastTxPacketAtMs = millis();
      Serial.println("TX_BEGIN duplicate accepted.");
      return;
    }
    handleMalformedTxPacket("TX_BEGIN while another TX is active");
    return;
  }

  xQueueReset(txAudioQueue);
  txAbortRequested = false;
  txAbortReason = TX_ABORT_NONE;
  txEndReceived = false;
  txStreamActive = true;
  txSequenceInitialized = true;
  txBeginSequence = sequence;
  expectedTxSequence = static_cast<uint16_t>(sequence + 1);
  lastTxPacketAtMs = millis();
  Serial.println("TX_BEGIN accepted.");
  if (!queueTxCommand(TX_COMMAND_STREAM_BEGIN)) {
    txStreamActive = false;
    txSequenceInitialized = false;
  }
}

void handleIncomingTxPackets() {
  while (true) {
    const int packetBytes = udp.parsePacket();
    if (packetBytes <= 0) {
      return;
    }
    if (receiveOnlyProfile) {
      udp.flush();
      continue;
    }
    if (packetBytes < static_cast<int>(sizeof(TxUdpHeader)) ||
        packetBytes > static_cast<int>(sizeof(txReceiveBuffer))) {
      udp.flush();
      handleMalformedTxPacket("invalid datagram length");
      continue;
    }

    const int receivedBytes = udp.read(txReceiveBuffer, packetBytes);
    if (receivedBytes != packetBytes) {
      handleMalformedTxPacket("short UDP read");
      continue;
    }

    TxUdpHeader header{};
    memcpy(&header, txReceiveBuffer, sizeof(header));
    if (header.magic != TX_PACKET_MAGIC || header.reserved != 0 ||
        header.payloadBytes != packetBytes - static_cast<int>(sizeof(TxUdpHeader))) {
      handleMalformedTxPacket("bad header");
      continue;
    }

    switch (header.type) {
      case TX_PACKET_BEGIN:
        if (header.payloadBytes != 0) {
          handleMalformedTxPacket("TX_BEGIN has payload");
        } else {
          beginTxStream(header.sequence);
        }
        break;

      case TX_PACKET_AUDIO: {
        if (header.payloadBytes != PCM_FRAME_SAMPLES * sizeof(int16_t) ||
            !isTransmitting() || txEndReceived || !acceptTxSequence(header.sequence)) {
          if (!isTransmitting()) {
            handleMalformedTxPacket("audio without active TX");
          } else if (header.payloadBytes != PCM_FRAME_SAMPLES * sizeof(int16_t)) {
            handleMalformedTxPacket("wrong audio payload length");
          }
          break;
        }
        TxAudioFrame frame{};
        frame.sequence = header.sequence;
        memcpy(frame.samples, txReceiveBuffer + sizeof(TxUdpHeader), sizeof(frame.samples));
        if (xQueueSend(txAudioQueue, &frame, 0) != pdPASS) {
          handleMalformedTxPacket("audio queue full");
          requestTxAbort(TX_ABORT_BUFFER_OVERFLOW);
          break;
        }
        lastTxPacketAtMs = millis();
        break;
      }

      case TX_PACKET_END:
        if (header.payloadBytes != 0 || !isTransmitting() ||
            !acceptTxSequence(header.sequence)) {
          if (header.payloadBytes != 0 || !isTransmitting()) {
            handleMalformedTxPacket("invalid TX_END");
          }
          break;
        }
        txEndReceived = true;
        lastTxPacketAtMs = millis();
        Serial.println("TX END received; draining buffered audio.");
        break;

      default:
        handleMalformedTxPacket("unknown packet type");
        break;
    }
  }
}

void enforceTxSafety() {
  if (isTransmitting() && WiFi.status() != WL_CONNECTED) {
    requestTxAbort(TX_ABORT_WIFI_DISCONNECT);
    return;
  }
  if (txStreamActive && !txEndReceived &&
      millis() - lastTxPacketAtMs > TX_TIMEOUT_MS) {
    requestTxAbort(TX_ABORT_STREAM_TIMEOUT);
  }
}

void collectAndMaybeStreamFrame() {
  const int64_t frameStartedAtUs = esp_timer_get_time();
  const uint32_t framePeriodUs = previousFrameStartedAtUs == 0
                                     ? 0
                                     : static_cast<uint32_t>(frameStartedAtUs - previousFrameStartedAtUs);
  previousFrameStartedAtUs = frameStartedAtUs;

  uint32_t adcDmaReadUs = 0;
  if (!readDmaFrame(&adcDmaReadUs)) {
    finishStatsWindowIfNeeded();
    return;
  }

  size_t pcmIndex = 0;
  for (size_t index = 0; index < ADC_DMA_FRAME_SAMPLES; ++index) {
    const uint16_t rawSample = rawAdcFrame[index].type2.data;
    windowStats.add(rawSample);

    // 20 kHz -> 16 kHz exactly: keep four samples, drop the fifth.
    if ((index % 5) != 4) {
      packet.samples[pcmIndex++] = adcToSignedPcm(rawSample);
    }
  }
  if (pcmIndex != PCM_FRAME_SAMPLES) {
    Serial.println("FATAL: ADC-to-PCM frame conversion length mismatch.");
    while (true) {
      delay(1000);
    }
  }

  timingStats.add(adcDmaReadUs, framePeriodUs);
  sendAudioPacket();
  finishStatsWindowIfNeeded();
}

constexpr size_t PROGRAM_TRACE_CAPACITY = 192;
struct ProgrammingTrace {
  uint8_t pin;
  volatile uint32_t count;
  volatile uint32_t atUs[PROGRAM_TRACE_CAPACITY];
  volatile uint8_t level[PROGRAM_TRACE_CAPACITY];
};
ProgrammingTrace programmingTxTrace = {PTT_PIN, 0, {}, {}};
ProgrammingTrace programmingRxTrace = {4, 0, {}, {}};
void printProgrammingTrace(const char *name, const ProgrammingTrace &trace);

void IRAM_ATTR onProgrammingEdge(void *argument) {
  ProgrammingTrace *trace = static_cast<ProgrammingTrace *>(argument);
  const uint32_t index = trace->count;
  if (index < PROGRAM_TRACE_CAPACITY) {
    trace->atUs[index] = static_cast<uint32_t>(esp_timer_get_time());
    trace->level[index] = gpio_get_level(static_cast<gpio_num_t>(trace->pin));
  }
  trace->count = index + 1;
}

void printProgrammingTrace(const char *name, const ProgrammingTrace &trace) {
  Serial.printf("CHIRP TRACE %s edges=%lu data=", name,
                static_cast<unsigned long>(trace.count));
  const uint32_t origin = trace.count ? trace.atUs[0] : 0;
  for (uint32_t index = 0; index < trace.count && index < PROGRAM_TRACE_CAPACITY; ++index) {
    if (index) Serial.print(',');
    Serial.printf("%lu:%u", static_cast<unsigned long>(trace.atUs[index] - origin),
                  trace.level[index]);
  }
  Serial.println();
}

// The BC548 is an inverting open-collector driver on the radio's RX/PT line.
// Invert UART TX only: idle then leaves the transistor OFF and PT released.
// Run synchronously with the audio task suspended so nothing else owns IO10.
void runProgrammingBridge(uint32_t idleTimeoutMs) {
  if (txTaskHandle != nullptr) vTaskSuspend(txTaskHandle);
  if (isTransmitting() ||
      (txCommandQueue != nullptr && uxQueueMessagesWaiting(txCommandQueue) != 0)) {
    if (txTaskHandle != nullptr) vTaskResume(txTaskHandle);
    Serial.println("CHIRP BUSY: wait for audio TX to finish.");
    return;
  }
  const bool wasStreaming = streamAudio;
  streamAudio = false;
  stopAdcDmaForBlockingWork();
  digitalWrite(PTT_PIN, PTT_INACTIVE_LEVEL);

  uart_config_t config = {};
  config.baud_rate = 9600;
  config.data_bits = UART_DATA_8_BITS;
  config.parity = UART_PARITY_DISABLE;
  config.stop_bits = UART_STOP_BITS_1;
  config.flow_ctrl = UART_HW_FLOWCTRL_DISABLE;
  config.source_clk = UART_SCLK_APB;
  esp_err_t result = uart_param_config(UART_NUM_1, &config);
  bool installed = false;
  if (result == ESP_OK) {
    result = uart_driver_install(UART_NUM_1, 1024, 0, 0, nullptr, 0);
    installed = result == ESP_OK;
  }
  if (result == ESP_OK)
    result = uart_set_line_inverse(UART_NUM_1, UART_SIGNAL_TXD_INV);
  if (result == ESP_OK)
    result = uart_set_pin(UART_NUM_1, PTT_PIN, 4, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE);

  if (result == ESP_OK) {
    programmingTxTrace.count = programmingRxTrace.count = 0;
    attachInterruptArg(PTT_PIN, onProgrammingEdge, &programmingTxTrace, CHANGE);
    attachInterruptArg(4, onProgrammingEdge, &programmingRxTrace, CHANGE);
    uart_flush_input(UART_NUM_1);
    uint32_t actualBaud = 0;
    uart_get_baudrate(UART_NUM_1, &actualBaud);
    Serial.printf("CHIRP READY baud=%lu rx=%d tx_idle=%d\n",
                  static_cast<unsigned long>(actualBaud), gpio_get_level(GPIO_NUM_4),
                  gpio_get_level(static_cast<gpio_num_t>(PTT_PIN)));
    Serial.flush();
    uint32_t lastActivity = millis();
    const uint32_t startedAt = lastActivity;
    uint8_t bytes[128];
    // No text diagnostics inside the binary stream. A disconnected host
    // releases the port after 15 seconds; a session lasts at most 2 minutes.
    while (millis() - lastActivity < idleTimeoutMs && millis() - startedAt < 120000) {
      size_t count = 0;
      while (Serial.available() && count < sizeof(bytes))
        bytes[count++] = static_cast<uint8_t>(Serial.read());
      if (count) {
        uart_write_bytes(UART_NUM_1, bytes, count);
        lastActivity = millis();
      }
      const int received = uart_read_bytes(UART_NUM_1, bytes, sizeof(bytes), 0);
      if (received > 0) {
        Serial.write(bytes, received);
        lastActivity = millis();
      }
      delay(1);
    }
    // BF-888 protocol exit command, never a memory write.
    uart_write_bytes(UART_NUM_1, "E", 1);
    uart_wait_tx_done(UART_NUM_1, pdMS_TO_TICKS(100));
    detachInterrupt(PTT_PIN);
    detachInterrupt(4);
    printProgrammingTrace("TX", programmingTxTrace);
    printProgrammingTrace("RX", programmingRxTrace);
  } else {
    Serial.printf("CHIRP ERROR: %s\n", esp_err_to_name(result));
  }
  if (installed) uart_driver_delete(UART_NUM_1);
  gpio_reset_pin(static_cast<gpio_num_t>(PTT_PIN));
  digitalWrite(PTT_PIN, PTT_INACTIVE_LEVEL);
  pinMode(PTT_PIN, OUTPUT);
  digitalWrite(PTT_PIN, PTT_INACTIVE_LEVEL);
  restartAdcDmaAfterBlockingWork();
  windowStats = AdcStats{};
  timingStats = TimingStats{};
  previousFrameStartedAtUs = 0;
  windowStartedAtMs = lastSerialReportAtMs = millis();
  // Discard audio TX packets received while the radio was being programmed.
  while (udp.parsePacket() > 0) {
    while (udp.available()) udp.read();
  }
  streamAudio = wasStreaming;
  if (txTaskHandle != nullptr) vTaskResume(txTaskHandle);
  Serial.println("CHIRP CLOSED; audio resumed; PTT OFF.");
}

void printHelp() {
  Serial.println();
  Serial.println("Commands:");
  Serial.println("  u - enable UDP audio stream");
  Serial.println("  s - stop UDP; keep ADC1/ADC-DMA diagnostics running");
  Serial.println("  c - remeasure quiet-input DC center");
  Serial.println("  p - PTT-only TX diagnostic (3 seconds)");
  Serial.println("  t - low-amplitude 1 kHz TX tone diagnostic");
  Serial.println("  b - 9600-baud radio programming bridge (15s idle timeout)");
  Serial.println("  q - programming bridge with 1s idle timeout for scanner retunes");
  Serial.println("  r - scanner RX-only profile; persistently inhibit all audio/PTT TX");
  Serial.println("  a - Hermes profile; re-enable audio/PTT TX after restoring radio memory");
  Serial.println("  h - show this help");
  Serial.println();
}

void handleSerialCommands() {
  while (Serial.available() > 0) {
    const char command = static_cast<char>(Serial.read());
    switch (command) {
      case 'b':
      case 'B':
        runProgrammingBridge(15000);
        break;
      case 'q':
        runProgrammingBridge(1000);
        break;
      case 'r':
        receiveOnlyProfile = true;
        profilePreferences.putBool("rx-only", true);
        requestTxAbort(TX_ABORT_STREAM_TIMEOUT);
        digitalWrite(PTT_PIN, PTT_INACTIVE_LEVEL);
        Serial.println("PROFILE SCANNER RX_ONLY=1");
        break;
      case 'a':
        receiveOnlyProfile = false;
        profilePreferences.putBool("rx-only", false);
        Serial.println("PROFILE HERMES RX_ONLY=0");
        break;
      case 'u':
      case 'U':
        if (WiFi.status() == WL_CONNECTED) {
          streamAudio = true;
          Serial.println("UDP audio stream enabled.");
        } else {
          Serial.println("Wi-Fi is not connected; check secrets.h and network coverage.");
        }
        break;
      case 's':
      case 'S':
        streamAudio = false;
        Serial.println("UDP audio stream disabled; ADC1/ADC-DMA diagnostics continue.");
        break;
      case 'c':
      case 'C':
        calibrateDcCenter();
        windowStats = AdcStats{};
        timingStats = TimingStats{};
        previousFrameStartedAtUs = 0;
        windowStartedAtMs = millis();
        lastSerialReportAtMs = windowStartedAtMs;
        break;
      case 'p':
      case 'P':
        queueTxCommand(TX_COMMAND_PTT_TEST);
        break;
      case 't':
      case 'T':
        queueTxCommand(TX_COMMAND_TONE_TEST);
        break;
      case 'h':
      case 'H':
      case '?':
        printHelp();
        break;
      case '\r':
      case '\n':
        break;
      default:
        Serial.print("Unknown command: ");
        Serial.println(command);
        printHelp();
        break;
    }
  }
}

void connectWifi() {
  Serial.println("Connecting to configured Wi-Fi network(s)...");
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);

  for (size_t networkIndex = 0;
       networkIndex < WIFI_NETWORK_COUNT && WiFi.status() != WL_CONNECTED;
       ++networkIndex) {
    WiFi.disconnect();
    delay(100);
    WiFi.begin(WIFI_NETWORKS[networkIndex].ssid, WIFI_NETWORKS[networkIndex].password);

    const uint32_t startedAt = millis();
    while (WiFi.status() != WL_CONNECTED &&
           millis() - startedAt < WIFI_CONNECT_TIMEOUT_PER_NETWORK_MS) {
      delay(250);
      Serial.print('.');
    }
  }
  Serial.println();

  wifiConnected = WiFi.status() == WL_CONNECTED;
  if (!wifiConnected) {
    Serial.println("Wi-Fi not connected. ADC diagnostics remain available over serial.");
    return;
  }

  Serial.print("Wi-Fi connected. ESP32 IP: ");
  Serial.println(WiFi.localIP());
  Serial.print("UDP PCM destination: ");
  Serial.print(HOST_PC_IP);
  Serial.print(':');
  Serial.println(UDP_AUDIO_PORT);
  if (!udp.begin(TX_UDP_PORT)) {
    Serial.println("TX UDP receiver failed to bind; TX is unavailable until reboot.");
    txSubsystemReady = false;
  } else {
    Serial.print("TX UDP listener: port ");
    Serial.println(TX_UDP_PORT);
  }
  streamAudio = START_AUDIO_STREAM;
}

void setup() {
  digitalWrite(PTT_PIN, PTT_INACTIVE_LEVEL);
  pinMode(PTT_PIN, OUTPUT);
  digitalWrite(PTT_PIN, PTT_INACTIVE_LEVEL);
  Serial.begin(115200);
  profilePreferences.begin("radio-profile", false);
  receiveOnlyProfile = profilePreferences.getBool("rx-only", false);
  delay(300);
  Serial.println();
  Serial.println("BF-777S -> ESP32-C3-MINI-1 RX/TX audio bridge");
  Serial.println("GPIO3 / ADC1_CH3 RX; GPIO1 PWM TX; GPIO10 PTT");
  Serial.println(receiveOnlyProfile ? "PROFILE SCANNER RX_ONLY=1" : "PROFILE HERMES RX_ONLY=0");

  initializeAdcDma();
  initializeTxSubsystem();
  Serial.println("ADC DMA capture: 20 kHz; PCM output: 16 kHz (4-of-5 decimation).");
  calibrateDcCenter();

  // Wi-Fi association can block for seconds. Do not let it overflow a running
  // ADC DMA ring buffer; restart capture after all startup blocking work ends.
  stopAdcDmaForBlockingWork();
  connectWifi();
  restartAdcDmaAfterBlockingWork();

  windowStats = AdcStats{};
  timingStats = TimingStats{};
  adcDmaOverruns = 0;
  shortDmaFrames = 0;
  adcDmaReadErrors = 0;
  previousFrameStartedAtUs = 0;
  windowStartedAtMs = millis();
  lastSerialReportAtMs = windowStartedAtMs;
  printHelp();
}

void loop() {
  handleSerialCommands();
  handleIncomingTxPackets();
  enforceTxSafety();
  collectAndMaybeStreamFrame();
  handleIncomingTxPackets();
  enforceTxSafety();
}
