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

// Optionnel : chemin d'un preset .epr "audio WAV" personnalisé (config machine,
// fichier NON versionné cep-panel/js/config.local.js). Vide = le JSX prend le
// préréglage WAV livré avec Premiere (coquilleDefaultAudioPreset).
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
    // → { words, timing } ; timing = "aligné" si le timing a été recalé.
    if (REMOTE) {
      return httpUpload("/transcribe-upload", audioPath, { engine: engine, language: "fr" });
    }
    return http("POST", "/transcribe", { audio_path: audioPath, language: "fr", engine: engine });
  },
  process: function (payload) { return http("POST", "/process", payload); },
  // Re-segmentation de mots déjà corrigés (après validation des suggestions).
  segment: function (payload) { return http("POST", "/segment", payload); },
  // Clé OpenRouter : état (check = vérification réelle auprès d'OpenRouter),
  // enregistrement (vérifiée puis écrite dans backend/.env), retrait.
  llmStatus: function (check) { return http("GET", "/llm" + (check ? "?check=true" : "")); },
  saveKey: function (key) { return http("POST", "/llm/key", { key: key }); },
  deleteKey: function () { return http("DELETE", "/llm/key"); }
};

// Message lisible d'une erreur HTTP du backend (« HTTP 400 /x : {"detail": …} »).
function errDetail(e) {
  var m = /^HTTP \d+ [^ ]+ : ([\s\S]*)$/.exec(e.message || "");
  if (m) {
    try { var d = JSON.parse(m[1]).detail; if (typeof d === "string") return d; } catch (x) {}
  }
  return e.message;
}

// État de la revue en cours (suggestions LLM à valider avant insertion).
// words = mots après glossaire (base) ; accepted = { index: suggestion } — une
// suggestion remplace la plage de mots [index, end_index] par `suggested`.
var review = { media: null, maxChars: null, words: [], accepted: {} };

// --- Initialisation ---------------------------------------------------------
// --- Démarrage automatique du backend (installation par Coquille-Setup.exe) ---
// L'installeur écrit dans config.local.js le Python embarqué et le dossier du
// backend : si le backend ne répond pas, le panneau le lance lui-même (fenêtre
// cachée, journal dans backendLog) et attend qu'il réponde.
function startBackend() {
  if (!CONFIG.backendPython || REMOTE) return false;
  try {
    var cp = require("child_process");
    var port = (/:(\d+)\/?$/.exec(BACKEND) || [])[1] || "8765";
    var args = ["-m", "coquille.serve", "--port", port];
    if (CONFIG.backendLog) { args.push("--log", CONFIG.backendLog); }
    var child = cp.spawn(CONFIG.backendPython, args,
      { cwd: CONFIG.backendDir, detached: true, stdio: "ignore", windowsHide: true });
    child.unref();
    return true;
  } catch (e) { return false; }
}

function ensureBackend() {
  return backend.getPresets().catch(function (err) {
    if (!startBackend()) throw err;
    setBusy(true);
    setStatus("Démarrage du moteur Coquille…");
    var deadline = Date.now() + 120000;  // 1er lancement : imports + antivirus
    return new Promise(function (resolve, reject) {
      (function poll() {
        backend.getPresets().then(function (p) { setBusy(false); resolve(p); }).catch(function (e) {
          if (Date.now() > deadline) {
            setBusy(false);
            reject(new Error("le moteur ne démarre pas" +
              (CONFIG.backendLog ? " (journal : " + CONFIG.backendLog + ")" : "") + "."));
          } else { setTimeout(poll, 1000); }
        });
      })();
    });
  });
}

