"""Recalage précis du timing des mots par alignement forcé (MMS, torchaudio).

Whisper « efface » les pauses : le mot qui suit un silence démarre pile à la fin
du précédent, si bien que le sous-titre surgit ~0,4 s AVANT que la personne
parle (mesuré sur 7 shorts : 100 % des mots après une pause ≥ 0,25 s). Un
aligneur forcé, lui, connaît déjà le texte et repère où chaque mot est prononcé
à ~20 ms près : c'est l'approche de WhisperX.

Optionnel : nécessite torch + torchaudio + num2words (requirements-align.txt).
Sans eux — ou en cas d'échec — on garde le timing Whisper tel quel.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import replace

from .models import Word

CHUNK_S = 30.0   # l'attention du modèle est quadratique : on aligne par tranches
MARGIN_S = 0.6   # marge audio autour d'une tranche (le timing Whisper est approximatif)
SR = 16000

# Unités écrites en abrégé mais prononcées en entier (sinon rien à aligner).
_SPOKEN_UNITS = {
    "km": "kilomètres", "kw": "kilowatts", "kwh": "kilowattheures", "ch": "chevaux",
    "mm": "millimètres", "cm": "centimètres", "kg": "kilos", "go": "gigas",
    "to": "téras", "mah": "milliampères", "hz": "hertz", "ghz": "gigahertz",
}

_bundle = None  # (model, tokenizer, aligner, device), chargé une seule fois


def available() -> bool:
    try:
        import num2words  # noqa: F401
        import torch  # noqa: F401
        import torchaudio  # noqa: F401
    except Exception:
        return False
    return True


def _load():
    global _bundle
    if _bundle is None:
        import torch
        from torchaudio.pipelines import MMS_FA

        device = "cuda" if torch.cuda.is_available() else "cpu"
        model = MMS_FA.get_model(with_star=False).to(device)
        _bundle = (model, MMS_FA.get_tokenizer(), MMS_FA.get_aligner(), device)
    return _bundle


def _spoken_tokens(text: str, language: str) -> list[str]:
    """Forme prononcée, en lettres a-z (alphabet du modèle) : « 810 km » →
    ["huit", "cent", "dix", "kilometres"]."""
    from num2words import num2words

    t = text.lower().replace("%", " pourcent ").replace("€", " euros ")
    t = re.sub(r"(\d)[\s ](\d{3})\b", r"\1\2", t)  # 66 500 → 66500

    def num(m: re.Match) -> str:
        s = m.group().replace(",", ".")
        try:
            val = float(s) if "." in s else int(s)
            return " " + num2words(val, lang=language) + " "
        except Exception:
            return " "

    t = re.sub(r"\d+(?:[.,]\d+)?", num, t)
    t = unicodedata.normalize("NFD", t)
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    toks: list[str] = []
    for tok in re.findall(r"[a-z]+", t):
        toks.extend(re.findall(r"[a-z]+", _SPOKEN_UNITS.get(tok, tok)))
    return toks


def _chunks(words: list[Word]) -> list[tuple[int, int]]:
    out, i = [], 0
    while i < len(words):
        j = i + 1
        while j < len(words) and words[j].end - words[i].start < CHUNK_S:
            j += 1
        out.append((i, j))
        i = j
    return out


def _align_chunk(words: list[Word], audio, language: str) -> list[tuple[float, float] | None]:
    import torch

    model, tokenizer, aligner, device = _load()
    t0 = max(0.0, words[0].start - MARGIN_S)
    t1 = min(len(audio) / SR, words[-1].end + MARGIN_S)
    clip = torch.from_numpy(audio[int(t0 * SR): int(t1 * SR)]).unsqueeze(0).to(device)
    flat, owner = [], []
    for k, w in enumerate(words):
        for tok in _spoken_tokens(w.text, language):
            flat.append(tok)
            owner.append(k)
    spans_out: list[tuple[float, float] | None] = [None] * len(words)
    if not flat:
        return spans_out
    with torch.inference_mode():
        emission, _ = model(clip)
        spans = aligner(emission[0].cpu(), tokenizer(flat))
    ratio = clip.size(1) / emission.size(1) / SR
    for sp, k in zip(spans, owner):
        s, e = t0 + sp[0].start * ratio, t0 + sp[-1].end * ratio
        prev = spans_out[k]
        spans_out[k] = (s, e) if prev is None else (prev[0], e)
    return spans_out


def refine(words: list[Word], audio_path: str, language: str = "fr") -> list[Word]:
    """Renvoie les mots avec un timing recalé ; les mots non alignables (sans
    lettres) restent entre leurs voisins. Lève en cas d'échec (l'appelant replie)."""
    if not words:
        return words
    from faster_whisper.audio import decode_audio

    audio = decode_audio(audio_path, sampling_rate=SR)
    times: list[tuple[float, float] | None] = []
    for i, j in _chunks(words):
        times.extend(_align_chunk(words[i:j], audio, language))

    out: list[Word] = []
    for w, t in zip(words, times):
        if t is None:
            out.append(w)
        else:
            out.append(replace(w, start=t[0], duration=max(0.02, t[1] - t[0])))
    # Ordre temporel garanti (un mot non aligné ne doit pas chevaucher ses voisins).
    for k in range(1, len(out)):
        if out[k].start < out[k - 1].end:
            if times[k] is None:
                out[k] = replace(out[k], start=out[k - 1].end,
                                 duration=max(0.02, out[k].end - out[k - 1].end))
            else:
                out[k - 1] = replace(out[k - 1], duration=max(0.02, out[k].start - out[k - 1].start))
    return out
