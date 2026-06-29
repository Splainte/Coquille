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

import glob
import os
from typing import Any

from .models import Word

_cuda_dll_ready = False


def _ensure_cuda_dll_path() -> None:
    """Sur Windows, rend trouvables les DLL CUDA des paquets pip ``nvidia-*-cu12``.

    CTranslate2 charge ``cublas64_12.dll`` / cuDNN au runtime. Contrairement à
    Linux, Windows ne cherche pas dans ``site-packages`` → on enregistre les
    dossiers ``bin`` des paquets nvidia via ``os.add_dll_directory``. No-op hors
    Windows ou si les paquets ne sont pas installés (on retombe alors sur CPU).
    """
    global _cuda_dll_ready
    if _cuda_dll_ready or os.name != "nt":
        return
    _cuda_dll_ready = True
    try:
        import nvidia  # namespace des paquets nvidia-*-cu12
    except Exception:
        return
    for base in list(getattr(nvidia, "__path__", [])):
        for bindir in glob.glob(os.path.join(base, "*", "bin")):
            try:
                os.add_dll_directory(bindir)
            except Exception:  # pragma: no cover - dépend de l'environnement
                pass


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


# ---------------------------------------------------------------------------
# Couche transcription enfichable
#
# Moteurs disponibles dans le sélecteur du panneau. "auto" choisit selon le
# matériel : GPU Nvidia détecté → large-v3 ; sinon → small (tourne en CPU).
# ---------------------------------------------------------------------------

ENGINES: dict[str, dict[str, str]] = {
    "auto":     {"label": "Auto (selon le matériel)"},
    "large-v3": {"label": "Local — large-v3 (GPU)"},
    "small":    {"label": "Local léger — small (CPU/test)"},
    "voxtral":  {"label": "Voxtral (API Mistral)"},
}


def _has_cuda() -> bool:
    """Vrai si un GPU CUDA est exploitable (via CTranslate2, sans exiger torch)."""
    try:
        _ensure_cuda_dll_path()
        import ctranslate2  # type: ignore

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:  # pragma: no cover - dépend de l'environnement
        return False


def detect_hardware() -> tuple[str, str]:
    """Renvoie (device, compute_type) adaptés à la machine pour faster-whisper."""
    if _has_cuda():
        return "cuda", "float16"
    return "cpu", "int8"


def resolve_engine(engine: str | None) -> str:
    """Résout "auto" en un moteur concret selon le matériel détecté."""
    if not engine or engine == "auto":
        return "large-v3" if _has_cuda() else "small"
    if engine not in ENGINES:
        raise ValueError(f"Moteur de transcription inconnu : {engine}")
    return engine


def list_engines() -> dict:
    """Catalogue pour le panneau : moteurs + résolution auto + matériel détecté."""
    device, _ = detect_hardware()
    return {
        "engines": [{"id": k, "label": v["label"]} for k, v in ENGINES.items()],
        "auto_resolves_to": resolve_engine("auto"),
        "device": device,
    }


def _fw_segments_to_words(segments) -> list[Word]:
    """Convertit les segments faster-whisper (objets .words) en list[Word]."""
    words: list[Word] = []
    for seg in segments:
        for w in getattr(seg, "words", None) or []:
            text = (w.word or "").strip()
            if not text:
                continue
            start = float(w.start)
            end = float(w.end)
            prob = getattr(w, "probability", None)
            words.append(
                Word(
                    text=text,
                    start=start,
                    duration=max(0.0, end - start),
                    confidence=float(prob) if prob is not None else None,
                )
            )
    return words


def _faster_whisper_transcribe(
    audio_path: str, model_name: str, language: str, initial_prompt: str | None
) -> list[Word]:
    _ensure_cuda_dll_path()
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "faster-whisper n'est pas installé : pip install faster-whisper"
        ) from exc

    device, compute_type = detect_hardware()
    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    segments, _info = model.transcribe(
        audio_path,
        language=language,
        word_timestamps=True,
        initial_prompt=initial_prompt,
    )
    return _fw_segments_to_words(segments)


def _voxtral_json_to_words(data: dict) -> list[Word]:
    """Mappe la réponse Voxtral (timestamp_granularities=word) en list[Word]."""
    raw_words = data.get("words") or []
    if not raw_words:  # repli : certaines réponses logent les mots dans segments
        for seg in data.get("segments") or []:
            raw_words.extend(seg.get("words") or [])
    words: list[Word] = []
    for w in raw_words:
        text = (w.get("text") or w.get("word") or "").strip()
        if not text:
            continue
        start = float(w.get("start", 0.0))
        end = float(w.get("end", start))
        words.append(Word(text=text, start=start, duration=max(0.0, end - start)))
    return words


def _voxtral_transcribe(audio_path: str) -> list[Word]:
    if not config.MISTRAL_API_KEY:
        raise RuntimeError("MISTRAL_API_KEY manquante pour le moteur Voxtral.")
    import os as _os

    import httpx  # importé à la demande

    # timestamp_granularities=word est incompatible avec le paramètre `language`
    # (doc Mistral) → on ne passe pas la langue ici, Voxtral la détecte.
    with open(audio_path, "rb") as fh:
        files = {"file": (_os.path.basename(audio_path), fh, "audio/wav")}
        form = [
            ("model", config.MISTRAL_TRANSCRIBE_MODEL),
            ("timestamp_granularities", "word"),
        ]
        with httpx.Client(timeout=300) as client:
            resp = client.post(
                f"{config.MISTRAL_BASE_URL}/audio/transcriptions",
                headers={"Authorization": f"Bearer {config.MISTRAL_API_KEY}"},
                data=form,
                files=files,
            )
            resp.raise_for_status()
            return _voxtral_json_to_words(resp.json())


def transcribe_audio(
    audio_path: str,
    *,
    engine: str = "auto",
    language: str = "fr",
    initial_prompt: str | None = None,
) -> list[Word]:
    """Transcrit un fichier audio en mots horodatés via le moteur choisi.

    engine : "auto" (selon matériel), "large-v3", "small" ou "voxtral".
    `initial_prompt` (Whisper) biaise le modèle avec du jargon connu.
    """
    resolved = resolve_engine(engine)
    if resolved == "voxtral":
        return _voxtral_transcribe(audio_path)
    return _faster_whisper_transcribe(
        audio_path, resolved, language, initial_prompt
    )
