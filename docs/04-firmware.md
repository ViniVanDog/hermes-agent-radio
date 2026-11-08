**Português (Brasil)** · [English](en/04-firmware.md)

# 4. Firmware

Os dois firmwares são C++ Arduino nativo compilado com PlatformIO, para
ter temporização confiável de ADC e Wi-Fi. Python roda só no PC.

| Alvo | Código | Compilar |
|---|---|---|
| ESP32-C3-MINI-1 | `firmware/BaofengRxC3/` | `python3 tools/flash.py --target c3 …` |
| ESP32 clássico (DevKitC) | `firmware/BaofengRx/` | `python3 tools/flash.py …` |

## 1. Wi-Fi e o endereço do PC

```bash
cp firmware/BaofengRx/secrets.example.h firmware/BaofengRx/secrets.h
```

Edite o `secrets.h` (ele é ignorado pelo git; os dois firmwares o incluem):

- `WIFI_NETWORKS`: um ou mais pares SSID/senha, tentados em ordem.
- `HOST_PC_IP`: o endereço na rede local do PC que recebe o áudio de RX.
  Nunca é `127.0.0.1`, porque no ESP32 esse endereço é o próprio ESP32.
- `START_AUDIO_STREAM`: `false` deixa o UDP desligado até você digitar `u`,
  útil para conferir os níveis do ADC antes.

## 2. Compilar, gravar, monitorar

```bash
python3 tools/flash.py --target c3 --port /dev/ttyUSB0 --monitor
```

Na primeira execução o `tools/flash.py` cria um `.build-venv` privado com o
PlatformIO (ele nunca mexe no Python do sistema), baixa o toolchain,
compila, grava e abre um monitor a 115200 baud. Variações úteis:

```bash
python3 tools/flash.py --build-only            # só compila
python3 tools/flash.py --port COM3             # Windows
sudo apt install python3-venv            # se a criação do venv falhar no Debian/Ubuntu
```

## 3. Comandos seriais

| Tecla | Ambos | Significado |
|---|---|---|
| `u` | ✓ | inicia o envio de áudio por UDP |
| `s` | ✓ | para o UDP; as estatísticas do ADC continuam |
| `c` | ✓ | mede de novo o centro DC da entrada em silêncio (1 s, deixe o rádio quieto) |
| `p` | ✓ | **teste só de PTT**: transmite por 3 s, sem áudio |
| `t` | ✓ | **teste de tom**: PTT, 1,5 s de antecedência, 1 s de tom de 1 kHz em nível baixo, 150 ms de cauda, libera |
| `h` | ✓ | ajuda |
| `b` | C3 | ponte de programação do rádio, 15 s de tempo ocioso ([capítulo 9](09-chirp-programming.md)) |
| `q` | C3 | ponte de programação, 1 s de tempo ocioso (resintonias do scanner) |
| `r` | C3 | perfil do scanner: inibe todo TX de forma persistente (sobrevive a reset, gravado na NVS) |
| `a` | C3 | reativa o TX depois que a memória do rádio foi restaurada |

## 4. Lendo o diagnóstico de RX

```text
RX 100 ms: min=... max=... avg=... dc=... p-p=... n=... clipping=none udp=on ...
```

- Canal quieto: `min`, `max` e `avg` ficam perto de `dc`, e `p-p` é pequeno.
- Alguém falando: `min` cai, `max` sobe e `p-p` fica bem maior.
- `clipping=LOW/HIGH` significa que amostras chegaram a menos de 64
  contagens de 0 ou de 4095. A fala deve mostrar `clipping=none`. Ajuste o
  **33k** do RX ([capítulo 3](03-wiring.md)), não os resistores de polarização.

A temporização vem do **DMA** do ADC, não de um laço de `analogRead()`. A
taxa mínima de DMA do driver é 20 kHz, então o firmware captura a 20 kHz e
mantém 4 de cada 5 amostras, dando exatamente 16.000 amostras/s. Com o UDP
ligado, as linhas longas de estatística saem uma vez por segundo em vez de
dez. Metas de saúde:

```text
frame_start_hz         ≈ 50 (20 ms frames)
frame_period_us_avg    ≈ 20000
adc_dma_ovf, short_frames, adc_dma_errors   0
```

