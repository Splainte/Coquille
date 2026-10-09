"""API REST (FastAPI) appelée par le panneau CEP.

Lancer :  uvicorn coquille.app:app --host 127.0.0.1 --port 8765

Endpoints :
- GET  /presets                liste des médias disponibles
- POST /process                pipeline complet → SRT + cues
- POST /transcribe             (re)transcription WhisperX d'un fichier audio
- GET  /progress/{job}         avancement d'une transcription (job_id du panneau)
- GET  /llm                    état de la clé OpenRouter (?check=1 : la vérifie)
- POST /llm/key                enregistre la clé OpenRouter (backend/.env)
- DELETE /llm/key              retire la clé OpenRouter
"""

from __future__ import annotations

from typing import Any, Optional

import os
import tempfile
import threading
import time

try:
    from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel
except ImportError as exc:  # pragma: no cover
    raise RuntimeError(
        "FastAPI/pydantic requis pour le serveur : pip install -r requirements.txt"
    ) from exc

from dataclasses import replace

from . import config, llm_key
from .config import list_presets, load_preset
from .correct import Span, apply_spans
from .pipeline import process
from .segment import segment
from .serialize import cue_to_dict, word_from_dict, word_to_dict
from .srt import to_srt
from .transcribe import (
    list_engines,
    load_premiere_transcript,
    refine_word_timing,
    transcribe_audio,
)

app = FastAPI(title="Coquille", version="0.3.0")

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
    # "suggest" = corrections LLM proposées (non appliquées) ; "apply" = appliquées.
    llm_mode: str = "suggest"


class SegmentRequest(BaseModel):
    """Re-segmentation de mots déjà corrigés (après validation des suggestions)."""

    media: str
    words: list[dict]
    max_chars_per_line: Optional[int] = None
    # Suggestions acceptées à appliquer d'abord : [{"de", "a", "texte"}] (indices
    # dans `words`). Le backend répartit le timing des mots nouveaux.
    spans: Optional[list[dict]] = None


class KeyRequest(BaseModel):
    key: str


class TranscribeRequest(BaseModel):
    audio_path: str
    language: str = "fr"
    engine: str = "auto"
    initial_prompt: Optional[str] = None
    # Zone à transcrire dans le fichier (secondes) ; None = tout le fichier.
    start: Optional[float] = None
    end: Optional[float] = None
    # Identifiant choisi par le panneau pour suivre l'avancement (/progress).
    job_id: Optional[str] = None


# Avancement des transcriptions en cours, lu par le panneau pendant qu'il attend
# la réponse (les routes synchrones tournent dans un pool de threads).
_PROGRESS: dict[str, dict] = {}
_PROGRESS_LOCK = threading.Lock()


def _tracker(job_id: Optional[str]):
    if not job_id:
        return lambda phase, fraction: None
    t0 = time.monotonic()

    def report(phase: str, fraction: Optional[float]) -> None:
        with _PROGRESS_LOCK:
            prev = _PROGRESS.get(job_id) or {}
            started = prev.get("phase_started") if prev.get("phase") == phase else time.monotonic()
            _PROGRESS[job_id] = {
                "phase": phase, "fraction": fraction, "phase_started": started,
                "elapsed": round(time.monotonic() - t0, 1),
            }

    report("démarrage", None)
    return report


def _done(job_id: Optional[str]) -> None:
    if job_id:
        with _PROGRESS_LOCK:
            _PROGRESS.pop(job_id, None)


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
            apply_llm=(req.llm_mode != "suggest"),
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
            "suggestions": [
                {
                    "index": s.index,
                    "original": s.original,
                    "suggested": s.suggested,
                    "start": round(s.start, 3),
                    "end_index": s.index if s.end_index is None else s.end_index,
                }
                for s in corr.suggestions
            ],
        } if corr else None,
    }


@app.post("/segment")
def post_segment(req: SegmentRequest) -> dict:
    """Re-segmente une liste de mots déjà corrigés → cues + SRT (pas de LLM).

    Appelé par le panneau quand l'utilisateur applique/retire des suggestions :
    les mots sont déjà à jour côté panneau, on ne refait QUE la segmentation."""
    mcpl = req.max_chars_per_line
    if mcpl is not None and not (10 <= mcpl <= 80):
        raise HTTPException(
            status_code=400, detail="max_chars_per_line doit être entre 10 et 80."
        )
    try:
        preset = load_preset(req.media)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    rules = preset.segmentation
    if mcpl:
        rules = replace(rules, max_chars_per_line=mcpl)

    words = [word_from_dict(w) for w in req.words]
    if req.spans:
        words = apply_spans(
            words, [Span(int(s["de"]), int(s["a"]), str(s["texte"])) for s in req.spans]
        )
    cues = segment(words, rules)
    return {
        "track_style": preset.track_style,
        "srt": to_srt(cues),
        "cues": [cue_to_dict(c) for c in cues],
    }


