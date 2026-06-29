/*
 * Coquille — orchestration du panneau CEP (contexte CEF/Node).
 *
 * Deux ponts :
 *   - vers Premiere : window.__adobe_cep__.evalScript() → jsx/coquille.jsx
 *   - vers le backend Python local : XMLHttpRequest → http://127.0.0.1:8765
 *
 * On évite la dépendance à CSInterface.js : __adobe_cep__ est injecté par CEP.
 */

var CONFIG = window.COQUILLE || {};
// backendUrl : surchargeable (ex. IP du PC GPU). remoteBackend : si true, le WAV
// est envoyé en upload (backend sur une autre machine) au lieu d'un chemin local.
var BACKEND = CONFIG.backendUrl || "http://127.0.0.1:8765";
var REMOTE = !!CONFIG.remoteBackend;

// ⚠ À CONFIGURER : chemin d'un preset .epr "audio WAV uniquement" exporté depuis
// Premiere (Fichier ▸ Exporter ▸ Média ▸ régler WAV ▸ Enregistrer le préréglage).
// Config machine = fichier NON versionné cep-panel/js/config.local.js (cf. .example).
// Sans lui, le rendu audio (donc Whisper) est indisponible.
var AUDIO_PRESET_PATH =
  (window.COQUILLE && window.COQUILLE.audioPresetPath) || "";

var $ = function (id) { return document.getElementById(id); };
var setStatus = function (msg) { $("status").textContent = msg; };
// Spinner d'attente : affiché pendant les phases longues (rendu, transcription).
var setBusy = function (on) { $("spinner").classList.toggle("hidden", !on); };
var lastResult = null;

// --- Nettoyage des temporaires ----------------------------------------------
// Les WAV (rendu audio) et SRT (insertion) s'écrivent dans os.tmpdir() avec un
// nom horodaté. On supprime le WAV dès la transcription faite, et on purge au
// démarrage les coquille-*.wav/srt de plus d'une heure (au cas où).
function rmFile(p) { try { require("fs").unlinkSync(p); } catch (e) {} }

function cleanupOldTmp() {
  try {
    var fs = require("fs"), os = require("os"), pathmod = require("path");
    var dir = os.tmpdir(), now = Date.now();
    fs.readdirSync(dir).forEach(function (name) {
      if (!/^coquille-.*\.(wav|srt)$/.test(name)) return;
      var fp = pathmod.join(dir, name);
      try { if (now - fs.statSync(fp).mtimeMs > 3600000) fs.unlinkSync(fp); }
      catch (e) {}
    });
  } catch (e) { /* hors Node : rien à nettoyer */ }
}

// --- Pont ExtendScript ------------------------------------------------------
function evalScript(code) {
  return new Promise(function (resolve, reject) {
    if (!window.__adobe_cep__) {
      reject(new Error("Hors Premiere (CEP indisponible)."));
      return;
    }
    window.__adobe_cep__.evalScript(code, function (res) {
      if (typeof res === "string" && res.indexOf("ERREUR:") === 0) {
        reject(new Error(res.slice("ERREUR:".length).trim()));
      } else {
        resolve(res);
      }
    });
  });
}
function jsStr(s) { return JSON.stringify(String(s)); } // échappe pour evalScript

// --- Pont backend (XHR, pas de dépendance fetch) ----------------------------
function http(method, path, body) {
  return new Promise(function (resolve, reject) {
    var xhr = new XMLHttpRequest();
    xhr.open(method, BACKEND + path, true);
    xhr.setRequestHeader("Content-Type", "application/json");
    xhr.onload = function () {
      if (xhr.status >= 200 && xhr.status < 300) {
        try { resolve(JSON.parse(xhr.responseText)); }
        catch (e) { reject(new Error("Réponse JSON invalide : " + e.message)); }
      } else {
        reject(new Error("HTTP " + xhr.status + " " + path + " : " + xhr.responseText));
      }
    };
    xhr.onerror = function () { reject(new Error("Backend injoignable (" + BACKEND + ").")); };
    xhr.send(body ? JSON.stringify(body) : null);
  });
}

