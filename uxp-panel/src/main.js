/*
 * Orchestration de l'UI du panneau Coquille.
 *
 * Flux :
 *   1. Générer : récupère le transcript Premiere (timecodes) + rend l'audio →
 *      Whisper (backend) → /process (alignement + correction + segmentation).
 *   2. Révision : l'utilisateur relit/édite les cues (les faibles confiances sont
 *      surlignées). [Édition manuelle = TODO : réinjecter dans le SRT.]
 *   3. Insérer : importFiles(.srt) + createCaptionTrack().
 */

const backend = require("./api/backend");
const captions = require("./ppro/captions");
const audio = require("./ppro/audio");
const transcript = require("./ppro/transcript");

const $ = (id) => document.getElementById(id);
const setStatus = (msg) => ($("status").textContent = msg);

let lastResult = null; // dernière réponse /process (contient le SRT)

async function init() {
  try {
    const presets = await backend.getPresets();
    const sel = $("media");
    for (const p of presets) {
      const opt = document.createElement("option");
      opt.value = p.media;
      opt.textContent = p.label;
      sel.appendChild(opt);
    }
    setStatus("Prêt.");
  } catch (e) {
    setStatus("Backend injoignable (lancez le serveur sur :8765). " + e.message);
  }

  $("generate").addEventListener("click", onGenerate);
  $("insert").addEventListener("click", onInsert);
}

async function onGenerate() {
  $("insert").disabled = true;
  lastResult = null;
  const media = $("media").value;
  const offline = $("offline").checked;

  try {
    // 1a. Transcript Premiere (timecodes de référence). Optionnel.
    setStatus("Lecture du transcript Premiere…");
    let premiereTranscript = null;
    try {
      premiereTranscript = await transcript.getActiveSequenceTranscript();
    } catch (e) {
      setStatus("Pas de transcript Premiere (" + e.message + "), on tentera Whisper seul.");
    }

    // 1b. Audio + Whisper (texte précis). Optionnel si le spike audio n'est pas prêt.
    let whisperWords = null;
    try {
      setStatus("Rendu audio de la séquence…");
      const wavPath = await audio.renderSequenceAudio();
      setStatus("Transcription Whisper en cours…");
      whisperWords = await backend.transcribe(wavPath, { language: "fr" });
    } catch (e) {
      setStatus("Whisper indisponible (" + e.message + "), on utilise le transcript Premiere.");
    }

    if (!premiereTranscript && !whisperWords) {
      setStatus("Aucune source disponible (ni transcript Premiere, ni Whisper).");
      return;
    }

    // 1c. Pipeline backend.
    setStatus("Alignement, correction et segmentation…");
    lastResult = await backend.process({
      media,
      whisper_words: whisperWords,
      premiere_transcript: premiereTranscript,
      offline,
    });

    renderCues(lastResult.cues, lastResult.words);
    setStatus(`${lastResult.cues.length} sous-titres générés. Track Style : ${lastResult.track_style}`);
    $("insert").disabled = false;
  } catch (e) {
    setStatus("Erreur : " + e.message);
  }
}

function renderCues(cues, words) {
  const container = $("cues");
  container.innerHTML = "";
  for (const c of cues) {
    const div = document.createElement("div");
    div.className = "cue";
    const tc = document.createElement("div");
    tc.className = "tc";
    tc.textContent = `#${c.index}  ${c.start.toFixed(2)} → ${c.end.toFixed(2)}  (CPS ${c.cps})`;
    const ta = document.createElement("textarea");
    ta.rows = c.lines.length;
    ta.value = c.lines.join("\n");
    div.appendChild(tc);
    div.appendChild(ta);
    container.appendChild(div);
  }
}

async function onInsert() {
  if (!lastResult) return;
  try {
    setStatus("Insertion de la piste de sous-titres…");
    await captions.insertCaptions(lastResult.srt, 0);
    setStatus(
      `Piste insérée. Appliquez le Track Style « ${lastResult.track_style} » sur la piste de sous-titres.`
    );
  } catch (e) {
    setStatus("Erreur d'insertion : " + e.message);
  }
}

init();
