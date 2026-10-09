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
    lead_in: float = 0.08          # le ST apparaît ~2 images AVANT la parole
    #                                (convention des monteurs, mesurée sur les
    #                                shorts de référence : laisse l'œil se poser)
    max_hold_gap: float = 0.5      # écart < à ça entre deux cues = pas un vrai
    #                                silence → on tient le 1er ST jusqu'au 2nd
    #                                (coupe directe, sans image vide)


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


def _load_dotenv(path: Path | None = None) -> None:
    """Charge un fichier ``backend/.env`` (KEY=VALUE) dans os.environ.

    Loader minimal, sans dépendance : lignes ``KEY=VALUE``, ``#`` en commentaire,
    guillemets optionnels autour de la valeur. Une variable déjà définie dans
    l'environnement réel n'est PAS écrasée (l'env explicite gagne). Fichier
    NON versionné (cf. .gitignore) — c'est là que vit la clé OpenRouter.
    """
    path = path or ENV_PATH
    if not path.exists():
        return
    # Lecture tolérante à l'encodage : un .env sauvé en ANSI/cp1252 par Notepad
    # (accents dans les commentaires) ne doit JAMAIS faire planter le backend.
    # Les clés/valeurs utiles sont en ASCII ; on ignore les octets non décodables.
    try:
        text = path.read_text(encoding="utf-8-sig", errors="ignore")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().lstrip("﻿")  # retire un éventuel BOM résiduel
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


ENV_PATH = ROOT / ".env"
# Clé venant d'une vraie variable d'environnement (et non du .env) : elle
# l'emporte au démarrage sur ce que le panneau enregistre dans le .env.
KEY_FROM_SYSTEM_ENV = bool(os.environ.get("OPENROUTER_API_KEY"))

try:
    _load_dotenv()
except Exception:  # un .env malformé ne doit jamais empêcher le démarrage
    pass

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
# Modèle de correction. Choisi sur banc (oct. 2026, 7 shorts Frandroid/Survoltés,
# corrections par plages) : google/gemini-3.8-flash = 13 suggestions justes pour
# 1 fausse ; sonnet-4.6/5.5 et gpt-5.6 en proposent nettement plus de fausses.
DEFAULT_OPENROUTER_MODEL = "google/gemini-3.8-flash"
# L'ancien défaut était recopié en dur dans les .env (via .env.example) : on le
# migre, sinon les machines déjà installées resteraient bloquées dessus.
_LEGACY_DEFAULT_MODELS = {"anthropic/claude-sonnet-4.6"}
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "").strip()
if not OPENROUTER_MODEL or OPENROUTER_MODEL in _LEGACY_DEFAULT_MODELS:
    OPENROUTER_MODEL = DEFAULT_OPENROUTER_MODEL
# Effort de réflexion demandé au modèle ("" = défaut du modèle). Banc Gemini 3.8
# Flash (7 shorts) : défaut = 14 suggestions justes / 1 fausse, ~20 s par short ;
# "low" = 14 / 6 mais ~6 s. On privilégie la justesse (chaque fausse suggestion
# coûte un décochage au monteur) ; "low" reste possible via le .env.
OPENROUTER_REASONING_EFFORT = os.environ.get("OPENROUTER_REASONING_EFFORT", "").strip()
OPENROUTER_BASE_URL = os.environ.get(
    "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
)
# Si vrai, on n'appelle pas le réseau : la correction se fait uniquement via le
# glossaire (déterministe). Pratique pour les tests et le mode hors-ligne.
CORRECT_OFFLINE = os.environ.get("COQUILLE_CORRECT_OFFLINE", "") == "1"

# --- Transcription ---
# Recalage du timing des mots par alignement forcé (si torch/torchaudio sont
# installés, cf. requirements-align.txt). "0" pour le désactiver.
FORCED_ALIGN = os.environ.get("COQUILLE_FORCED_ALIGN", "1") != "0"
# Moteur par défaut quand le panneau n'en impose pas ("auto" = selon le matériel).
DEFAULT_ENGINE = os.environ.get("COQUILLE_ENGINE", "auto")
# Voxtral via l'API Mistral (moteur "voxtral").
MISTRAL_API_KEY = os.environ.get("MISTRAL_API_KEY", "")
MISTRAL_BASE_URL = os.environ.get("MISTRAL_BASE_URL", "https://api.mistral.ai/v1")
MISTRAL_TRANSCRIBE_MODEL = os.environ.get(
    "MISTRAL_TRANSCRIBE_MODEL", "voxtral-mini-latest"
)
