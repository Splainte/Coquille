# Coquille

Extension Adobe Premiere Pro de **sous-titrage augmenté par LLM**, pour Frandroid,
Numerama, Clubic et Survoltés. Elle corrige les erreurs de transcription sur le jargon tech
(noms de produits, anglicismes) tout en réutilisant la précision des timecodes,
puis insère des sous-titres stylés par média avec un découpage de lignes équilibré.

Le nom « Coquille » = *typo* en français. 🙂

## Architecture

```
Séquence Premiere
   │  (panneau CEP) rendu audio WAV (exportAsMediaDirect)
   ▼
Backend local (FastAPI, machines maison)
   ├─ Whisper large-v3 (+ filtre voix) → texte + timestamps au mot
   ├─ alignement forcé MMS (optionnel) → timing recalé sur la voix (~WhisperX)
   ├─ correction LLM          → glossaire par média + suggestions OpenRouter
   │                            par plages de mots (Gemini 3.8 Flash), à valider
   ├─ découpage optimal       → coût global (pauses, ponctuation, mots-outils,
   │                            durée d'affichage), ≤ 2 lignes équilibrées
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

## Démarrage rapide

- **Windows (monteurs)** : télécharger `Coquille-Setup.exe` dans la dernière release GitHub et
  le lancer (pas de droits admin). Il installe le panneau, un Python autonome avec le moteur de
  transcription, propose l'accélération NVIDIA, le recalage du timing, le téléchargement des
  modèles et une page « Clé API OpenRouter » (facultative). Le panneau démarre ensuite le moteur
  tout seul. Mise à jour : relancer le nouvel installeur (clé et paquets conservés).
  Installation silencieuse : `Coquille-Setup.exe /VERYSILENT /TASKS=gpu,align,models /KEY=sk-or-v1-…`.
- **Windows (développement)** : `setup-windows.bat` (droits admin demandés), puis `run-backend.bat`.
- **macOS** : `bash setup-mac.sh`, puis `bash run-backend.sh`.
- Clé OpenRouter : bouton **Ajouter la clé** en haut du panneau (ou menu ≡ du panneau ▸
  *Clé OpenRouter…*). Elle est vérifiée auprès d'OpenRouter puis écrite dans `backend/.env`,
  effet immédiat. Une pastille indique l'état : verte = correction IA active, grise = pas de
  clé (glossaire seul), rouge = clé refusée, orange = OpenRouter injoignable. On peut aussi
  éditer `backend/.env` à la main (modèle : `backend/.env.example`).
- Premiere ▸ *Fenêtre ▸ Extensions ▸ Coquille — Sous-titres*.

Tests : `cd backend && python3 -m unittest discover -s tests` (69 tests).

## État d'avancement (oct. 2026)

Validé **de bout en bout dans Premiere 26.5.1** (Windows, RTX 4090) sur 8 shorts
Frandroid/Survoltés, comparés à leurs sous-titres faits main :

- texte : ~4,4 % d'écart avec les sous-titres des monteurs une fois les
  suggestions acceptées (dont une bonne part de choix éditoriaux : « km » vs
  « kilomètres ») ;
- timing : apparition à ~0,07 s en moyenne de celle des monteurs (2 images
  d'avance sur la voix, comme eux) ;
- lisibilité : 4 sous-titres < 0,7 s contre 54 chez les monteurs, aucun mot-outil
  en fin de sous-titre, aucun sous-titre d'un seul mot ;
- durée : ~20-40 s pour un short de 1-2 min (dont ~20 s de correction IA).

- 🟡 **Track Styles** : à créer une fois dans Premiere (noms `Coquille — Frandroid/
  Numerama/Clubic/Survoltés`), valeurs de référence dans `backend/presets/*.json`.
- ⚪ **Panneau UXP** : archivé (pas de `createCaptionTrack` en UXP, cf. `uxp-panel/README.md`).

## Limites connues

- L'API Premiere ne permet pas d'appliquer un style de caption programmatiquement :
  le monteur applique le Track Style de marque en 1 clic après insertion.
- L'édition manuelle des cues dans le panneau n'est pas encore réinjectée dans le
  SRT (TODO).
