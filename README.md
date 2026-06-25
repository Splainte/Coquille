# Coquille

Extension Adobe Premiere Pro de **sous-titrage augmenté par LLM**, pour Frandroid,
Numerama et Clubic. Elle corrige les erreurs de transcription sur le jargon tech
(noms de produits, anglicismes) tout en réutilisant la précision des timecodes,
puis insère des sous-titres stylés par média avec un découpage de lignes équilibré.

Le nom « Coquille » = *typo* en français. 🙂

## Architecture

```
Séquence Premiere
   │  (panneau CEP) rendu audio WAV (exportAsMediaDirect)
   ▼
Backend local (FastAPI, machines maison)
   ├─ Whisper(X) / Voxtral   → texte précis + timestamps au mot
   ├─ alignement hybride      → texte Whisper ancré sur les timecodes Premiere
   ├─ correction LLM          → OpenRouter + glossaire par média (timing-safe)
   ├─ segmentation/lignes     → CPS, max car/ligne, ≤2 lignes, équilibrage
   └─ export SRT
   ▼
Panneau CEP
   ├─ révision humaine (cues éditables, faible confiance surlignée)
   └─ importFiles(.srt) → createCaptionTrack() → Track Style de marque (1 clic)
```

> **Runtime : CEP** (et non UXP). L'insertion auto de piste caption
> (`createCaptionTrack`) et le rendu audio (`exportAsMediaDirect`) n'existent qu'en
> ExtendScript ; la référence UXP 2026 ne les expose pas. Choix assumé pour un outil
> interne (Premiere épinglé) ; le backend étant agnostique du runtime, un re-port UXP
> futur ne touchera que le panneau. Détails : `cep-panel/README.md`.

Décisions de conception et état de l'API Premiere : voir
`~/.claude/plans/je-souhaite-cr-er-une-ticklish-cherny.md`.

## Composants

| Dossier        | Rôle |
|----------------|------|
| `backend/`     | Pipeline Python : transcription, alignement, correction, segmentation, SRT, API REST. |
| `cep-panel/`   | **Panneau CEP** (UI + ExtendScript : audio, insertion). Runtime actif. |
| `uxp-panel/`   | Squelette UXP **archivé** — conservé pour un re-port futur (cf. son README). |
| `legacy-jsx/`  | Brouillon ExtendScript initial (repris/élargi dans `cep-panel/jsx/`). |
| `backend/presets/`  | 3 presets média (règles de segmentation + rappel du Track Style). |
| `backend/glossary/` | Glossaires éditables (base + par média) — cœur de la correction. |

## Démarrage rapide (backend)

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # serveur + httpx
# Transcription GPU (machine dédiée) :  pip install whisperx

export OPENROUTER_API_KEY=sk-or-...       # correction LLM (sinon mode hors-ligne)
export OPENROUTER_MODEL=anthropic/claude-sonnet-4.6

uvicorn coquille.app:app --host 127.0.0.1 --port 8765
```

Démo hors-ligne (sans GPU ni clé) et tests :

```bash
cd backend
python3 demo.py frandroid                 # montre alignement + correction + SRT
python3 -m unittest discover -s tests -v  # 19 tests
```

## État d'avancement

- ✅ **Backend** : pipeline complet, testé (alignement, correction glossaire,
  segmentation/équilibrage, SRT). Correction LLM OpenRouter câblée (mode hors-ligne
  déterministe pour les tests).
- 🟡 **Panneau CEP** : scaffold complet (UI + `jsx/coquille.jsx` + ponts backend).
  Spike à lancer dans Premiere via le bouton **🔍 Diagnostic** (sonde `createCaptionTrack`,
  `exportAsMediaDirect`, backend, séquence active). 2 des 3 points reposent sur des API
  ExtendScript documentées ; reste à confirmer sur la version cible + configurer le
  preset audio `.epr`. La lecture du transcript natif n'est pas fiable en ExtendScript →
  fonctionnement Whisper-seul pour l'instant.
- ⚪ **Panneau UXP** : archivé (pas de `createCaptionTrack` en UXP, cf. `uxp-panel/README.md`).
- 🟡 **Track Styles** : à créer une fois dans Premiere (noms `Coquille — Frandroid/
  Numerama/Clubic`), valeurs de référence dans `backend/presets/*.json`.

## Limites connues

- L'API Premiere ne permet pas d'appliquer un style de caption programmatiquement :
  le monteur applique le Track Style de marque en 1 clic après insertion.
- L'édition manuelle des cues dans le panneau n'est pas encore réinjectée dans le
  SRT (TODO).
