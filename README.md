# Coquille

Extension Adobe Premiere Pro de **sous-titrage augmenté par IA**, pensée pour les vidéos tech
(Frandroid, Numerama, Clubic, Survoltés). Elle transcrit la séquence, corrige les erreurs sur le
jargon (noms de produits, anglicismes, homophones) sans toucher au timing, découpe les
sous-titres en lignes équilibrées puis les insère en piste de sous-titres dans la séquence.

## Installation

Téléchargez l'installeur dans la [dernière release](https://github.com/Splainte/Coquille/releases/latest).

- **Windows** : `Coquille-Setup.exe` (pas de droits administrateur). Il installe le panneau, un
  Python autonome avec le moteur de transcription et, sans rien demander, l'accélération NVIDIA
  (si une carte est présente), le recalage du timing et les modèles. Seule la page « Clé API
  OpenRouter » (facultative) est proposée. Installation silencieuse :
  `Coquille-Setup.exe /VERYSILENT /KEY=sk-or-v1-…`.
- **macOS Apple Silicon** : `Coquille-macOS.pkg`. Le paquet n'est pas signé par Apple : au premier
  lancement, Réglages Système ▸ Confidentialité et sécurité ▸ « Ouvrir quand même » (ou clic droit
  ▸ Ouvrir). Transcription sur le processeur. Les Mac Intel ne sont pas pris en charge (PyTorch
  n'y existe plus).

Puis redémarrez Premiere ▸ *Fenêtre ▸ Extensions ▸ Coquille*. Le moteur de
transcription démarre tout seul à l'ouverture du panneau.

## Utilisation

1. Choisissez le média (règles de découpage et glossaire propres à chaque marque).
2. **Générer** : la zone sous-titrée est celle des points d'entrée/sortie de la séquence s'ils
   sont posés, sinon l'étendue des clips sélectionnés, sinon toute la séquence. Une barre
   d'avancement suit la transcription.
3. Relisez les **suggestions de l'IA** (à cocher une par une ou toutes d'un coup) : rien n'est
   appliqué sans votre accord. Les corrections du glossaire, elles, sont appliquées d'office.
4. **Insérer** : la piste de sous-titres est créée dans la séquence. Appliquez ensuite le Track
   Style de la marque en un clic.

Le bouton **Recommencer** (flèche circulaire en haut à droite) efface tout ce qui a été généré et
annule une génération en cours ; les réglages sont conservés.

**Mises à jour** : le panneau vérifie au démarrage s'il existe une nouvelle version. Si c'est le
cas, le lien en bas du panneau devient « Installer la mise à jour » : un clic la télécharge,
l'installe (clé et modèles conservés) puis recharge le panneau.

**Clé OpenRouter** (facultative) : bouton *Ajouter la clé* en haut du panneau. Elle est vérifiée
auprès d'OpenRouter puis enregistrée uniquement sur la machine. Sans clé, seul le glossaire
corrige la transcription.

## Comment ça marche

```
Séquence Premiere
   │  panneau CEP : rendu audio WAV
   ▼
Moteur local (Python, FastAPI)
   ├─ Whisper (faster-whisper, GPU si dispo) → texte + temps au mot
   ├─ alignement forcé MMS (optionnel)       → timing recalé sur la voix
   ├─ correction                             → glossaire par média
   │                                           + suggestions IA via OpenRouter, à valider
   ├─ découpage                              → pauses, ponctuation, mots-outils,
   │                                           durée d'affichage, ≤ 2 lignes équilibrées
   └─ export SRT
   ▼
Panneau CEP : revue des suggestions → importFiles(.srt) → createCaptionTrack()
```

Le panneau est en **CEP** (et non UXP) : l'insertion automatique d'une piste de sous-titres
(`createCaptionTrack`) et le rendu audio n'existent qu'en ExtendScript. Le moteur étant
indépendant du panneau, un portage UXP ne toucherait que `cep-panel/`.

| Dossier             | Rôle |
|---------------------|------|
| `backend/`          | Moteur Python : transcription, alignement, correction, découpage, SRT, API locale. |
| `backend/glossary/` | Glossaires éditables (base + par média), cœur de la correction. |
| `backend/presets/`  | Règles de découpage par média + nom du Track Style. |
| `cep-panel/`        | Panneau CEP (interface + ExtendScript : rendu audio, insertion). |
| `installer/`        | Installeurs Windows (Inno Setup) et macOS (pkg), fabriqués par GitHub Actions. |
| `uxp-panel/`        | Ébauche UXP archivée, gardée pour un portage futur. |

## Développement

- Windows : `setup-windows.bat` (droits admin demandés), puis `run-backend.bat`.
- macOS : `bash setup-mac.sh`, puis `bash run-backend.sh`.
- Tests : `cd backend && python3 -m unittest discover -s tests`.
- Le panneau se débogue sur <http://localhost:8088> (fichier `cep-panel/.debug`).

## Limites connues

- L'API Premiere ne permet pas d'appliquer un style de sous-titres par script : le Track Style
  de la marque s'applique à la main (un clic) après insertion.
- Aucune API ne permet de modifier le texte d'une piste déjà posée : la relecture se fait dans le
  panneau, avant insertion.
