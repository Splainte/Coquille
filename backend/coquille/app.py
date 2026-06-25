"""API REST (FastAPI) appelée par le panneau UXP.

Lancer :  uvicorn coquille.app:app --host 127.0.0.1 --port 8765

Endpoints :
- GET  /presets                liste des médias disponibles
- POST /process                pipeline complet → SRT + cues
- POST /transcribe             (re)transcription WhisperX d'un fichier audio
"""

from __future__ import annotations

from typing import Any, Optional

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel
except ImportError as exc:  # pragma: no cover
    raise RuntimeError(
        "FastAPI/pydantic requis pour le serveur : pip install -r requirements.txt"
    ) from exc

from .config import list_presets, load_preset
from .pipeline import process
from .serialize import cue_to_dict, word_from_dict, word_to_dict
from .transcribe import load_premiere_transcript, transcribe_audio

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


class TranscribeRequest(BaseModel):
    audio_path: str
    language: str = "fr"
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

    try:
        result = process(
            req.media,
            whisper_words=whisper_words,
            premiere_words=premiere_words,
            offline=req.offline,
        )
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {
        "media": result.media,
        "track_style": load_preset(result.media).track_style,
        "srt": result.srt,
        "cues": [cue_to_dict(c) for c in result.cues],
        "words": [word_to_dict(w) for w in result.words],
    }


@app.post("/transcribe")
def post_transcribe(req: TranscribeRequest) -> dict:
    try:
        words = transcribe_audio(
            req.audio_path, language=req.language, initial_prompt=req.initial_prompt
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"words": [word_to_dict(w) for w in words]}
