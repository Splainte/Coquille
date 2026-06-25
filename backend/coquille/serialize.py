"""(Dé)sérialisation JSON des modèles, sans dépendance externe."""

from __future__ import annotations

from .models import Cue, Word


def word_to_dict(w: Word) -> dict:
    return {
        "text": w.text,
        "start": round(w.start, 3),
        "duration": round(w.duration, 3),
        "confidence": w.confidence,
        "type": w.type,
        "is_eos": w.is_eos,
        "speaker": w.speaker,
        "tags": w.tags,
    }


def word_from_dict(d: dict) -> Word:
    return Word(
        text=d["text"],
        start=float(d.get("start", 0.0)),
        duration=float(d.get("duration", 0.0)),
        confidence=d.get("confidence"),
        type=d.get("type", "word"),
        is_eos=bool(d.get("is_eos", False)),
        speaker=d.get("speaker"),
        tags=list(d.get("tags", []) or []),
    )


def cue_to_dict(c: Cue) -> dict:
    return {
        "index": c.index,
        "start": round(c.start, 3),
        "end": round(c.end, 3),
        "lines": c.lines,
        "cps": round(c.cps, 1),
    }
