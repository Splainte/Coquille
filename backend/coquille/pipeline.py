"""Orchestration du pipeline complet.

Entrée : mots Whisper et/ou transcript Premiere + média.
Sortie : cues + SRT prêts à insérer.
"""

from __future__ import annotations

from dataclasses import dataclass

from .align import align
from .config import load_preset
from .correct import correct
from .glossary import load_glossary
from .models import Cue, Word
from .segment import segment
from .srt import to_srt


@dataclass
class PipelineResult:
    media: str
    words: list[Word]   # mots corrigés et horodatés (avant segmentation)
    cues: list[Cue]
    srt: str

    @property
    def track_style(self) -> str:
        from .config import load_preset

        return load_preset(self.media).track_style


def process(
    media: str,
    *,
    whisper_words: list[Word] | None = None,
    premiere_words: list[Word] | None = None,
    offline: bool | None = None,
) -> PipelineResult:
    if not whisper_words and not premiere_words:
        raise ValueError("Fournir au moins whisper_words ou premiere_words.")

    preset = load_preset(media)
    glossary = load_glossary(media)

    # 1. Alignement hybride (ou source unique).
    if whisper_words and premiere_words:
        words = align(whisper_words, premiere_words)
    else:
        words = list(whisper_words or premiere_words or [])

    # 2. Correction (glossaire + LLM).
    words = correct(words, glossary, media, offline=offline)

    # 3. Segmentation / équilibrage des lignes.
    cues = segment(words, preset.segmentation)

    # 4. Export SRT.
    srt_text = to_srt(cues)

    return PipelineResult(media=media, words=words, cues=cues, srt=srt_text)