// Upload multipart d'un fichier local (lu via Node fs) → backend distant.
function httpUpload(path, filePath, fields) {
  return new Promise(function (resolve, reject) {
    var buf;
    try { buf = require("fs").readFileSync(filePath); }
    catch (e) { reject(new Error("Lecture WAV impossible : " + e.message)); return; }
    var fd = new FormData();
    fd.append("file", new Blob([new Uint8Array(buf)]), "audio.wav");
    Object.keys(fields || {}).forEach(function (k) { fd.append(k, fields[k]); });
    var xhr = new XMLHttpRequest();
    xhr.open("POST", BACKEND + path, true); // pas de Content-Type : boundary auto
    xhr.onload = function () {
      if (xhr.status >= 200 && xhr.status < 300) { resolve(JSON.parse(xhr.responseText)); }
      else { reject(new Error("HTTP " + xhr.status + " " + path + " : " + xhr.responseText)); }
    };
    xhr.onerror = function () { reject(new Error("Backend injoignable (" + BACKEND + ").")); };
    xhr.send(fd);
  });
}

var backend = {
  getPresets: function () { return http("GET", "/presets").then(function (r) { return r.presets; }); },
  getEngines: function () { return http("GET", "/engines"); },
  transcribe: function (audioPath, engine) {
    if (REMOTE) {
      return httpUpload("/transcribe-upload", audioPath, { engine: engine, language: "fr" })
        .then(function (r) { return r.words; });
    }
    return http("POST", "/transcribe", { audio_path: audioPath, language: "fr", engine: engine })
      .then(function (r) { return r.words; });
  },
  process: function (payload) { return http("POST", "/process", payload); }
};

// --- Initialisation ---------------------------------------------------------
function init() {
  cleanupOldTmp();
  backend.getPresets().then(function (presets) {
    var sel = $("media");
    presets.forEach(function (p) {
      var opt = document.createElement("option");
      opt.value = p.media; opt.textContent = p.label;
      sel.appendChild(opt);
    });
    setStatus("Prêt." + (REMOTE ? " (backend distant)" : ""));
  }).catch(function (e) {
    setStatus("Backend injoignable (lancez uvicorn sur :8765). " + e.message);
  });

  // Sélecteur de moteur de transcription (Auto affiche ce qu'il choisit ici).
  backend.getEngines().then(function (info) {
    var sel = $("engine");
    info.engines.forEach(function (e) {
      var opt = document.createElement("option");
      opt.value = e.id;
      opt.textContent = e.id === "auto"
        ? e.label + " → " + info.auto_resolves_to + " [" + info.device + "]"
        : e.label;
      sel.appendChild(opt);
    });
  }).catch(function () { /* backend down : déjà signalé via getPresets */ });

  // Max caractères/ligne : piloté ici (indépendant du média), persisté localement.
  var saved = null;
  try { saved = window.localStorage.getItem("coquille.maxchars"); } catch (e) {}
  if (saved) { $("maxchars").value = saved; }
  $("maxchars").addEventListener("change", function () {
    try { window.localStorage.setItem("coquille.maxchars", $("maxchars").value); } catch (e) {}
  });

  $("generate").addEventListener("click", onGenerate);
  $("insert").addEventListener("click", onInsert);
}

