"""API REST (FastAPI) appelée par le panneau UXP.

Lancer :  uvicorn coquille.app:app --host 127.0.0.1 --port 8765

Endpoints :
- GET  /presets                liste des médias disponibles
- POST /process                pipeline complet → SRT + cues
- POST /transcribe             (re)transcription WhisperX d'un fichier audio
"""

from __future__ import annotations

from typing import Any, Optional

import os
import tempfile

try:
    from fastapi import FastAPI, File, Form, HTTPException, UploadFile
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel
except ImportError as exc:  # pragma: no cover
    raise RuntimeError(
        "FastAPI/pydantic requis pour le serveur : pip install -r requirements.txt"
    ) from exc

from . import config
from .config import list_presets, load_preset
from .pipeline import process
from .serialize import cue_to_dict, word_from_dict, word_to_dict
from .transcribe import (
    list_engines,
    load_premiere_transcript,
    transcribe_audio,
)

app = FastAPI(title="Coquille", version="0.1.0")

# Le panneau CEP s'exécute dans CEF depuis une origine "file://" (= "null").
# Outil local uniquement (127.0.0.1) → on autorise toutes les origines.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ProcessRequest(BaseModel):
    media: str
    whisper_words: Optional[list[dict]] = None
    premiere_words: Optional[list[dict]] = None
    premiere_transcript: Optional[dict[str, Any]] = None  # JSON brut Premiere
    offline: Optional[bool] = None
    max_chars_per_line: Optional[int] = None  # override piloté par le panneau


class TranscribeRequest(BaseModel):
    audio_path: str
    language: str = "fr"
    engine: str = "auto"
    initial_prompt: Optional[str] = None


@app.get("/presets")
def get_presets() -> dict:
    presets = []
    for media in list_presets():
        p = load_preset(media)
        presets.append({"media": p.media, "label": p.label, "track_style": p.track_style})
    return {"presets": presets}


@app.post("/process")
def post_process(req: ProcessRequest) -> dict:
    whisper_words = [word_from_dict(w) for w in req.whisper_words] if req.whisper_words else None

    premiere_words = None
    if req.premiere_words:
        premiere_words = [word_from_dict(w) for w in req.premiere_words]
    elif req.premiere_transcript:
        premiere_words = load_premiere_transcript(req.premiere_transcript)

    mcpl = req.max_chars_per_line
    if mcpl is not None and not (10 <= mcpl <= 80):
        raise HTTPException(
            status_code=400, detail="max_chars_per_line doit être entre 10 et 80."
        )

    try:
        result = process(
            req.media,
            whisper_words=whisper_words,
            premiere_words=premiere_words,
            offline=req.offline,
            max_chars_per_line=mcpl,
        )
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    corr = result.correction
    return {
        "media": result.media,
        "track_style": load_preset(result.media).track_style,
        "srt": result.srt,
        "cues": [cue_to_dict(c) for c in result.cues],
        "words": [word_to_dict(w) for w in result.words],
        "correction": {
            "used_llm": corr.used_llm,
            "model": corr.model,
            "n_corrections": corr.n_corrections,
            "error": corr.error,
            "warnings": corr.warnings,
            "items": [
                {
                    "original": it.original,
                    "corrected": it.corrected,
                    "start": round(it.start, 3),
                    "source": it.source,
                }
                for it in corr.items
            ],
        } if corr else None,
    }


@app.get("/engines")
def get_engines() -> dict:
    """Moteurs de transcription + résolution "auto" pour le matériel courant."""
    return list_engines()


@app.post("/transcribe")
def post_transcribe(req: TranscribeRequest) -> dict:
    """Transcrit un fichier déjà présent sur la machine du backend (chemin local)."""
    engine = req.engine or config.DEFAULT_ENGINE
    try:
        words = transcribe_audio(
            req.audio_path,
            engine=engine,
            language=req.language,
            initial_prompt=req.initial_prompt,
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"words": [word_to_dict(w) for w in words]}


@app.post("/transcribe-upload")
async def post_transcribe_upload(
    file: UploadFile = File(...),
    engine: str = Form("auto"),
    language: str = Form("fr"),
) -> dict:
    """Transcrit un WAV envoyé par le panneau (cas backend distant / autre machine)."""
    suffix = os.path.splitext(file.filename or "audio.wav")[1] or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    try:
        words = transcribe_audio(
            tmp_path, engine=engine or config.DEFAULT_ENGINE, language=language
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        os.unlink(tmp_path)
    return {"words": [word_to_dict(w) for w in words]}