def _panel_only(request: Request) -> None:
    """Écriture de la clé réservée au panneau (origine file:// → "null") : une
    page web ouverte dans un navigateur ne doit pas pouvoir la remplacer."""
    origin = request.headers.get("origin")
    if origin and origin != "null" and not origin.startswith("file://"):
        raise HTTPException(status_code=403, detail="Réservé au panneau Coquille.")


@app.get("/llm")
def get_llm(check: bool = False) -> dict:
    """État de la clé OpenRouter (jamais la clé en clair)."""
    return llm_key.status(check=check)


@app.post("/llm/key")
def post_llm_key(req: KeyRequest, request: Request) -> dict:
    """Vérifie puis enregistre la clé dans backend/.env (effet immédiat)."""
    _panel_only(request)
    key = llm_key.clean(req.key)
    if not llm_key.looks_valid(key):
        raise HTTPException(status_code=400, detail="Ce texte ne ressemble pas à une clé OpenRouter (sk-or-v1-…).")
    check = llm_key.verify(key)
    if check["state"] == "invalide":
        raise HTTPException(status_code=400, detail="Clé refusée par OpenRouter : vérifiez-la (non enregistrée).")
    # OpenRouter injoignable : on enregistre quand même (vérifiable plus tard).
    try:
        llm_key.set_key(key)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Écriture de backend/.env impossible : {exc}")
    out = llm_key.status()
    out.update(check)
    return out


@app.delete("/llm/key")
def delete_llm_key(request: Request) -> dict:
    _panel_only(request)
    try:
        llm_key.set_key("")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Écriture de backend/.env impossible : {exc}")
    return llm_key.status()


@app.get("/engines")
def get_engines() -> dict:
    """Moteurs de transcription + résolution "auto" pour le matériel courant."""
    return list_engines()


@app.get("/progress/{job_id}")
def get_progress(job_id: str) -> dict:
    """Avancement : phase ("démarrage" | "modèle" | "transcription" | "alignement"),
    fraction de la phase (0-1, null si inconnue), secondes écoulées et estimation
    du temps restant de la phase."""
    with _PROGRESS_LOCK:
        p = dict(_PROGRESS.get(job_id) or {"phase": "inconnu", "fraction": None, "elapsed": 0})
    f, started = p.pop("fraction"), p.pop("phase_started", None)
    p["fraction"] = f
    p["remaining"] = None
    if f and f > 0.05 and started:
        spent = time.monotonic() - started
        p["remaining"] = round(spent * (1 - f) / f, 1)
    return p


@app.post("/transcribe")
def post_transcribe(req: TranscribeRequest) -> dict:
    """Transcrit un fichier déjà présent sur la machine du backend (chemin local)."""
    engine = req.engine or config.DEFAULT_ENGINE
    report = _tracker(req.job_id)
    try:
        words = transcribe_audio(
            req.audio_path,
            engine=engine,
            language=req.language,
            initial_prompt=req.initial_prompt,
            refine_timing=False,
            start=req.start,
            end=req.end,
            progress=report,
        )
        words, timing = refine_word_timing(words, req.audio_path, req.language, report)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        _done(req.job_id)
    return {"words": [word_to_dict(w) for w in words], "timing": timing}


@app.post("/transcribe-upload")
async def post_transcribe_upload(
    file: UploadFile = File(...),
    engine: str = Form("auto"),
    language: str = Form("fr"),
    start: Optional[float] = Form(None),
    end: Optional[float] = Form(None),
    job_id: Optional[str] = Form(None),
) -> dict:
    """Transcrit un WAV envoyé par le panneau (cas backend distant / autre machine)."""
    suffix = os.path.splitext(file.filename or "audio.wav")[1] or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    report = _tracker(job_id)
    try:
        words = transcribe_audio(
            tmp_path, engine=engine or config.DEFAULT_ENGINE, language=language,
            refine_timing=False, start=start, end=end, progress=report,
        )
        words, timing = refine_word_timing(words, tmp_path, language, report)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        os.unlink(tmp_path)
        _done(job_id)
    return {"words": [word_to_dict(w) for w in words], "timing": timing}
