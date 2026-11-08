**Português (Brasil)** · [English](en/08-stt-and-tts.md)

# 8. Fala para texto (STT) e texto para fala (TTS)

No rádio, **a latência é a experiência**. O operador solta o PTT e espera
em silêncio, e cada segundo de STT é um segundo de silêncio no ar antes de
o agente sequer começar a pensar. Escolha os motores pensando nisso.

## STT: o que o Hermes usa

O caminho do Hermes ([capítulo 6](06-hermes-integration.md)) usa a
configuração de STT do próprio Hermes. O Hermes aceita vários provedores
(`stt.provider` no `config.yaml`): de nuvem, como xAI, Groq, OpenAI,
Mistral e DeepInfra, e `local` (faster-whisper). Nós usamos um provedor
**de nuvem** (xAI). Como o gate de RX corta cada fala exatamente no fim da
transmissão, o envio é curto e a transcrição volta rápido.

Dicas que valem qualquer que seja a escolha:

- Defina `stt.language` explicitamente. A detecção automática em poucos
  segundos de áudio de rádio com banda estreita erra mais.
- Uma dica de vocabulário (`stt.prompt`, onde o provedor aceitar) ajuda com
  indicativos, nomes e jargão.
- O áudio de rádio vai de 300 a 3000 Hz e tem ruído. Modelos grandes lidam
  melhor com ele do que os minúsculos; avalie a precisão junto com a latência.

## Whisper local no Hermes: opcional, com mais latência

**Recomendamos STT em nuvem para o agente de rádio**, priorizando baixa
latência. Whisper local é uma alternativa quando o áudio precisa ficar na
máquina, mas não é a nossa recomendação para uma conversa com respostas rápidas.

Se precisar dessa alternativa, use o provedor `local` do próprio Hermes
(`stt.provider` no `config.yaml`) e defina `stt.language` explicitamente.
O áudio continua chegando pelo mesmo loopback ALSA; não há worker ou
instalador de STT adicional neste caminho do rádio.

Meça a latência de ponta a ponta no hardware que vai usar. Modelos maiores
podem atrasar a resposta, principalmente na CPU; modelos menores trocam
precisão por velocidade. O processamento também disputa recursos com as
outras tarefas da máquina.

## TTS

O **Edge TTS**, padrão do Hermes, é gratuito, não precisa de chave e soa
bem no rádio. Com o [`apply_radio_speech.py`](06-hermes-integration.md) ele
também ganha pausas reais entre frases, que fazem muita diferença quando o
ouvinte não pode reler. Escolha uma voz em `tts.edge.voice`. Qualquer outro
provedor de TTS do Hermes também funciona; o rádio só vê áudio no loopback.

### Clonagem de voz fora do Hermes (`tx.py`)

O `tx.py` envia fala ao rádio sem o Hermes. É útil para testes, ou para
scripts que só precisam falar:

```bash
python3 -m baofeng_bridge.audio.tx --esp32-host <esp32-ip> --wav hello.mp3 --begin-repeats 3       # qualquer arquivo de áudio
python3 -m baofeng_bridge.audio.tx --esp32-host <esp32-ip> "Base no ar." --begin-repeats 3        # via Chatterbox
```

O modo texto chama um servidor [Chatterbox](https://github.com/resemble-ai/chatterbox)
local (`--chatterbox-url`, padrão `http://127.0.0.1:8000`, endpoint
`/v1/audio/speech/clone`) com a sua própria voz de referência
(`--voice voices/reference.mp3`; `voices/` é ignorado pelo git). Em Python:

```python
from baofeng_bridge.audio.tx import synthesize_and_transmit
synthesize_and_transmit("<esp32-ip>", "Base no ar.", begin_repeats=3)
```
