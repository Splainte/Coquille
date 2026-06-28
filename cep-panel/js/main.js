/*
 * Coquille — orchestration du panneau CEP (contexte CEF/Node).
 *
 * Deux ponts :
 *   - vers Premiere : window.__adobe_cep__.evalScript() → jsx/coquille.jsx
 *   - vers le backend Python local : XMLHttpRequest → http://127.0.0.1:8765
 *
 * On évite la dépendance à CSInterface.js : __adobe_cep__ est injecté par CEP.
 */

var BACKEND = "http://127.0.0.1:8765";

// ⚠ À CONFIGURER : chemin d'un preset .epr "audio WAV uniquement" exporté depuis
// Premiere (Fichier ▸ Exporter ▸ Média ▸ régler WAV ▸ Enregistrer le préréglage).
// Config machine = fichier NON versionné cep-panel/js/config.local.js (cf. .example).
// Sans lui, le rendu audio (donc Whisper) est indisponible.
var AUDIO_PRESET_PATH =
  (window.COQUILLE && window.COQUILLE.audioPresetPath) || "";

var $ = function (id) { return document.getElementById(id); };
var setStatus = function (msg) { $("status").textContent = msg; };
var lastResult = null;

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

var backend = {
  getPresets: function () { return http("GET", "/presets").then(function (r) { return r.presets; }); },
  transcribe: function (audioPath) {
    return http("POST", "/transcribe", { audio_path: audioPath, language: "fr" })
      .then(function (r) { return r.words; });
  },
  process: function (payload) { return http("POST", "/process", payload); }
};

// --- Initialisation ---------------------------------------------------------
function init() {
  backend.getPresets().then(function (presets) {
    var sel = $("media");
    presets.forEach(function (p) {
      var opt = document.createElement("option");
      opt.value = p.media; opt.textContent = p.label;
      sel.appendChild(opt);
    });
    setStatus("Prêt.");
  }).catch(function (e) {
    setStatus("Backend injoignable (lancez uvicorn sur :8765). " + e.message);
  });

  $("generate").addEventListener("click", onGenerate);
  $("insert").addEventListener("click", onInsert);
  $("diag").addEventListener("click", onDiagnostic);
  $("testInsert").addEventListener("click", onTestInsert);
}

// --- Flux génération --------------------------------------------------------
function onGenerate() {
  $("insert").disabled = true;
  lastResult = null;
  var media = $("media").value;
  var offline = $("offline").checked;

  if (!AUDIO_PRESET_PATH) {
    setStatus("AUDIO_PRESET_PATH non configuré dans js/main.js — rendu audio impossible. " +
      "Indiquez un .epr WAV pour activer Whisper.");
    return;
  }

  var tmpWav = "";
  setStatus("Rendu audio de la séquence…");
  // os.tmpdir via Node (CEP : --enable-nodejs)
  try {
    var os = require("os"); var pathmod = require("path");
    tmpWav = pathmod.join(os.tmpdir(), "coquille-" + Date.now() + ".wav");
  } catch (e) {
    setStatus("Node indisponible : " + e.message); return;
  }

  evalScript("coquilleExportAudio(" + jsStr(tmpWav) + "," + jsStr(AUDIO_PRESET_PATH) + ",0)")
    .then(function (res) {
      var wav = res.indexOf("OK:") === 0 ? res.slice(3) : tmpWav;
      setStatus("Transcription Whisper en cours…");
      return backend.transcribe(wav);
    })
    .then(function (whisperWords) {
      setStatus("Alignement, correction et segmentation…");
      return backend.process({ media: media, whisper_words: whisperWords, offline: offline });
    })
    .then(function (result) {
      lastResult = result;
      renderCues(result.cues);
      setStatus(result.cues.length + " sous-titres générés. Track Style : " + result.track_style);
      $("insert").disabled = false;
    })
    .catch(function (e) { setStatus("Erreur : " + e.message); });
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
  evalScript("coquilleInsertCaptions(" + jsStr(srtPath) + ",0)")
    .then(function () {
      setStatus("Piste insérée." +
        (styleName ? " Appliquez le Track Style « " + styleName + " »." : ""));
    })
    .catch(function (e) { setStatus("Erreur d'insertion : " + e.message); });
}

function onInsert() {
  if (!lastResult) return;
  insertSrt(lastResult.srt, lastResult.track_style);
}

// Insertion d'un SRT factice — valide createCaptionTrack sans backend ni Whisper.
function onTestInsert() {
  var srt = [
    "1", "00:00:00,000 --> 00:00:02,500", "Coquille — test d'insertion", "",
    "2", "00:00:02,500 --> 00:00:05,000", "Si ce texte apparaît dans la timeline,", "",
    "3", "00:00:05,000 --> 00:00:07,500", "createCaptionTrack fonctionne. ✅", ""
  ].join("\n");
  insertSrt(srt, null);
}

// --- Diagnostic du spike ----------------------------------------------------
function onDiagnostic() {
  var out = $("diagOut");
  out.innerHTML = "Sondage…";
  var lines = [];
  var line = function (label, ok, extra) {
    lines.push('<div class="' + (ok ? "ok" : "ko") + '">' +
      (ok ? "✓ " : "✗ ") + label + (extra ? " — " + extra : "") + "</div>");
    out.innerHTML = lines.join("");
  };

  // 1. Backend
  backend.getPresets()
    .then(function (p) { line("Backend :8765", true, p.length + " presets"); })
    .catch(function (e) { line("Backend :8765", false, e.message); })
    // 2. Sonde ExtendScript
    .then(function () { return evalScript("coquilleProbe()"); })
    .then(function (res) {
      var caps = JSON.parse(res.slice(3)); // retire "OK:"
      line("Projet ouvert", caps.hasProject);
      line("Séquence active", caps.hasActiveSequence);
      line("importFiles", caps.hasImportFiles);
      line("createCaptionTrack (insertion)", caps.hasCreateCaptionTrack);
      line("exportAsMediaDirect (audio)", caps.hasExportAsMediaDirect);
      line("Premiere " + caps.appVersion, true);
      line("AUDIO_PRESET_PATH configuré", !!AUDIO_PRESET_PATH,
        AUDIO_PRESET_PATH || "à renseigner dans js/main.js");
    })
    .catch(function (e) { line("Sonde ExtendScript", false, e.message); });
}

init();
