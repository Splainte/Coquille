# Panneau UXP Coquille — ⚠ ARCHIVÉ

> **Non utilisé.** Le runtime actif est **CEP** (`../cep-panel/`). Raison : l'API UXP
> 2026 n'expose **pas** `createCaptionTrack` (insertion de piste caption) ni le rendu
> audio — `Sequence`/`SequenceEditor`/`CaptionTrack` sont en lecture seule. Ces API
> n'existent qu'en ExtendScript, d'où le choix CEP.
>
> Ce squelette est **conservé pour un re-port futur** : le jour où UXP exposera la
> création de captions, seul ce dossier changera (le backend est agnostique du runtime).
> Les API UXP vérifiées au passage : audio = `EncoderManager.exportSequence`,
> transcript = `Transcript.exportToJSON(clipProjectItem)`.

Panneau de sous-titrage pour Premiere Pro (UXP, min. 25.1). Squelette **sans étape
de build** (JS vanilla + Spectrum natif) pour démarrer vite ; à migrer vers
[bolt-uxp](https://github.com/hyperbrew/bolt-uxp) (TypeScript + React) pour la prod.

## Charger le panneau (dev)

1. Lancer le backend : `uvicorn coquille.app:app --port 8765` (voir `../backend`).
2. Installer **UXP Developer Tool** (UDT) depuis Creative Cloud.
3. UDT ▸ *Add Plugin* ▸ sélectionner `uxp-panel/manifest.json`.
4. *Load* ▸ ouvrir le panneau dans Premiere via *Fenêtre ▸ Extensions ▸ Coquille*.

## Structure

| Fichier | Rôle |
|---------|------|
| `manifest.json` | Déclaration UXP (host, permissions réseau `:8765`, accès FS). |
| `index.html` / `src/main.js` | UI + orchestration du flux. |
| `src/api/backend.js` | Client HTTP vers le backend local. |
| `src/ppro/transcript.js` | Lecture du transcript natif Premiere. ⚠ spike |
| `src/ppro/audio.js` | Rendu WAV de la séquence. ⚠ spike |
| `src/ppro/captions.js` | `importFiles` + `createCaptionTrack`. |

## Spike à mener en premier

Les trois fichiers `src/ppro/*.js` contiennent des appels marqués `⚠ SPIKE À VALIDER`.
Valider ces 3 points (transcript, audio, insertion) sur une vraie séquence avant
d'investir davantage — c'est le principal risque technique du projet. La référence
API : <https://developer.adobe.com/premiere-pro/uxp/ppro-reference/> et les samples
<https://github.com/AdobeDocs/uxp-premiere-pro-samples> (`premiere-api`).
