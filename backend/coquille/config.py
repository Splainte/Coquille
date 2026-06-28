"""Chargement des presets média et des paramètres globaux.

Un preset média (frandroid/numerama/clubic) regroupe :
- les règles de segmentation (max caractères/ligne, CPS, durées),
- des métadonnées de style (rappel du Track Style à appliquer côté Premiere),
- le nom du fichier glossaire associé.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

# Racine du dossier backend/ (contient presets/ et glossary/)
ROOT = Path(__file__).resolve().parent.parent
PRESETS_DIR = ROOT / "presets"
GLOSSARY_DIR = ROOT / "glossary"


@dataclass
class SegmentationRules:
    max_chars_per_line: int = 42
    max_lines: int = 2
    max_cps: float = 17.0          # vitesse de lecture max (caractères/seconde)
    min_duration: float = 1.0      # durée min d'un cue (s)
    max_duration: float = 6.0      # durée max d'un cue (s)
    min_gap: float = 0.08          # écart min entre deux cues (s)
    gap_split: float = 0.6         # une pause >= à ça force un nouveau cue (s)


@dataclass
class Preset:
    media: str                     # "frandroid" | "numerama" | "clubic"
    label: str
    glossary: str                  # nom de fichier glossaire (sans extension)
    track_style: str               # nom du Track Style Premiere à appliquer
    segmentation: SegmentationRules

    @classmethod
    def from_dict(cls, data: dict) -> "Preset":
        seg = SegmentationRules(**data.get("segmentation", {}))
        return cls(
            media=data["media"],
            label=data.get("label", data["media"].capitalize()),
            glossary=data.get("glossary", data["media"]),
            track_style=data.get("track_style", data["media"]),
            segmentation=seg,
        )


def load_preset(media: str, presets_dir: Path | None = None) -> Preset:
    presets_dir = presets_dir or PRESETS_DIR
    path = presets_dir / f"{media}.json"
    if not path.exists():
        raise FileNotFoundError(f"Preset introuvable : {path}")
    return Preset.from_dict(json.loads(path.read_text(encoding="utf-8")))


def list_presets(presets_dir: Path | None = None) -> list[str]:
    presets_dir = presets_dir or PRESETS_DIR
    return sorted(p.stem for p in presets_dir.glob("*.json"))


# --- Paramètres globaux (depuis l'environnement) ---

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "anthropic/claude-sonnet-4.6")
OPENROUTER_BASE_URL = os.environ.get(
    "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
)
# Si vrai, on n'appelle pas le réseau : la correction se fait uniquement via le
# glossaire (déterministe). Pratique pour les tests et le mode hors-ligne.
CORRECT_OFFLINE = os.environ.get("COQUILLE_CORRECT_OFFLINE", "") == "1"

# --- Transcription ---
# Moteur par défaut quand le panneau n'en impose pas ("auto" = selon le matériel).
DEFAULT_ENGINE = os.environ.get("COQUILLE_ENGINE", "auto")
# Voxtral via l'API Mistral (moteur "voxtral").
MISTRAL_API_KEY = os.environ.get("MISTRAL_API_KEY", "")
MISTRAL_BASE_URL = os.environ.get("MISTRAL_BASE_URL", "https://api.mistral.ai/v1")
MISTRAL_TRANSCRIBE_MODEL = os.environ.get(
    "MISTRAL_TRANSCRIBE_MODEL", "voxtral-mini-latest"
)
