/*
 * Config locale du panneau (spécifique à CETTE machine) — À COPIER en
 * `config.local.js` (non versionné) et adapter.
 *
 *   cp config.local.example.js config.local.js
 *
 * audioPresetPath = chemin absolu d'un preset .epr "audio WAV uniquement"
 * exporté depuis Premiere. Sur macOS, généralement sous :
 *   ~/Documents/Adobe/Adobe Media Encoder/<version>/Presets/
 */
window.COQUILLE = {
  audioPresetPath: "/Users/<toi>/Documents/Adobe/Adobe Media Encoder/26.0/Presets/Coquille WAV.epr",

  // --- Backend (optionnel) ---
  // Par défaut le backend est local (http://127.0.0.1:8765, même machine que Premiere).
  // Pour transcrire sur un PC GPU distant, pointe son IP et active l'upload :
  // backendUrl: "http://192.168.1.42:8765",
  // remoteBackend: true
};
