**Português (Brasil)** · [English](en/05-host-audio.md)

# 5. Áudio no host

O modo de voz do Hermes espera um microfone e um alto-falante. Na máquina
do Hermes damos a ele dispositivos virtuais: uma placa de loopback ALSA. O
áudio recebido do rádio é tocado no loopback, então o Hermes o "ouve"; o
TTS do Hermes é tocado no loopback, e uma ponte o captura e envia ao rádio.

```text
ESP32 ──UDP 7777──► host PC: udp_relay.py ──UDP 7777──► Hermes VM: radio_rx.py
                                                         ├─ aplay → hw:HermesLoopback,0,0 ═► Hermes mic (…,1,0)
                                                         └─ writes ~/.local/state/baofeng-radio/rx_gate

Hermes speaker (hw:HermesLoopback,1,0) ═► …,0,0 → arecord → radio_tx_bridge.py ──UDP 7778──► ESP32 (PTT + mic)
```

O Linux é necessário na máquina ou VM que roda o Hermes e as pontes de
áudio, por causa do ALSA e do módulo `snd-aloop`. O PC físico que hospeda
a VM não precisa rodar Linux.

Se o ESP32 consegue alcançar essa máquina ou VM diretamente, pule o
`udp_relay.py` e aponte o `HOST_PC_IP` do ESP32 para o IP dela. Caso precise
de um relay no host, o `udp_relay.py` usa só a biblioteca padrão do Python;
a execução no Windows/macOS ainda não foi verificada. As units systemd
deste guia são para Linux.

## 0. Teste sem o Hermes

No PC indicado no `secrets.h`:

```bash
python3 -m baofeng_bridge.audio.bridge                      # toca o áudio de RX (precisa do aplay: sudo apt install alsa-utils)
python3 -m baofeng_bridge.audio.bridge --no-play --record received.wav
python3 -m baofeng_bridge.audio.bridge --no-play --utterance-dir utterances   # teste do gate: um WAV por transmissão
```

O `bridge.py` usa um buffer ALSA de 600 ms para absorver falhas do Wi-Fi e,
a cada 50 pacotes, registra `rx_pps` (meta 50), `pcm_hz` (meta 16000),
`dropped_window` e `max_gap_ms`.

### Os dois gates de RX

1. **O tom de squelch do rádio (CTCSS/DCS).** Programe o canal do rádio da
   base com um tom de recepção e configure o mesmo tom nos portáteis. Assim
   o tráfego alheio na frequência não abre o squelch. Isso é um filtro de
   ruído, **não segurança**: qualquer um pode usar o mesmo tom, e todos no
   alcance continuam ouvindo os dois lados ([segurança e privacidade](security-and-privacy.md)).
2. **Um gate de energia no PC.** Ele abre depois de 3 pacotes de 20 ms
   altos (`--gate-rms`, padrão 1200) e fecha depois de **3 s de silêncio**,
   para que as pausas dentro de uma transmissão fiquem numa só fala. Ele
   guarda 250 ms antes da abertura. Suba o `--gate-rms` se o chiado em
   repouso abrir o gate; baixe se quem fala baixo estiver sendo perdido.

O `radio_rx.py` usa o mesmo gate e o publica para o Hermes como arquivo
(`open|closed <seq> <loud_ms>`). É isso que faz o Hermes começar e parar
de gravar exatamente junto com cada transmissão ([capítulo 6](06-hermes-integration.md)).

## 1. PC host: relay para a VM

```bash
python3 -m baofeng_bridge.audio.udp_relay --target-host <vm-ip> --allow-source <esp32-ip>
```

Ele repassa só pacotes BF77 válidos, e só vindos do ESP32. Enquanto o
[scanner](10-scanner-dashboard.md) usa o rádio, ele para de repassar ao
Hermes, então o agente nunca ouve os canais varridos. Também para se o
scanner cair no meio de uma varredura.

