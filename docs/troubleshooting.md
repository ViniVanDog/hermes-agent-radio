**Português (Brasil)** · [English](en/troubleshooting.md)

# Solução de problemas

| Sintoma | Causa provável → correção |
|---|---|
| `tools/flash.py`: "Missing …/secrets.h" | `cp firmware/BaofengRx/secrets.example.h firmware/BaofengRx/secrets.h` e preencha. |
| `tools/flash.py`: falha ao criar o venv | `sudo apt install python3-venv`. |
| O ESP32 nunca entra no Wi-Fi | Só SSID de 2,4 GHz; confira o `secrets.h`; acompanhe o log serial. |
| O `bridge.py` não recebe nada | `HOST_PC_IP` errado ou `127.0.0.1`; firewall na UDP 7777; `START_AUDIO_STREAM=false` (digite `u`). |
| Áudio de RX picotado | Confira `rx_pps` ≈ 50, `max_gap_ms`, `adc_dma_ovf = 0`. Aproxime o ESP32 do roteador. |
| RX com `clipping=HIGH/LOW` | Baixe o volume do rádio ou aumente o 33k do RX. |
| RX muito baixo | Suba o volume do rádio ou diminua o 33k do RX. Confira a polarização de 1,65 V. |
| Chiado abre o gate | Use um tom de recepção CTCSS/DCS; aumente o `--gate-rms`. |
| O Hermes nunca começa a gravar | `HERMES_RADIO_GATED=1` não definida; `baofeng-rx` parado; caminho do arquivo do gate diferente; patches não reaplicados depois de `hermes update`. |
| O Hermes não ouve nada, mas o gate abre | O dispositivo de entrada do Hermes não é `HermesLoopback,1,0` ([capítulo 5](05-host-audio.md)). |
| O Hermes responde a "Battery low" etc. | `apply_radio_input_filter.py` não aplicado, ou uma frase de aviso nova. Acrescente-a em `radio_bridge/radio_input.py` e rode de novo o `apply_radio_input_filter.py` (ele atualiza o classificador copiado). |
| Patch diz `anchor not found` | O Hermes mudou. Rode o `tests.integration.test_hermes_patches` contra a versão nova e atualize a âncora. Não force. |
| Portadora, mas sem áudio no TX | Polaridade do capacitor de TX; terra comum; saída do Hermes fora do loopback. |
| Sem portadora no TX | Pinagem do NPN; resistor de base; teste `p` na serial; C3 deixado em modo só recepção (`a` reativa, depois de uma restauração verificada). |
| Bipes ou som de "pensando" vão ao ar | `voice.beep_enabled: false`, `voice.thinking_sound: false`. |
| Primeira palavra das respostas cortada | Aumente `PTT_LEAD_MS` (firmware) e `--speech-lead-ms` (`radio_tx_bridge.py`). |
| Respostas param no meio da frase | A serial mostra `TX aborted: stream timeout`, ou seja, o transmissor travou por mais de 2 s. Confira a carga da VM e o Wi-Fi. |
| Programação: sem ACK | Feche os monitores seriais; encaixe bem os plugues; confira o jumper do GPIO4; veja as notas do [capítulo 9](09-chirp-programming.md). |
| Rádio travado depois da programação | Envie `E` pela ponte, depois desligue e ligue o **rádio**. |
| O scanner se recusa a iniciar | O perfil Hermes não foi salvo, o rádio difere dele, ou há uma restauração pendente. Restaure primeiro. |
| "Clear context" do painel falha | Nenhuma CLI do Hermes rodando foi encontrada. Ela precisa rodar na sessão tmux dela (`hermes-radio.service`). |
| Tabela de uso vazia | `radio_bridge/install.py` não foi rodado, ou o caminho de `~/.hermes/state.db` é outro (`HERMES_STATE_DB`). |
| O rádio fala no idioma errado | `BAOFENG_LANG` no `baofeng.env` (`en` ou `pt-BR`), `tts.edge.voice` e `stt.language` no `config.yaml` do Hermes, e o idioma da persona precisam concordar. Reinicie o `hermes-radio`. |