// --- Flux génération --------------------------------------------------------
function onGenerate() {
  $("insert").disabled = true;
  lastResult = null;
  var media = $("media").value;
  var engine = $("engine").value || "auto";
  var offline = $("offline").checked;
  var maxChars = parseInt($("maxchars").value, 10) || null;

  if (!AUDIO_PRESET_PATH) {
    setStatus("Preset audio non configuré (window.COQUILLE.audioPresetPath dans " +
      "cep-panel/js/config.local.js) — rendu audio impossible.");
    return;
  }

  var tmpWav = "";
  setBusy(true);
  setStatus("Rendu audio de la séquence…");
  // os.tmpdir via Node (CEP : --enable-nodejs)
  try {
    var os = require("os"); var pathmod = require("path");
    tmpWav = pathmod.join(os.tmpdir(), "coquille-" + Date.now() + ".wav");
  } catch (e) {
    setBusy(false); setStatus("Node indisponible : " + e.message); return;
  }

  evalScript("coquilleExportAudio(" + jsStr(tmpWav) + "," + jsStr(AUDIO_PRESET_PATH) + ",0)")
    .then(function (res) {
      var wav = res.indexOf("OK:") === 0 ? res.slice(3) : tmpWav;
      setStatus("Transcription (" + engine + ")…");
      return backend.transcribe(wav, engine).then(function (words) {
        rmFile(wav);  // WAV inutile une fois transcrit
        return words;
      });
    })
    .then(function (whisperWords) {
      setStatus("Alignement, correction et segmentation…");
      return backend.process({
        media: media, whisper_words: whisperWords, offline: offline,
        max_chars_per_line: maxChars,
      });
    })
    .then(function (result) {
      setBusy(false);
      lastResult = result;
      renderCues(result.cues);
      setStatus(result.cues.length + " sous-titres générés. Track Style : " + result.track_style);
      $("insert").disabled = false;
    })
    .catch(function (e) { setBusy(false); setStatus("Erreur : " + e.message); });
}

function renderCues(cues) {
  var c = $("cues"); c.innerHTML = "";
  cues.forEach(function (cue) {
    var div = document.createElement("div"); div.className = "cue";
    var tc = document.createElement("div"); tc.className = "tc";
    tc.textContent = "#" + cue.index + "  " + cue.start.toFixed(2) + " → " +
      cue.end.toFixed(2) + "  (CPS " + cue.cps + ")";
    var ta = document.createElement("textarea");
    ta.rows = cue.lines.length; ta.value = cue.lines.join("\n");
    div.appendChild(tc); div.appendChild(ta); c.appendChild(div);
  });
}

// --- Flux insertion ---------------------------------------------------------
// Écrit le SRT en fichier temporaire puis appelle createCaptionTrack côté hôte.
function insertSrt(srtText, styleName) {
  var fs, os, pathmod, srtPath;
  try {
    fs = require("fs"); os = require("os"); pathmod = require("path");
    srtPath = pathmod.join(os.tmpdir(), "coquille-" + Date.now() + ".srt");
    fs.writeFileSync(srtPath, srtText, "utf8");
  } catch (e) { setStatus("Écriture SRT impossible : " + e.message); return; }

  setStatus("Insertion de la piste de sous-titres…");
  var styleMsg = styleName ? " Appliquez le Track Style « " + styleName + " »." : "";
  evalScript("coquilleInsertCaptions(" + jsStr(srtPath) + ",0)")
    .then(function (res) {
      // "OK:1" = clip retiré du chutier → on peut supprimer le fichier.
      // "OK:0:<raison>" = clip resté → on garde le fichier (sinon média hors-ligne).
      var cleaned = typeof res === "string" && res.indexOf("OK:1") === 0;
      if (cleaned) {
        rmFile(srtPath);
        setStatus("Piste insérée." + styleMsg);
      } else {
        var reason = typeof res === "string" ? res.replace(/^OK:0:?/, "") : "";
        setStatus("Piste insérée (clip SRT laissé dans le chutier" +
          (reason ? " : " + reason : "") + ", fichier conservé)." + styleMsg);
      }
    })
    .catch(function (e) { setStatus("Erreur d'insertion : " + e.message); });
}

function onInsert() {
  if (!lastResult) return;
  insertSrt(lastResult.srt, lastResult.track_style);
}

init();