## 2. Máquina do Hermes: a placa de loopback

Os scripts assumem uma placa `snd-aloop` com id `HermesLoopback`:

```bash
echo 'snd-aloop' | sudo tee /etc/modules-load.d/hermes-loopback.conf
echo 'options snd-aloop id=HermesLoopback pcm_substreams=1' | sudo tee /etc/modprobe.d/hermes-loopback.conf
sudo modprobe snd-aloop id=HermesLoopback pcm_substreams=1
aplay -l | grep -i loopback
```

Uma placa de loopback tem dois dispositivos. O que for tocado em `…,0,0`
pode ser capturado em `…,1,0`, e vice-versa. Nossas pontes usam o
dispositivo 0, e o Hermes precisa usar o dispositivo **1** tanto para o
microfone quanto para o alto-falante.

O Hermes grava e toca pelos dispositivos padrão do PortAudio, então torne
o dispositivo 1 o padrão **para o usuário que roda o Hermes**:

- **ALSA puro, sem PulseAudio/PipeWire** (o mais simples numa VM sem
  interface gráfica), `~/.asoundrc`:

  ```text
  pcm.!default {
      type asym
      playback.pcm "plughw:HermesLoopback,1,0"
      capture.pcm  "plughw:HermesLoopback,1,0"
  }
  ```

- **PipeWire/PulseAudio**: defina o sink e a source do dispositivo 1 do
  loopback como padrão (`pactl list short sinks|sources`, depois
  `pactl set-default-sink …` e `pactl set-default-source …`).

Confira o caminho de ponta a ponta antes de envolver o Hermes:

```bash
# terminal 1 (pontes paradas): captura o que o "Hermes" ouviria
arecord -D plughw:HermesLoopback,1,0 -f S16_LE -r 16000 -c 1 -d 5 /tmp/heard.wav
# terminal 2: toca no lado do "microfone"
aplay -D plughw:HermesLoopback,0,0 some.wav
```

## 3. Máquina do Hermes: pontes de RX e TX

```bash
python3 -m baofeng_bridge.audio.radio_rx                                   # UDP 7777 → loopback, escreve o arquivo do gate
python3 -m baofeng_bridge.audio.radio_tx_bridge --esp32-host <esp32-ip>    # loopback → UDP 7778, aciona o PTT
```

O `radio_tx_bridge.py` aplica um gate de energia no TTS capturado
(`--gate-rms 180`). Para cada resposta ele envia o `TX_BEGIN` três vezes,
espera a antecedência do PTT e então envia exatamente um pacote de 20 ms a
cada 20 ms. A pilha de rede do C3 guarda só uns seis datagramas, então
rajadas a fazem transbordar. Cada fala é limitada a 55 s, abaixo do limite
de 60 s do firmware.

Os dois scripts acrescentam eventos em `~/.local/state/baofeng-radio/events.jsonl`
(`rx_open`, `rx_closed`, `tts_audio_open`, `tts_audio_closed`, `tx_begin`,
`tx_playback`, `tx_complete`), que o [painel](10-scanner-dashboard.md) e o
[medidor de uso](11-usage-meter.md) leem.

## 4. Rodar tudo como serviços de usuário

```bash
mkdir -p ~/.config/baofeng-radio ~/.config/systemd/user
cp systemd/baofeng.env.example ~/.config/baofeng-radio/baofeng.env    # edite os endereços (e BAOFENG_LANG=pt-BR)
cp systemd/*.service ~/.config/systemd/user/
systemctl --user daemon-reload

# PC host
systemctl --user enable --now baofeng-relay
# VM do Hermes
systemctl --user enable --now baofeng-rx baofeng-tx
loginctl enable-linger "$USER"     # mantém os serviços de usuário rodando sem sessão aberta
```

As units assumem que o repositório está em `~/hermes-agent-radio`.
Edite `WorkingDirectory=` se não estiver.