## 5. Formato do pacote de RX (ESP32 → PC, UDP 7777)

Um datagrama de 648 bytes a cada 20 ms:

```text
uint32 magic     0x37374642  ("BF77", little-endian)
uint16 sequence  wraps at 65535
uint16 count     320
int16  pcm[320]  16 kHz mono, signed little-endian
```

## 6. Formato do pacote de TX (PC → ESP32, UDP 7778)

Todo datagrama começa com um cabeçalho little-endian de 10 bytes:

```text
uint32 magic          0x58544642  ("BFTX")
uint8  type           1 = TX_BEGIN, 2 = TX_AUDIO, 3 = TX_END
uint8  reserved       0
uint16 sequence       increments every packet
uint16 payload_bytes  0 for BEGIN/END, 640 for AUDIO (320 signed 16-bit samples)
```

O transmissor envia `TX_BEGIN`, espera **1,5 s** (a antecedência de PTT do
firmware, para o rádio já estar transmitindo quando a fala chegar), envia
o AUDIO em ritmo de tempo real e depois manda `TX_END`. O firmware acumula
120 ms antes de tocar e enfileira até 480 ms. Ele registra inícios,
profundidade do buffer, saltos de sequência, falta de dados e motivos de
aborto, mas nada por amostra. No C3, envie o `TX_BEGIN` três vezes com o
mesmo número de sequência (`python3 -m baofeng_bridge.audio.tx --begin-repeats 3`; o
`radio_tx_bridge.py` já faz isso por padrão), para que um pacote UDP
perdido não derrube a resposta inteira.

Durante a transmissão, o firmware continua esvaziando o DMA do RX, mas
**não repassa o áudio de RX**, então o agente nunca ouve a si mesmo.

### Proteções (o PTT sempre é liberado)

- O PTT é desligado no boot, antes de Wi-Fi ou áudio começarem.
- Liberado em: perda do Wi-Fi, pacote malformado no meio do envio, falha de
  DMA/timer, estouro da fila, **2 s sem áudio** (`TX_TIMEOUT_MS`) ou **60 s**
  no total (`TX_MAX_TRANSMIT_MS`).
- Estados: `IDLE → TX_STARTING → TRANSMITTING → TX_ENDING`.

As constantes principais ficam juntas no topo de cada `.ino`:

```text
PCM_SAMPLE_RATE_HZ = 16000    PTT_LEAD_MS = 1500    PTT_TAIL_MS = 150
TX_TIMEOUT_MS = 2000          TX_MAX_TRANSMIT_MS = 60000
classic: TX_DAC_AMPLITUDE = 128    C3: TX_PWM_AMPLITUDE = 128, TX_PWM_CARRIER_HZ = 156250
```

## 7. Primeiro teste de TX

Coloque um segundo rádio no mesmo canal, abra o monitor e então:

1. `p`: o segundo rádio deve mostrar portadora por 3 s (em silêncio).
2. `t`: você deve ouvir um tom baixo de 1 kHz.
3. Só depois envie áudio de verdade:

```bash
python3 -m baofeng_bridge.audio.tx --esp32-host <esp32-ip> --wav some-speech.mp3 --begin-repeats 3
```

O `tx.py` decodifica com o FFmpeg do `.build-venv` (`.build-venv/bin/pip install imageio-ffmpeg`
se esse venv foi recriado). Ele nunca toca áudio no PC.

### Problemas no TX

| Sintoma | Confira |
|---|---|
| Portadora, sem tom | Pino de TX → 33k → **polaridade do capacitor** → M+; terra comum |
| Sem portadora | Pinagem do NPN, resistor de base de 2,2k, o `p` registra as duas transições do PTT |
| Muito baixo | Primeiro as ligações do 680R e do capacitor; a constante de amplitude já está na escala cheia de 8 bits |
| Distorcido / desvio excessivo | Baixe a constante de amplitude (ex.: 128 → 96 → 64), uma gravação por vez |
| Primeiras ou últimas palavras cortadas | Aumente `PTT_LEAD_MS` ou `PTT_TAIL_MS` |
| TX para antes da hora | A serial mostra `TX aborted`; `stream timeout` quer dizer que o transmissor travou por > 2 s |
