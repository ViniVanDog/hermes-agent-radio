"""Deterministic rejection of standalone radio voice announcements before LLM.

This runs after STT. It does not prevent the STT request, inspect audio, or
discard human requests merely because they mention a radio announcement.
"""

import re
import unicodedata


def normalized_words(text):
    text = unicodedata.normalize('NFKD', text.casefold())
    text = ''.join(char for char in text if not unicodedata.combining(char))
    return ' '.join(re.findall(r'[^\W_]+', text, flags=re.UNICODE))


NUMBER = (r'(?:1[0-6]|[1-9]|one|two|three|four|five|six|seven|eight|nine|ten|'
          r'eleven|twelve|thirteen|fourteen|fifteen|sixteen|um|uma|dois|duas|'
          r'tres|quatro|cinco|seis|sete|oito|nove|dez|onze|doze|treze|'
          r'quatorze|catorze|quinze|dezesseis)')
ANNOUNCEMENTS = (
    ('battery_low', r'(?:battery(?: is)? low|low battery|low voltage|voltage low|'
                    r'please (?:recharge|charge|replace|change)(?: the)? battery|'
                    r'(?:recharge|charge)(?: the)? battery|'
                    r'bateria (?:baixa|fraca|descarregada)|'
                    r'(?:recarregue|carregue)(?: a)? bateria)'),
    ('startup', r'(?:(?:welcome|wellcome|bem vindo)(?: (?:channel |canal )?' + NUMBER + r')?|'
                r'power on|power off)'),
    ('channel', r'(?:channel|canal) ' + NUMBER),
    ('radio_setting', r'(?:scan(?:ning)? (?:on|off|begin|end)|'
                      r'vox (?:on|off|enabled|disabled)|high power|low power|'
                      r'channel mode|frequency mode|transmission time(?:d)? out|'
                      r'time out|timeout)'),
)
PATTERNS = [(reason, re.compile(pattern + r'(?= |$)'))
            for reason, pattern in ANNOUNCEMENTS]


def radio_prompt_reason(text):
    """Return a reason only if the ENTIRE transcript is known announcements.

    Mixed speech such as 'O rádio disse battery low, o que eu faço?' must reach
    Hermes unchanged. Bare numbers and confirmations are legitimate user input.
    """
    if not isinstance(text, str):
        return None
    remaining = normalized_words(text)
    if not remaining:
        return None
    reasons = []
    while remaining:
        matches = [(match.end(), reason) for reason, pattern in PATTERNS
                   if (match := pattern.match(remaining))]
        if not matches:
            return None
        end, reason = max(matches)
        reasons.append(reason)
        remaining = remaining[end:].strip()
    return '+'.join(dict.fromkeys(reasons))
