# Panneau CEP Coquille

Panneau de sous-titrage pour Premiere Pro, en **CEP/ExtendScript**.

## Pourquoi CEP et pas UXP ?

Le cœur de Coquille est l'**insertion automatisée** de la piste de sous-titres.
La référence UXP (2026) ne l'expose pas (`Sequence`/`SequenceEditor`/`CaptionTrack`
sont en lecture seule). Seul ExtendScript fournit `createCaptionTrack` —
et `exportAsMediaDirect` pour le rendu audio. Adobe n'a pas annoncé de date de fin
pour CEP. Le backend Python étant agnostique du runtime, un re-port UXP futur ne
touchera que ce dossier.

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

## Rendu audio

Le panneau rend l'audio de la séquence avec le préréglage **livré avec Premiere**
`Settings/EncoderPresets/WAV_Mono_16bit_16kHz.epr` (le format natif de Whisper),
trouvé automatiquement par `coquilleDefaultAudioPreset()`. Pour forcer un autre
`.epr`, renseigner `audioPresetPath` dans `js/config.local.js` (cf. l'exemple).

## Validation

Validé de bout en bout sur Premiere 26.5 (Windows) : rendu audio → transcription →
correction → revue des suggestions → insertion de la piste de sous-titres.
