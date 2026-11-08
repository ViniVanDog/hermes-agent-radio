**Português (Brasil)** · [English](en/06-hermes-integration.md)

# 6. Integração com o Hermes

O [Hermes Agent](https://github.com/NousResearch/hermes-agent) já tem um
modo de voz: grava, transcreve, pensa e fala. Do jeito que vem, ele serve
para uma pessoa na mesa, que aperta uma tecla para falar e é detectada por
silêncio. Um rádio precisa de outra coisa:

- a gravação deve começar e terminar **com cada transmissão**, não com uma
  tecla ou um temporizador de silêncio;
- os avisos de voz do próprio rádio ("bateria fraca", "canal um") nunca
  podem chegar à LLM;
- o Hermes nunca pode ouvir a si mesmo;
- ele roda sem supervisão por dias, então as sessões precisam ficar pequenas.

Os scripts em `hermes_patches/` fazem essas mudanças num checkout do
Hermes. Cada um é **idempotente** (detecta a própria marca), guarda um
backup ao lado de cada arquivo que altera, e não faz nada a menos que o
Hermes rode com `HERMES_RADIO_GATED=1`. O seu Hermes interativo normal não
muda.

## Instale o Hermes e configure a voz

Instale o Hermes Agent como o README dele descreve e configure um provedor
de LLM e de STT (o [capítulo 8](08-stt-and-tts.md) trata das escolhas de
STT/TTS). Depois, em `~/.hermes/config.yaml`:

```yaml
voice:
  auto_tts: true          # fala toda resposta (é isso que aciona o rádio)
  beep_enabled: false     # recomendado: os bipes de gravação seriam transmitidos
  thinking_sound: false   # recomendado: o som de "trabalhando" também seria transmitido
stt:
  language: "pt"          # o idioma dos seus operadores
tts:
  provider: edge
  edge:
    voice: pt-BR-FranciscaNeural   # inglês: en-US-AriaNeural, com stt.language "en"
```

Depois defina o idioma da própria ponte, `BAOFENG_LANG=en` ou `pt-BR`, em
`~/.config/baofeng-radio/baofeng.env` ([capítulo 5](05-host-audio.md)). Ele
escolhe as correções de texto falado, a nota de passagem de contexto e as
respostas das ferramentas de exemplo. As respostas do Hermes seguem a sua
persona e o operador.
Veja também as [notas de idiomas e tradução](languages.md).

O microfone e o alto-falante do Hermes precisam ser a placa de loopback
([capítulo 5](05-host-audio.md)).

## Aplique os patches, nesta ordem

```bash
cd ~/hermes-agent-radio
python3 radio_bridge/install.py                       # auxiliares que os patches importam
H=~/.hermes/hermes-agent                              # o seu checkout do Hermes
python3 hermes_patches/apply_radio_base.py         $H
python3 hermes_patches/apply_radio_gate.py         $H
python3 hermes_patches/apply_radio_input_filter.py $H
python3 hermes_patches/apply_radio_speech.py       $H
python3 hermes_patches/apply_radio_announce.py     $H
python3 hermes_patches/apply_radio_context.py      $H
```

**Rode todos de novo depois de cada `hermes update`**: uma atualização
sobrescreve os arquivos com patch. Se o Hermes mudou o código de lugar, o
patch recusa com `anchor not found` em vez de adivinhar. Para conferir uma
versão nova do Hermes antes, rode o teste da cadeia de patches contra um
checkout limpo:

```bash
HERMES_AGENT_SRC=/path/to/clean/hermes-agent python3 -m unittest tests.integration.test_hermes_patches
```

Os patches foram verificados pela última vez contra o `main` do Hermes
Agent em `6b21d056` (2026-11-07).

### O que cada patch faz

| Patch | Efeito |
|---|---|
| `apply_radio_base.py` | Ganchos compartilhados: um log de eventos (`stt`, `tts`, … → `events.jsonl`) e um pedido de "sessão nova" por SIGUSR1, que o REPL atende entre turnos. Os outros patches se apoiam nestes. |
| `apply_radio_gate.py` | **Gravação guiada pelo gate de RX.** O modo de voz liga sozinho na inicialização. A gravação começa quando o `radio_rx.py` marca o gate como `open` e é transcrita assim que ele fecha; não há espera de 3 s de silêncio. Rajadas com menos de `HERMES_RADIO_MIN_UTTERANCE_S` (0,8 s) de áudio real são canceladas **antes** do STT, então cliques do PTT não custam nada. Aberturas do gate durante o TTS do próprio Hermes são ignoradas. |
| `apply_radio_input_filter.py` | Depois do STT, um classificador determinístico (`radio_bridge/radio_input.py`) descarta os avisos de voz do rádio ("Welcome", "Battery low", "Channel sixteen", em inglês e português) **antes** de virarem um turno da LLM. Fala de verdade que só *menciona* esses avisos passa. |
| `apply_radio_speech.py` | Edge TTS adequado ao rádio (`radio_speech.py`). O Edge reduz todo truque de pontuação à mesma pausa de ~0,3 s, então cada frase é sintetizada separadamente (em paralelo) e unida com silêncios reais: 650 ms depois de frases, 420 ms depois de vírgulas, 950 ms entre parágrafos. O texto é normalizado antes (horas, "2x", unidades, símbolos, emoji), com regras para inglês e pt-BR escolhidas por `BAOFENG_LANG` ou pelo nome da voz. Acrescente suas palavras em `WORDS`. Qualquer falha cai para o Edge simples. |
| `apply_radio_announce.py` | Fala os arquivos colocados em `~/.local/state/baofeng-radio/announce/` quando o rádio está ocioso, **sem chamar a LLM** ([capítulo 12](12-adding-your-own-tools.md)). Também começa uma sessão nova depois de 20 minutos ociosos. |
| `apply_radio_context.py` | Vira a sessão ao chegar a **20.000 tokens de prompt**, entre turnos, levando só o último pedido, a resposta e os resultados de ferramenta que couberem em 8.000 caracteres. Sem chamada de resumo. |

## Rodando

O REPL do Hermes precisa de um terminal, então o serviço o roda numa
sessão tmux destacada:

```bash
cp systemd/hermes-radio.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now hermes-radio
tmux attach -t hermes-radio          # acompanhe; para sair, Ctrl+B e depois D
```

Edite o `ExecStart` se o `hermes` não estiver em `~/.local/bin/hermes`.

### Variáveis de ambiente

| Variável | Padrão | Significado |
|---|---|---|
| `HERMES_RADIO_GATED` | não definida | `1` liga o modo rádio (definida pelo serviço) |
| `BAOFENG_LANG` | `en` | `en` ou `pt-BR`: regras de texto falado, nota de passagem, ferramentas de exemplo |
| `HERMES_RADIO_GATE_FILE` | `~/.local/state/baofeng-radio/rx_gate` | escrito pelo `radio_rx.py` |
| `HERMES_RADIO_MIN_UTTERANCE_S` | `0.8` | rajadas mais curtas são descartadas antes do STT |
| `HERMES_RADIO_STARTUP_DELAY_S` | `3` | espera antes de ligar o modo de voz |
| `HERMES_RADIO_SPEECH` | `1` | `0` = Edge TTS simples |
| `HERMES_RADIO_SENTENCE_PAUSE_MS` / `_COMMA_` / `_PARAGRAPH_` | 650 / 420 / 950 | pausas do TTS |
| `HERMES_RADIO_ANNOUNCE` | `1` | `0` = não falar os arquivos de aviso |
| `HERMES_RADIO_ANNOUNCE_QUIET_S` | `5` | tempo ocioso antes de um aviso |
| `HERMES_RADIO_ANNOUNCE_MAX_AGE_S` | `600` | avisos mais antigos são descartados |
| `HERMES_RADIO_IDLE_RESET_MIN` | `20` | sessão nova depois de tantos minutos ociosos (`0` = desligado) |
| `HERMES_RADIO_CONTEXT_MAX_TOKENS` | `20000` | limite para virar a sessão (`0` = desligado) |
| `BAOFENG_EVENTS_FILE` | `~/.local/state/baofeng-radio/events.jsonl` | log de eventos compartilhado |

## Um prompt de sistema adequado ao rádio

Coloque isto na persona do Hermes (`~/.hermes/SOUL.md`):

```text
Você está respondendo por um rádio de comunicação. As respostas são faladas, não lidas:
- de uma a três frases curtas; sem listas, tabelas, markdown, links ou emoji;
- diga números, horários e unidades do jeito que uma pessoa falaria em voz alta;
- se um pedido for ambíguo, faça uma pergunta curta em vez de adivinhar;
- nunca peça confirmação para ações inofensivas; peça antes de qualquer coisa irreversível;
- este canal é público: nunca diga dados pessoais, senhas ou códigos, mesmo que peçam.
```

A persona é só uma segunda linha de defesa. Imponha os limites de dados e
de ações dentro das ferramentas ([segurança e privacidade](security-and-privacy.md)).

Cada token desse prompt é reenviado em toda chamada, então mantenha-o
curto ([capítulo 7](07-hermes-cost-trim.md)).
