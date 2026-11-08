**Português (Brasil)** · [English](en/11-usage-meter.md)

# 11. Medidor de uso

Não dá para enxugar o que não se mede. O `radio_bridge/usage.py` (só
biblioteca padrão) monta uma tabela por dia a partir de duas fontes:

- **A contabilidade do próprio Hermes**: `~/.hermes/state.db`, tabela
  `session_model_usage`, aberta **somente para leitura**. Ela dá as
  chamadas e os tokens da LLM (entrada sem cache, entrada com cache, saída,
  raciocínio) por tarefa do Hermes (conversation, background_review,
  title_generation, …).
- **O log de eventos do rádio**, `~/.local/state/baofeng-radio/events.jsonl`:
  `stt` (um por transmissão transcrita), `tts_audio_closed` (respostas e
  seus minutos de áudio), `announce` (falados sem a LLM) e `context_reset`.

```bash
python3 -m radio_bridge.usage 7          # últimos 7 dias
BAOFENG_TZ=America/Sao_Paulo python3 -m radio_bridge.usage 30
```

Exemplo de saída (números ilustrativos):

```text
day         LLM   uncached  cache  output  tok/call  STT  TTS(min)  announce  resets
2026-11-06    41       38k   212k      9k        6k   44   39(6)         12       3
            (conversation 39, title_generation 2)
```

O painel mostra a mesma tabela no cartão **API usage per day**
(`/api/usage`). Ele carrega o módulo do checkout, ou de
`~/.local/share/baofeng-radio/`, onde o `radio_bridge/install.py` o coloca.

Ressalva: o Hermes guarda totais **por sessão e tarefa**, não por chamada,
então o uso de uma sessão conta no dia da última chamada dela. Uma sessão
que atravessa a meia-noite cai inteira no segundo dia.

Variáveis: `HERMES_STATE_DB`, `BAOFENG_EVENTS_FILE`, `BAOFENG_TZ` (padrão:
o fuso do sistema).
