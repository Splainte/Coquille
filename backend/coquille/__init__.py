"""Coquille — sous-titrage augmenté par LLM pour Adobe Premiere.

Backend local : transcription (WhisperX/Voxtral) → alignement sur les timecodes
Premiere → correction LLM contextuelle (OpenRouter) → segmentation/équilibrage
des lignes → export SRT.
"""

__version__ = "0.3.0"
