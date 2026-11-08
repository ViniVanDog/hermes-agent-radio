[Português (Brasil)](../troubleshooting.md) · **English**

# Troubleshooting

| Symptom | Likely cause → fix |
|---|---|
| `tools/flash.py`: "Missing …/secrets.h" | `cp firmware/BaofengRx/secrets.example.h firmware/BaofengRx/secrets.h` and fill it in. |
| `tools/flash.py`: venv creation fails | `sudo apt install python3-venv`. |
| ESP32 never joins Wi-Fi | 2.4 GHz SSID only; check `secrets.h`; watch the serial log. |
| `bridge.py` receives nothing | `HOST_PC_IP` is wrong or `127.0.0.1`; firewall on UDP 7777; `START_AUDIO_STREAM=false` (type `u`). |
| RX audio choppy | Check `rx_pps` ≈ 50, `max_gap_ms`, `adc_dma_ovf = 0`. Move the ESP32 closer to the AP. |
| RX `clipping=HIGH/LOW` | Lower the radio volume, or raise the RX 33k. |
| RX very quiet | Raise the radio volume, or lower the RX 33k. Check the 1.65 V bias. |
| Hiss opens the gate | Use a CTCSS/DCS receive tone; raise `--gate-rms`. |
| Hermes never starts recording | `HERMES_RADIO_GATED=1` unset; `baofeng-rx` not running; gate file path differs; patches not applied after `hermes update`. |
| Hermes hears nothing but the gate opens | Hermes' input device is not `HermesLoopback,1,0` ([chapter 5](05-host-audio.md)). |
| Hermes answers "Battery low" etc. | `apply_radio_input_filter.py` not applied, or a new prompt phrase. Add it to `radio_bridge/radio_input.py` and re-run `apply_radio_input_filter.py` (it refreshes the copied classifier). |
| Patch says `anchor not found` | Hermes changed. Run `tests.integration.test_hermes_patches` against the new version and update the anchor. Do not force it. |
| Carrier but no audio on TX | TX capacitor polarity; shared ground; Hermes output not on the loopback. |
| No carrier on TX | NPN pinout; base resistor; serial `p` test; C3 left in receive-only mode (`a` re-enables, after a verified restore). |
| Beeps or a "thinking" sound get transmitted | `voice.beep_enabled: false`, `voice.thinking_sound: false`. |
| First word of replies cut off | Raise `PTT_LEAD_MS` (firmware) and `--speech-lead-ms` (`radio_tx_bridge.py`). |
| Replies stop mid-sentence | Serial shows `TX aborted: stream timeout`, so the sender stalled more than 2 s. Check VM load and Wi-Fi. |
| Programming: no ACK | Close serial monitors; seat the plugs fully; check the GPIO4 jumper; see the notes in [chapter 9](09-chirp-programming.md). |
| Radio stuck after programming | Send `E` through the bridge, then power-cycle the **radio**. |
| Scanner refuses to start | The Hermes profile is not saved, the radio differs from it, or a restore is pending. Restore first. |
| Dashboard "Clear context" fails | No running Hermes CLI found. It must run in its tmux session (`hermes-radio.service`). |
| Usage table empty | `radio_bridge/install.py` not run, or `~/.hermes/state.db` path differs (`HERMES_STATE_DB`). |
| The radio speaks the wrong language | `BAOFENG_LANG` in `baofeng.env` (`en` or `pt-BR`), `tts.edge.voice` and `stt.language` in Hermes' `config.yaml`, and the persona's language must agree. Restart `hermes-radio`. |
