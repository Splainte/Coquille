/*
 * Client HTTP vers le backend local Coquille (FastAPI).
 * Le backend tourne sur les machines maison ; il détient la clé OpenRouter et
 * les glossaires (jamais exposés au panneau).
 */

const BASE_URL = "http://127.0.0.1:8765";

async function getPresets() {
  const r = await fetch(`${BASE_URL}/presets`);
  if (!r.ok) throw new Error(`/presets: HTTP ${r.status}`);
  return (await r.json()).presets;
}

/**
 * Transcrit un WAV via WhisperX sur le backend.
 * @returns {Promise<Array>} liste de mots horodatés
 */
async function transcribe(audioPath, { language = "fr", initialPrompt = null } = {}) {
  const r = await fetch(`${BASE_URL}/transcribe`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ audio_path: audioPath, language, initial_prompt: initialPrompt }),
  });
  if (!r.ok) throw new Error(`/transcribe: HTTP ${r.status}`);
  return (await r.json()).words;
}

/**
 * Pipeline complet : alignement + correction + segmentation → SRT + cues.
 * @param {object} payload { media, whisper_words?, premiere_transcript?, offline? }
 */
async function process(payload) {
  const r = await fetch(`${BASE_URL}/process`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) {
    const detail = await r.text();
    throw new Error(`/process: HTTP ${r.status} ${detail}`);
  }
  return await r.json(); // { srt, cues, words, track_style }
}

module.exports = { getPresets, transcribe, process };
