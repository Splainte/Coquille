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
  // audioPresetPath : OPTIONNEL — par défaut, le préréglage WAV livré avec Premiere
  // est trouvé automatiquement. À renseigner seulement pour forcer un .epr perso :
  // audioPresetPath: "/Users/<toi>/Documents/Adobe/Adobe Media Encoder/26.0/Presets/Coquille WAV.epr",

  // --- Démarrage automatique du backend (écrit par Coquille-Setup.exe) ---
  // Si le backend ne répond pas, le panneau lance ce Python (fenêtre cachée).
  // backendPython: "C:\\Users\\<toi>\\AppData\\Local\\Coquille\\python\\pythonw.exe",
  // backendDir: "C:\\Users\\<toi>\\AppData\\Local\\Coquille\\backend",
  // backendLog: "C:\\Users\\<toi>\\AppData\\Local\\Coquille\\backend.log",

  // --- Backend (optionnel) ---
  // Par défaut le backend est local (http://127.0.0.1:8765, même machine que Premiere).
  // Pour transcrire sur un PC GPU distant, pointe son IP et active l'upload :
  // backendUrl: "http://192.168.1.42:8765",
  // remoteBackend: true
};