function init() {
  cleanupOldTmp();
  initKeyUi();
  ensureBackend().then(function (presets) {
    var sel = $("media");
    presets.forEach(function (p) {
      var opt = document.createElement("option");
      opt.value = p.media; opt.textContent = p.label;
      sel.appendChild(opt);
    });
    // Dernier média choisi, mémorisé (sinon on retomberait sur le 1er : Clubic).
    try {
      var m = window.localStorage.getItem("coquille.media");
      if (m && presets.some(function (p) { return p.media === m; })) { sel.value = m; }
    } catch (e) {}
    sel.addEventListener("change", function () {
      try { window.localStorage.setItem("coquille.media", sel.value); } catch (e) {}
    });
    setStatus("Prêt." + (REMOTE ? " (backend distant)" : ""));
    refreshLlm(true);
    return backend.getEngines();
  }).then(function (info) {
    var sel = $("engine");
    info.engines.forEach(function (e) {
      var opt = document.createElement("option");
      opt.value = e.id;
      opt.textContent = e.id === "auto"
        ? e.label + " → " + info.auto_resolves_to + " [" + info.device + "]"
        : e.label;
      sel.appendChild(opt);
    });
  }).catch(function (e) {
    refreshLlm(false);
    setStatus("Backend injoignable : " + e.message +
      (CONFIG.backendPython ? "" : " Lancez run-backend.bat (ou uvicorn sur :8765)."));
  });

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

// --- Clé OpenRouter : indicateur + menu de saisie ----------------------------
function renderLlm(st) {
  var dot = $("llmDot"), txt = $("llmText"), cls = "warn", msg;
  var hint = st.key_hint ? " (" + st.key_hint + ")" : "";
  if (st.offline_forced) {
    msg = "Correction IA coupée (COQUILLE_CORRECT_OFFLINE)";
  } else if (st.state === "absente") {
    cls = "none"; msg = "Pas de clé OpenRouter : glossaire seul";
  } else if (st.state === "ok") {
    cls = "ok"; msg = "Correction IA active" + hint;
    if (st.limit_remaining !== null && st.limit_remaining !== undefined) {
      msg += " — reste " + Number(st.limit_remaining).toFixed(2) + " $";
    }
  } else if (st.state === "invalide") {
    cls = "bad"; msg = "Clé OpenRouter refusée" + hint;
  } else if (st.state === "injoignable") {
    msg = "OpenRouter injoignable" + hint;
  } else {
    msg = "Clé OpenRouter non vérifiée" + hint;
  }
  dot.className = cls;
  txt.textContent = msg;
  $("llmRow").title = msg + (st.detail ? " — " + st.detail : "") +
    (st.model ? "\nModèle : " + st.model : "");
  $("llmSettings").textContent = st.configured ? "Clé…" : "Ajouter la clé";
  $("keyDelete").disabled = !st.configured;
}

function refreshLlm(check) {
  return backend.llmStatus(check).then(renderLlm).catch(function () {
    $("llmDot").className = "none";
    $("llmText").textContent = "Clé OpenRouter : backend injoignable";
  });
}

function setKeyMsg(msg) { $("keyMsg").textContent = msg; }

function toggleKeyBox(show) {
  var box = $("keyBox");
  if (show === undefined) show = box.classList.contains("hidden");
  box.classList.toggle("hidden", !show);
  if (show) { setKeyMsg(""); $("keyInput").value = ""; $("keyInput").focus(); }
}

function openUrl(url) {
  try {
    if (window.cep && window.cep.util) { window.cep.util.openURLInDefaultBrowser(url); return; }
  } catch (e) {}
  try { require("child_process").exec((process.platform === "win32" ? 'start "" ' : "open ") + '"' + url + '"'); }
  catch (e) { setKeyMsg("Ouvrez " + url + " dans votre navigateur."); }
}

function initKeyUi() {
  $("llmSettings").addEventListener("click", function () { toggleKeyBox(); });
  $("keyClose").addEventListener("click", function () { toggleKeyBox(false); });
  $("keyLink").addEventListener("click", function () { openUrl("https://openrouter.ai/settings/keys"); });
  $("keyInput").addEventListener("keydown", function (e) {
    if (e.key === "Enter") { $("keySave").click(); }
    if (e.key === "Escape") { toggleKeyBox(false); }
  });
  $("keySave").addEventListener("click", function () {
    var key = $("keyInput").value.trim();
    if (!key) { setKeyMsg("Collez d'abord la clé (sk-or-v1-…)."); return; }
    setKeyMsg("Vérification auprès d'OpenRouter…");
    backend.saveKey(key).then(function (st) {
      $("keyInput").value = "";
      renderLlm(st);
      setKeyMsg(st.state === "ok"
        ? "Clé enregistrée et valide" + (st.label ? " (« " + st.label + " »)" : "") + "."
        : "Clé enregistrée, mais non vérifiée : " + st.detail + ".");
      if (st.from_system_env) {
        setKeyMsg($("keyMsg").textContent + "\n⚠ Une variable d'environnement " +
          "OPENROUTER_API_KEY existe sur cette machine : elle reprendra le dessus " +
          "au prochain démarrage du backend.");
      }
      if (st.state === "ok") { setTimeout(function () { toggleKeyBox(false); }, 1500); }
    }).catch(function (e) { setKeyMsg(errDetail(e)); });
  });
  $("keyTest").addEventListener("click", function () {
    setKeyMsg("Vérification…");
    backend.llmStatus(true).then(function (st) {
      renderLlm(st);
      setKeyMsg(st.state === "absente" ? "Aucune clé enregistrée." : $("llmText").textContent +
        (st.detail ? " — " + st.detail : "") + ".");
    }).catch(function (e) { setKeyMsg(errDetail(e)); });
  });
  $("keyDelete").addEventListener("click", function () {
    backend.deleteKey().then(function (st) {
      renderLlm(st);
      setKeyMsg("Clé retirée : correction par glossaire seul.");
    }).catch(function (e) { setKeyMsg(errDetail(e)); });
  });

  // Entrées du menu du panneau (≡ en haut à droite du panneau Premiere).
  try {
    var cepApi = window.__adobe_cep__;
    if (cepApi) {
      cepApi.invokeSync("setPanelFlyoutMenu",
        '<Menu><MenuItem Id="key" Label="Clé OpenRouter…" Enabled="true" Checked="false"/>' +
        '<MenuItem Id="check" Label="Vérifier la clé OpenRouter" Enabled="true" Checked="false"/></Menu>');
      cepApi.addEventListener("com.adobe.csxs.events.flyoutMenuClicked", function (ev) {
        var data = ev && ev.data !== undefined ? ev.data : ev;
        if (typeof data === "string") {
          try { data = JSON.parse(data); } catch (x) { data = { menuId: (/menuId\W+(\w+)/.exec(data) || [])[1] }; }
        }
        if (data && data.menuId === "key") toggleKeyBox(true);
        if (data && data.menuId === "check") { toggleKeyBox(true); $("keyTest").click(); }
      });
    }
  } catch (e) { /* menu facultatif : le bouton « Clé… » suffit */ }
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

  var tmpWav = "";
  var timingInfo = "";
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
      return backend.transcribe(wav, engine).then(function (r) {
        rmFile(wav);  // WAV inutile une fois transcrit
        timingInfo = r.timing || "";
        return r.words;
      });
    })
    .then(function (whisperWords) {
      if (!whisperWords || !whisperWords.length) {
        // Musique seule / silence : rien à sous-titrer (ce n'est pas une erreur).
        var info = new Error("Aucune parole détectée dans la séquence " +
          "(musique ou silence seul) : rien à sous-titrer.");
        info.isInfo = true;
        throw info;
      }
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
      if (result.correction && result.correction.error) { refreshLlm(true); }
      setStatus(
        result.cues.length + " sous-titres générés. " +
        correctionSummary(result.correction) +
        (timingInfo === "aligné" ? "Timing recalé sur la voix. "
          : timingInfo ? "Timing Whisper (recalage " + timingInfo + "). " : "") +
        "Track Style : " + result.track_style
      );
      $("insert").disabled = false;
    })
    .catch(function (e) {
      setBusy(false);
      setStatus((e.isInfo ? "" : "Erreur : ") + e.message);
    });
}

// Applique au tableau de mots de base les suggestions cochées, puis re-segmente
// côté backend et met à jour l'aperçu + le SRT à insérer.
function applyReview() {
  // Le backend applique les plages (et répartit le timing des mots nouveaux).
  var spans = Object.keys(review.accepted).map(function (k) {
    var sg = review.accepted[k];
    return { de: sg.index, a: sg.end_index, texte: sg.suggested };
  });

  setStatus("Mise à jour des sous-titres…");
  return backend.segment({
    media: review.media, words: review.words, spans: spans,
    max_chars_per_line: review.maxChars,
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
      if (cb.checked) { review.accepted[sg.index] = sg; }
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
    suggestions.forEach(function (sg) { review.accepted[sg.index] = sg; });
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
