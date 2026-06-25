# Panneau CEP Coquille

Panneau de sous-titrage pour Premiere Pro, en **CEP/ExtendScript**.

## Pourquoi CEP et pas UXP ?

Le cœur de Coquille est l'**insertion automatisée** de la piste de sous-titres.
La référence UXP (2026) ne l'expose pas (`Sequence`/`SequenceEditor`/`CaptionTrack`
sont en lecture seule). Seul ExtendScript fournit `createCaptionTrack` —
et `exportAsMediaDirect` pour le rendu audio. CEP est déprécié (~sept. 2026) mais
acceptable pour un outil interne (version Premiere épinglée). Le backend Python
étant agnostique du runtime, un re-port UXP futur ne touchera que ce dossier.

## Structure

| Fichier | Rôle |
|---------|------|
| `CSXS/manifest.xml` | Déclaration CEP (host PPRO, Node activé, point d'entrée). |
| `.debug` | Active le débogage (à retirer en distribution). |
| `index.html` | UI du panneau. |
| `js/main.js` | Orchestration : pont `__adobe_cep__.evalScript` + XHR vers le backend. |
| `jsx/coquille.jsx` | Hôte Premiere : `coquilleExportAudio`, `coquilleInsertCaptions`, `coquilleProbe`. |

## Installation (dev)

1. **Mode debug CEP** (une fois) :
   - macOS : `defaults write com.adobe.CSXS.11 PlayerDebugMode 1`
   - Windows : clé registre `HKCU\Software\Adobe\CSXS.11` ▸ `PlayerDebugMode` = `1` (String)
   - (adapter le numéro CSXS à la version de Premiere)
2. Copier/lier `cep-panel/` dans le dossier des extensions CEP :
   - macOS : `~/Library/Application Support/Adobe/CEP/extensions/com.coquille.subtitles`
   - Windows : `%APPDATA%\Adobe\CEP\extensions\com.coquille.subtitles`
3. Lancer le backend : `cd backend && uvicorn coquille.app:app --port 8765`
4. Premiere ▸ *Fenêtre ▸ Extensions ▸ Coquille — Sous-titres*.
5. Débogage : ouvrir <http://localhost:8088> dans Chrome.

## À configurer avant le rendu audio

Dans `js/main.js`, renseigner `AUDIO_PRESET_PATH` = chemin d'un preset `.epr`
**audio WAV uniquement** (exporté depuis Premiere : *Exporter ▸ Média ▸ WAV ▸
Enregistrer le préréglage*). Sans lui, le bouton *Générer* ne peut pas produire
l'audio pour Whisper.

## Spike — à valider dans Premiere

Le bouton **🔍 Diagnostic spike** sonde à l'exécution :
- joignabilité du backend `:8765` ;
- présence réelle de `createCaptionTrack` et `exportAsMediaDirect` dans cet hôte ;
- séquence active / `importFiles` / version Premiere / preset audio configuré.

Lancer ce diagnostic en premier valide les 3 points de risque d'un coup, sur la
vraie version de Premiere ciblée, avant d'aller plus loin.
