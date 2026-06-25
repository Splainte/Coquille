# Coquille

Extension Adobe Premiere Pro de **sous-titrage augmenté par LLM**, pour Frandroid,
Numerama et Clubic. Elle corrige les erreurs de transcription sur le jargon tech
(noms de produits, anglicismes) tout en réutilisant la précision des timecodes,
puis insère des sous-titres stylés par média avec un découpage de lignes équilibré.

Le nom « Coquille » = *typo* en français. 🙂

## Architecture

```
Séquence Premiere
   │  (panneau UXP) rendu audio WAV + lecture du transcript natif (timecodes + confiance)
   ▼
Backend local (FastAPI, machines maison)
   ├─ Whisper(X) / Voxtral   → texte précis + timestamps au mot
   ├─ alignement hybride      → texte Whisper ancré sur les timecodes Premiere
   ├─ correction LLM          → OpenRouter + glossaire par média (timing-safe)
   ├─ segmentation/lignes     → CPS, max car/ligne, ≤2 lignes, équilibrage
   └─ export SRT
   ▼
Panneau UXP
   ├─ révision humaine (cues éditables, faible confiance surlignée)
   └─ importFiles(.srt) → createCaptionTrack() → Track Style de marque (1 clic)
```

Décisions de conception et état de l'API Premiere : voir
`~/.claude/plans/je-souhaite-cr-er-une-ticklish-cherny.md`.

## Composants

| Dossier        | Rôle |
|----------------|------|
| `backend/`     | Pipeline Python : transcription, alignement, correction, segmentation, SRT, API REST. |
| `uxp-panel/`   | Panneau UXP (UI + accès Premiere : audio, transcript, insertion). |
| `legacy-jsx/`  | Repli ExtendScript pour l'insertion uniquement. |
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
- 🟡 **Panneau UXP** : squelette fonctionnel + appels API. **3 points à valider par
  un spike sur Premiere** (signalés `⚠ SPIKE` dans le code) :
  1. lecture du transcript natif (`uxp-panel/src/ppro/transcript.js`) ;
  2. rendu audio de la séquence (`uxp-panel/src/ppro/audio.js`) ;
  3. `createCaptionTrack()` réel (`uxp-panel/src/ppro/captions.js`).
- 🟡 **Track Styles** : à créer une fois dans Premiere (noms `Coquille — Frandroid/
  Numerama/Clubic`), valeurs de référence dans `backend/presets/*.json`.

## Limites connues

- L'API Premiere ne permet pas d'appliquer un style de caption programmatiquement :
  le monteur applique le Track Style de marque en 1 clic après insertion.
- L'édition manuelle des cues dans le panneau n'est pas encore réinjectée dans le
  SRT (TODO).
