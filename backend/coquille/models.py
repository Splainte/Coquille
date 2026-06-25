"""Modèles de données partagés par tout le pipeline.

Un `Word` est l'unité de base : il porte un texte ET un timing. Les timecodes sont
exprimés en **secondes** (flottant) depuis le début de l'audio fourni — c'est le même
repère que le format transcript de Premiere (start + duration en secondes).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal, Optional

WordType = Literal["word", "punctuation"]


@dataclass
class Word:
    """Un mot (ou un signe de ponctuation) horodaté."""

    text: str
    start: float          # secondes depuis le début de l'audio
    duration: float       # secondes
    confidence: Optional[float] = None   # 0..1 ; None si inconnu
    type: WordType = "word"
    is_eos: bool = False  # fin de phrase (end of sentence)
    speaker: Optional[str] = None
    tags: list[str] = field(default_factory=list)  # ex. "profanity", "filler"

    @property
    def end(self) -> float:
        return self.start + self.duration

    def with_text(self, text: str) -> "Word":
        """Copie en remplaçant le texte (utilisé après correction LLM)."""
        return replace(self, text=text)


@dataclass
class Cue:
    """Un sous-titre affiché : 1 à 2 lignes, sur un intervalle de temps."""

    index: int
    start: float
    end: float
    lines: list[str]

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def char_count(self) -> int:
        return sum(len(line) for line in self.lines)

    @property
    def cps(self) -> float:
        """Caractères par seconde (vitesse de lecture)."""
        return self.char_count / self.duration if self.duration > 0 else float("inf")
