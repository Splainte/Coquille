"""Alignement hybride : texte Whisper ↔ timecodes Premiere.

Whisper(X) fournit un texte de meilleure qualité (et ses propres timestamps) ;
le transcript natif de Premiere fournit des timecodes dans le repère de la
séquence + un score de confiance par mot.

`align()` produit une liste de mots finale qui :
- garde le **texte de Whisper** (plus précis sur le jargon),
- ancre chaque mot sur le **timecode Premiere** quand les deux moteurs s'accordent,
- récupère la **confiance Premiere** comme signal d'erreur,
- marque comme peu fiables (confiance basse) les mots sur lesquels les deux
  moteurs divergent → ce sont les candidats prioritaires à la correction LLM.

Si aucun transcript Premiere n'est fourni, on retombe sur les mots Whisper tels quels.
"""

from __future__ import annotations

import difflib
import re
import unicodedata

from .models import Word


def _norm(text: str) -> str:
    """Clé de comparaison : minuscule, sans accents, sans ponctuation."""
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.lower()
    text = re.sub(r"[^\w]", "", text)
    return text


# Confiance attribuée à un mot Whisper non confirmé par Premiere (divergence).
DIVERGENCE_CONFIDENCE = 0.3


def align(whisper_words: list[Word], premiere_words: list[Word]) -> list[Word]:
    if not premiere_words:
        return list(whisper_words)
    if not whisper_words:
        return list(premiere_words)

    w_keys = [_norm(w.text) for w in whisper_words]
    p_keys = [_norm(p.text) for p in premiere_words]

    matcher = difflib.SequenceMatcher(a=w_keys, b=p_keys, autojunk=False)
    result: list[Word] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            # Accord : texte Whisper + timecode/confiance Premiere.
            for k in range(i2 - i1):
                w = whisper_words[i1 + k]
                p = premiere_words[j1 + k]
                result.append(
                    Word(
                        text=w.text,
                        start=p.start,
                        duration=p.duration,
                        confidence=p.confidence,
                        type=w.type,
                        is_eos=w.is_eos or p.is_eos,
                        speaker=p.speaker or w.speaker,
                        tags=list({*w.tags, *p.tags}),
                    )
                )
        else:
            # Divergence : on garde les mots Whisper (texte + timing propres),
            # confiance abaissée pour signaler le doute au LLM.
            for k in range(i1, i2):
                w = whisper_words[k]
                conf = (
                    min(w.confidence, DIVERGENCE_CONFIDENCE)
                    if w.confidence is not None
                    else DIVERGENCE_CONFIDENCE
                )
                result.append(
                    Word(
                        text=w.text,
                        start=w.start,
                        duration=w.duration,
                        confidence=conf,
                        type=w.type,
                        is_eos=w.is_eos,
                        speaker=w.speaker,
                        tags=w.tags,
                    )
                )
    return result
