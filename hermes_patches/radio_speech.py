#!/usr/bin/env python3
"""Radio-friendly speech for Hermes' Edge TTS (installed by apply_radio_speech.py).

Edge TTS collapses every punctuation trick to the same ~0.3 s sentence pause:
"..", "...", ". ." and line breaks all measured 308 ms, and custom SSML is
escaped. Longer pauses therefore come from real silence. Each sentence is
synthesized separately (in parallel), trimmed, and joined with ffmpeg using
fixed gaps. Before that, text the voice reads badly is rewritten: times,
"2x", dimensions, units, symbols and emoji. There are rule sets for English
and Brazilian Portuguese (WORDS, ABBREVIATIONS, SAY); add your own the same way.

Language: BAOFENG_LANG (en | pt-BR); when unset, the Edge voice's prefix
(pt-BR-FranciscaNeural -> pt), else English.

Tunable with environment variables (milliseconds):
  HERMES_RADIO_SENTENCE_PAUSE_MS   pause after . ! ? ; and dashes (default 650)
  HERMES_RADIO_COMMA_PAUSE_MS      pause after a comma (default 420; Edge's own is ~0.1 s)
  HERMES_RADIO_PARAGRAPH_PAUSE_MS  pause at line breaks and list items (default 950)
  HERMES_RADIO_SPEECH=0            disable; Hermes uses plain Edge TTS
"""

import asyncio
import os
import re
import shutil
import subprocess
import tempfile

SENTENCE_PAUSE_MS = int(os.environ.get("HERMES_RADIO_SENTENCE_PAUSE_MS", "650"))
PARAGRAPH_PAUSE_MS = int(os.environ.get("HERMES_RADIO_PARAGRAPH_PAUSE_MS", "950"))
COMMA_PAUSE_MS = int(os.environ.get("HERMES_RADIO_COMMA_PAUSE_MS", "420"))
TAIL_MS = 250            # after the last sentence, so the receiver does not clip it
MAX_SEGMENTS = 24        # beyond this, sentences are grouped to limit Edge requests
PARALLEL_REQUESTS = 4

def language(voice=None):
    """'pt' or 'en': BAOFENG_LANG wins, then the Edge voice name, then English."""
    tag = (os.environ.get("BAOFENG_LANG") or voice or "en").strip().lower()
    return "pt" if tag.startswith("pt") else "en"


# Whole-word rewrites applied before synthesis (pattern, spoken form). Add your
# own product names, acronyms and jargon to your language's list.
WORDS = {
    "pt": [
        (r"\bn[ºo°]\s*(?=\d)", "número "),
        (r"\bqtd\.?(?=\s)", "quantidade"),
        (r"\bunid\.?(?=\s)", "unidades"),
        (r"\bkm/h\b", "quilômetros por hora"),
        (r"\bMHz\b", "mega-hertz"),
    ],
    "en": [
        (r"\b[Nn]o\.\s*(?=\d)", "number "),
        (r"\bqty\.?(?=\s)", "quantity"),
        (r"\bpcs\.?(?=\s)", "pieces"),
        (r"\bkm/h\b", "kilometres per hour"),
        (r"\bmph\b", "miles per hour"),
        (r"\bMHz\b", "megahertz"),
    ],
}
# A "." after these does not end a sentence.
ABBREVIATIONS = {
    "pt": ("Sr", "Sra", "Dr", "Dra", "Av", "Ltda", "etc", "aprox", "obs", "ex", "tel"),
    "en": ("Mr", "Mrs", "Ms", "Dr", "St", "Ave", "Ltd", "etc", "approx", "vs", "No", "tel"),
}
# Words the rules below insert.
SAY = {
    "pt": {"by": "por", "cm": "centímetros", "unit": ("unidade", "unidades"), "of": "de",
           "and": "e", "plus": "mais"},
    "en": {"by": "by", "cm": "centimetres", "unit": ("unit", "units"), "of": "of",
           "and": "and", "plus": "plus"},
}
EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")


def _time(match, lang):
    hours, minutes = int(match.group(1)), int(match.group(2))
    if lang == "pt":
        if minutes == 0:
            return f"{hours} {'hora' if hours == 1 else 'horas'}"
        return f"{hours} e {minutes}"                                    # 14:30 -> 14 e 30
    if minutes == 0:
        return f"{hours} o'clock"                                        # 14:00 -> 14 o'clock
    return f"{hours} {'oh ' if minutes < 10 else ''}{minutes}"           # 9:05 -> 9 oh 5


def _quantity(match, lang):
    count = int(match.group(1))
    one, many = SAY[lang]["unit"]
    return f"{count} {one if count == 1 else many} {SAY[lang]['of']} "


def normalize(text, lang=None):
    """Rewrite what Edge voices read badly, keeping punctuation for pauses."""
    lang = lang or language()
    say = SAY[lang]
    text = EMOJI.sub("", text)
    text = re.sub(r"\b(\d{1,2}):(\d{2})\b", lambda m: _time(m, lang), text)
    text = re.sub(r"\b(\d+(?:[.,]\d+)?)\s*[x×]\s*(\d+(?:[.,]\d+)?)\s*cm\b",     # 24x14cm
                  rf"\1 {say['by']} \2 {say['cm']}", text)
    text = re.sub(r"\b(\d+(?:[.,]\d+)?)\s*cm\b", rf"\1 {say['cm']}", text)
    text = re.sub(r"\b(\d+)\s*[x×]\s+(?=[^\d\s])", lambda m: _quantity(m, lang), text)   # 2x Item
    for pattern, replacement in WORDS[lang]:
        text = re.sub(pattern, replacement, text)
    text = re.sub(r"\s*[—–]\s*", ". ", text)                                    # dashes: real pause
    text = re.sub(r"\b1\s+(\w+)\(s\)(?:\s+(\w+)\(s\))?",                                # 1 copy(s) sent(s)
                  lambda m: "1 " + m.group(1) + (" " + m.group(2) if m.group(2) else ""), text)
    text = re.sub(r"(?<=\w)\(s\)", "s", text)                                    # item(s) -> items
    text = re.sub(r"\s*\(([^()]*)\)", r", \1,", text)                          # (2 hours left)
    text = re.sub(r"\s*;\s*", ". ", text)
    text = text.replace("&", f" {say['and']} ").replace("+", f" {say['plus']} ")
    text = re.sub(r"[*_#`|<>]", " ", text)
    text = re.sub(r",\s*([.!?,])", r"\1", text)                                # ", ." -> "."
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r" *\n *", "\n", text).strip()


