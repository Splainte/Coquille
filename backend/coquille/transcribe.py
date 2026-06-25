"""Transcription et lecture du transcript Premiere.

Deux sources de mots horodatés :
- `load_premiere_transcript()` : parse le JSON du transcript natif de Premiere
  (timecodes au mot + scores de confiance), envoyé par le panneau UXP.
- `transcribe_audio()` : (re)transcription via WhisperX sur les machines maison.

WhisperX est importé à la demande : le reste du pipeline (alignement, correction,
segmentation) fonctionne sans GPU ni modèle installé, ce qui permet de tester et
de fournir un transcript Premiere seul si besoin.
"""

from __future__ import annotations

from typing import Any

from .models import Word


def _f(d: dict, *keys: str, default: float | None = 0.0) -> float | None:
    for k in keys:
        if k in d and d[k] is not None:
            return float(d[k])
    return default


def load_premiere_transcript(data: dict[str, Any]) -> list[Word]:
    """Parse tolérant du format transcript Premiere (cf. transcript_format_spec.json).

    On accepte plusieurs variantes de noms de champs pour rester robuste aux
    évolutions du format. Les temps sont en secondes.
    """
    speakers: dict[str, str] = {}
    for sp in data.get("speakers", []) or []:
        sid = sp.get("id") or sp.get("uuid") or sp.get("speakerId")
        if sid:
            speakers[str(sid)] = sp.get("name") or sp.get("label") or str(sid)

    segments = (
        data.get("transcript")
        or data.get("segments")
        or data.get("paragraphs")
        or []
    )

    words: list[Word] = []
    for seg in segments:
        speaker_id = seg.get("speaker") or seg.get("speakerId")
        speaker = speakers.get(str(speaker_id), speaker_id)
        for w in seg.get("words", []) or []:
            text = w.get("text") or w.get("word") or ""
            if not text:
                continue
            wtype = "punctuation" if w.get("type") == "punctuation" else "word"
            words.append(
                Word(
                    text=text,
                    start=_f(w, "start", "startTime", default=0.0) or 0.0,
                    duration=_f(w, "duration", "dur", "length", default=0.0) or 0.0,
                    confidence=_f(w, "confidence", "conf", "probability", default=None),
                    type=wtype,
                    is_eos=bool(
                        w.get("eos") or w.get("is_eos") or w.get("endOfSentence")
                    ),
                    speaker=speaker,
                    tags=list(w.get("tags", []) or []),
                )
            )
    return words


def whisperx_to_words(segments: list[dict]) -> list[Word]:
    """Convertit la sortie WhisperX (segments → words) en list[Word]."""
    words: list[Word] = []
    for seg in segments:
        for w in seg.get("words", []):
            text = w.get("word", "").strip()
            if not text:
                continue
            start = float(w.get("start", seg.get("start", 0.0)))
            end = float(w.get("end", start))
            words.append(
                Word(
                    text=text,
                    start=start,
                    duration=max(0.0, end - start),
                    confidence=float(w["score"]) if "score" in w else None,
                    speaker=seg.get("speaker"),
                )
            )
    return words


def transcribe_audio(
    audio_path: str,
    *,
    language: str = "fr",
    model_name: str = "large-v3",
    device: str = "cuda",
    compute_type: str = "float16",
    initial_prompt: str | None = None,
) -> list[Word]:
    """Transcrit un fichier audio avec WhisperX (timestamps au mot + alignement).

    `initial_prompt` permet de biaiser le modèle avec du jargon connu pour
    réduire les erreurs en amont (ex. liste de noms de produits).
    """
    try:
        import whisperx  # type: ignore
    except ImportError as exc:  # pragma: no cover - dépend de l'environnement GPU
        raise RuntimeError(
            "WhisperX n'est pas installé. Installez-le sur la machine de "
            "transcription (`pip install whisperx`) ou fournissez un transcript "
            "Premiere via load_premiere_transcript()."
        ) from exc

    asr_options = {"initial_prompt": initial_prompt} if initial_prompt else {}
    model = whisperx.load_model(
        model_name, device, compute_type=compute_type, asr_options=asr_options
    )
    audio = whisperx.load_audio(audio_path)
    result = model.transcribe(audio, language=language)

    align_model, metadata = whisperx.load_align_model(
        language_code=language, device=device
    )
    result = whisperx.align(
        result["segments"], align_model, metadata, audio, device,
        return_char_alignments=False,
    )
    return whisperx_to_words(result["segments"])
