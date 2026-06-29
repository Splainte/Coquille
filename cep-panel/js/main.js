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
  process: function (payload) { return http("POST", "/process", payload); },
  // Re-segmentation de mots déjà corrigés (après validation des suggestions).
  segment: function (payload) { return http("POST", "/segment", payload); }
};

// État de la revue en cours (suggestions LLM à valider avant insertion).
// words = mots après glossaire (base) ; accepted = { index: textProposé }.
var review = { media: null, maxChars: null, words: [], accepted: {} };

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
  $("corrections").innerHTML = "";
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
        max_chars_per_line: maxChars, llm_mode: "suggest",
      });
    })
    .then(function (result) {
      setBusy(false);
      lastResult = result;  // srt = version glossaire (aucune suggestion appliquée)
      // Mémorise la base pour la revue : mots après glossaire + suggestions.
      review = {
        media: media, maxChars: maxChars,
        words: result.words || [], accepted: {},
      };
      renderCorrections(result.correction);
      renderCues(result.cues);
      setStatus(
        result.cues.length + " sous-titres générés. " +
        correctionSummary(result.correction) +
        "Track Style : " + result.track_style
      );
      $("insert").disabled = false;
    })
    .catch(function (e) { setBusy(false); setStatus("Erreur : " + e.message); });
}

// Applique au tableau de mots de base les suggestions cochées, puis re-segmente
// côté backend et met à jour l'aperçu + le SRT à insérer.
function applyReview() {
  var words = review.words.map(function (w, i) {
    if (Object.prototype.hasOwnProperty.call(review.accepted, i)) {
      var copy = {}; for (var k in w) { copy[k] = w[k]; }
      copy.text = review.accepted[i];
      return copy;
    }
    return w;
  }).filter(function (w) { return w.text !== ""; });  // suppressions acceptées

  setStatus("Mise à jour des sous-titres…");
  return backend.segment({
    media: review.media, words: words, max_chars_per_line: review.maxChars,
  }).then(function (r) {
    if (lastResult) { lastResult.srt = r.srt; lastResult.track_style = r.track_style; }
    renderCues(r.cues);
    var n = Object.keys(review.accepted).length;
    setStatus(r.cues.length + " sous-titres. " + n + " suggestion(s) appliquée(s).");
  }).catch(function (e) { setStatus("Erreur de re-segmentation : " + e.message); });
}

// Résumé lisible de la passe de correction pour la barre de statut.
function correctionSummary(c) {
  if (!c) return "";
  var gloss = (c.items && c.items.length) || 0;
  var sugg = (c.suggestions && c.suggestions.length) || 0;
  var warn = (c.warnings && c.warnings.length) ? "⚠ " + c.warnings.join(" ") + " " : "";
  if (c.error) {
    return "⚠ LLM indisponible (glossaire seul, " + gloss + " corr.) — " + c.error + ". ";
  }
  if (c.used_llm) {
    return gloss + " corr. glossaire, " + sugg + " suggestion(s) LLM à valider (" +
      (c.model || "?") + "). " + warn;
  }
  return gloss + " correction(s) glossaire (hors-ligne). ";
}

// Timecode lisible (m:ss.s) pour le lien « localiser ».
function fmtTc(s) {
  var m = Math.floor(s / 60), sec = s - m * 60;
  return m + ":" + (sec < 10 ? "0" : "") + sec.toFixed(1);
}

// Déplace la tête de lecture de la séquence active sur le mot corrigé.
function seekTo(seconds) {
  evalScript("coquilleSeek(" + Number(seconds) + ")").catch(function (e) {
    setStatus("Localisation impossible : " + e.message);
  });
}

// Un lien « localiser m:ss.s » qui déplace la tête de lecture.
function locLink(start) {
  var loc = document.createElement("span"); loc.className = "loc";
  loc.textContent = "localiser " + fmtTc(start);
  loc.addEventListener("click", function () { seekTo(start); });
  return loc;
}

// Deux sections : corrections glossaire (appliquées, lecture seule) et
// suggestions LLM (à valider via cases à cocher). DOM en textContent.
function renderCorrections(corr) {
  var box = $("corrections");
  box.innerHTML = "";
  if (!corr) return;
  renderGlossarySection(box, corr.items || []);
  renderSuggestionSection(box, corr.suggestions || []);
}

function renderGlossarySection(box, items) {
  if (!items.length) return;
  var det = document.createElement("details");
  det.className = "corr";
  var sum = document.createElement("summary");
  sum.textContent = "Corrections glossaire (" + items.length + ") — appliquées";
  det.appendChild(sum);
  items.forEach(function (it) {
    var row = document.createElement("div"); row.className = "item";
    var o = document.createElement("span"); o.className = "orig"; o.textContent = it.original;
    var a = document.createElement("span"); a.className = "arrow"; a.textContent = "→";
    var n = document.createElement("span"); n.className = "new";
    n.textContent = it.corrected === "" ? "(supprimé)" : it.corrected;
    row.appendChild(o); row.appendChild(a); row.appendChild(n);
    row.appendChild(locLink(it.start));
    det.appendChild(row);
  });
  box.appendChild(det);
}

function renderSuggestionSection(box, suggestions) {
  if (!suggestions.length) return;
  var det = document.createElement("details");
  det.className = "corr"; det.open = true;
  var sum = document.createElement("summary");
  sum.textContent = "Suggestions LLM (" + suggestions.length + ") — à valider";
  det.appendChild(sum);

  // Barre d'actions groupées.
  var bar = document.createElement("div"); bar.className = "item bulk";
  var allBtn = document.createElement("button"); allBtn.textContent = "Tout appliquer";
  var noneBtn = document.createElement("button"); noneBtn.textContent = "Tout ignorer";
  bar.appendChild(allBtn); bar.appendChild(noneBtn);
  det.appendChild(bar);

  var boxes = [];
  suggestions.forEach(function (sg) {
    var row = document.createElement("div"); row.className = "item";
    var cb = document.createElement("input"); cb.type = "checkbox";
    cb.addEventListener("change", function () {
      if (cb.checked) { review.accepted[sg.index] = sg.suggested; }
      else { delete review.accepted[sg.index]; }
      applyReview();
    });
    boxes.push(cb);
    var o = document.createElement("span"); o.className = "orig"; o.textContent = sg.original;
    var a = document.createElement("span"); a.className = "arrow"; a.textContent = "→";
    var n = document.createElement("span"); n.className = "new";
    n.textContent = sg.suggested === "" ? "(supprimer)" : sg.suggested;
    var lab = document.createElement("label"); lab.className = "sugg";
    lab.appendChild(cb); lab.appendChild(o); lab.appendChild(a); lab.appendChild(n);
    row.appendChild(lab);
    row.appendChild(locLink(sg.start));
    det.appendChild(row);
  });

  // Actions groupées : on coche/décoche tout, met à jour `accepted`, re-segmente once.
  allBtn.addEventListener("click", function () {
    suggestions.forEach(function (sg) { review.accepted[sg.index] = sg.suggested; });
    boxes.forEach(function (cb) { cb.checked = true; });
    applyReview();
  });
  noneBtn.addEventListener("click", function () {
    review.accepted = {};
    boxes.forEach(function (cb) { cb.checked = false; });
    applyReview();
  });

  box.appendChild(det);
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