def _split_camel(name):
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name.replace("_", " ")).strip()


def segments(text, lang=None):
    """[(sentence, pause_after_ms)]; the last one gets TAIL_MS.

    Commas get their own real pause when that keeps the reply within MAX_SEGMENTS
    Edge requests; otherwise only sentences are split (and grouped if needed).
    """
    lang = lang or language()
    parts = _parts(text, commas=True, lang=lang)
    if len(parts) > MAX_SEGMENTS:
        parts = _parts(text, commas=False, lang=lang)
    if len(parts) > MAX_SEGMENTS:   # group into at most MAX_SEGMENTS requests
        size = -(-len(parts) // MAX_SEGMENTS)
        parts = [(" ".join(s for s, _ in parts[i:i + size]), parts[min(i + size, len(parts)) - 1][1])
                 for i in range(0, len(parts), size)]
    if parts:
        parts[-1] = (parts[-1][0], TAIL_MS)
    return parts


def _parts(text, commas, lang):
    parts = []
    for line in [l.strip() for l in normalize(text, lang).split("\n")]:
        if not line:
            if parts:
                parts[-1] = (parts[-1][0], PARAGRAPH_PAUSE_MS)
            continue
        line = re.sub(r"^[-•*\d]+[.)]?\s+", "", line) if re.match(r"^([-•*]|\d+[.)])\s", line) else line
        for sentence in _sentences(line, lang):
            clauses = re.split(r"(?<=,)\s+", sentence) if commas else [sentence]
            parts += [(c, COMMA_PAUSE_MS) for c in clauses[:-1]] + [(clauses[-1], SENTENCE_PAUSE_MS)]
        if parts:
            parts[-1] = (parts[-1][0], PARAGRAPH_PAUSE_MS)
    merged = []
    for s, p in parts:   # a clause without words ("1,") joins the previous one
        if re.search(r"\w", s) or not merged:
            merged.append((s, p))
        else:
            merged[-1] = (merged[-1][0] + " " + s, p)
    return [(s, p) for s, p in merged if re.search(r"\w", s)]


def _sentences(line, lang):
    out, start = [], 0
    for match in re.finditer(r"[.!?]+(?=\s+|$)", line):
        word = re.search(r"(\w+)$", line[start:match.start()])
        if match.group() == "." and word and word.group(1) in ABBREVIATIONS[lang]:
            continue
        out.append(line[start:match.end()].strip())
        start = match.end()
    if line[start:].strip():
        out.append(line[start:].strip())
    return out


async def synthesize_edge(edge_tts, text, output_path, voice, rate=None):
    """Edge TTS with fixed pauses between sentences. Raises on failure (caller falls back)."""
    parts = segments(text, language(voice))
    if not parts:
        raise ValueError("nothing to say")
    kwargs = {"voice": voice, **({"rate": rate} if rate else {})}
    ffmpeg = shutil.which("ffmpeg")
    if len(parts) == 1 or not ffmpeg:
        joined = " ".join(s for s, _ in parts)
        await edge_tts.Communicate(joined, **kwargs).save(output_path)
        return output_path

    with tempfile.TemporaryDirectory(prefix="radio_speech_") as tmp:
        paths = [os.path.join(tmp, f"{i:02d}.mp3") for i in range(len(parts))]
        limit = asyncio.Semaphore(PARALLEL_REQUESTS)

        async def one(sentence, path):
            async with limit:
                await edge_tts.Communicate(sentence, **kwargs).save(path)

        await asyncio.gather(*(one(s, p) for (s, _), p in zip(parts, paths)))
        inputs, chains = [], []
        for index, ((_, pause), path) in enumerate(zip(parts, paths)):
            inputs += ["-i", path]
            # Trim Edge's own lead (~160 ms) and tail (~840 ms), then add the exact pause.
            chains.append(f"[{index}:a]silenceremove=start_periods=1:start_threshold=-45dB,"
                          f"areverse,silenceremove=start_periods=1:start_threshold=-45dB,areverse,"
                          f"apad=pad_dur={pause / 1000:.3f}[a{index}]")
        graph = ";".join(chains) + ";" + "".join(f"[a{i}]" for i in range(len(parts))) + \
            f"concat=n={len(parts)}:v=0:a=1[out]"
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *inputs,
                        "-filter_complex", graph, "-map", "[out]", "-ac", "1", "-ar", "24000",
                        "-c:a", "libmp3lame", "-b:a", "64k", output_path],
                       check=True, timeout=60)
    return output_path


if __name__ == "__main__":
    import sys
    for sentence, pause in segments(" ".join(sys.argv[1:]) or sys.stdin.read()):
        print(f"[{pause:4d} ms] {sentence}")
