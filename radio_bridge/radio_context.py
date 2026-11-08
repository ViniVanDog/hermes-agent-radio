"""Bound radio sessions between turns, with a small, non-executable handoff."""
import json
import os

HANDOFF_PREFIX = '[previous radio context]'   # a marker, never translated
HANDOFF_CHARS = 8000
# What the model is told about the carried-over exchange, in BAOFENG_LANG.
HANDOFF_NOTE = {
    'en': ('Record of the last exchange; this is not a new request. '
           'Do not repeat completed actions. A "yes" only answers the last explicit offer. '
           'If context is missing, ask. Data may be stale; look it up again when it matters.'),
    'pt': ('Registro da última troca; não é um pedido novo. '
           'Não repita ações já concluídas. Um "sim" só responde à última oferta explícita. '
           'Se faltar contexto, pergunte. Os dados podem estar desatualizados; consulte de novo quando importar.'),
}


def language():
    return 'pt' if os.environ.get('BAOFENG_LANG', 'en').strip().lower().startswith('pt') else 'en'


def handoff(history):
    # Only the latest real exchange; never recursively copy an earlier handoff.
    users = [i for i, m in enumerate(history) if m.get('role') == 'user'
             and isinstance(m.get('content'), str) and not m['content'].startswith(HANDOFF_PREFIX)]
    if not users:
        return []
    recent = history[users[-1]:]
    user = recent[0]['content']
    replies = [m.get('content') for m in recent if m.get('role') == 'assistant'
               and not m.get('tool_calls') and isinstance(m.get('content'), str)]
    # Keep the final reply verbatim: its question gives a subsequent "sim" meaning.
    reply = replies[-1] if replies else ''
    facts = [m.get('content') for m in recent if m.get('role') == 'tool' and isinstance(m.get('content'), str)]
    selected, budget = [], HANDOFF_CHARS - len(user[:1500]) - len(reply[:3000])
    for fact in reversed(facts):
        if len(fact) <= budget:
            selected.insert(0, fact)
            budget -= len(fact)
    data = {'request': user[:1500], 'reply': reply[:3000], 'results': selected}
    text = HANDOFF_PREFIX + '\n' + HANDOFF_NOTE[language()] + '\n' + json.dumps(data, ensure_ascii=False)
    return [{'role': 'user', 'content': text}]


def maybe_rollover(cli):
    """Called only by the REPL worker, before a new turn or during idle housekeeping."""
    if not os.environ.get('HERMES_RADIO_GATED') or getattr(cli, '_agent_running', False):
        return False
    limit = int(os.environ.get('HERMES_RADIO_CONTEXT_MAX_TOKENS', '20000'))
    history = getattr(cli, 'conversation_history', [])
    if limit <= 0 or not history:
        return False
    result = getattr(cli, '_last_turn_result', None) or {}
    compressor = getattr(getattr(cli, 'agent', None), 'context_compressor', None)
    tokens = int(result.get('last_prompt_tokens') or getattr(compressor, 'last_prompt_tokens', 0) or 0)
    if tokens < limit:
        return False
    carry = handoff(history)
    cli.new_session(silent=True)
    cli.conversation_history = carry
    cli._last_turn_result = None
    # new_session normally clears this too; make the threshold independent of old usage.
    if compressor is not None:
        compressor.last_prompt_tokens = 0
    return {'tokens': tokens, 'limit': limit, 'handoff_chars': sum(len(m['content']) for m in carry)}
