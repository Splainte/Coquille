/*
 * Rendu d'un WAV de la séquence (zone In/Out) pour la transcription Whisper.
 *
 * ⚠ SPIKE À VALIDER : l'API encoder UXP (Encoder / exportSequence) et le format
 * de preset (.epr audio uniquement). On exporte un WAV mono 16 kHz suffisant pour
 * Whisper, dans le dossier temporaire du plugin, et on renvoie son chemin au backend.
 */

const ppro = require("premierepro");
const uxp = require("uxp");

/**
 * Exporte l'audio de la séquence active et renvoie le chemin du fichier WAV.
 * @returns {Promise<string>} chemin absolu du WAV
 */
async function renderSequenceAudio() {
  const project = await ppro.Project.getActiveProject();
  const sequence = await project.getActiveSequence();
  if (!sequence) {
    throw new Error("Aucune séquence active.");
  }

  const tmp = await uxp.storage.localFileSystem.getTemporaryFolder();
  const outFile = await tmp.createFile(`coquille-${Date.now()}.wav`, {
    overwrite: true,
  });

  // Chemin nominal (à confirmer pendant le spike) :
  //   const encoder = await ppro.EncoderManager.getManager();
  //   await encoder.exportSequence(sequence, outFile.nativePath, audioOnlyPreset, ...);
  //
  // À défaut d'API encoder stable, repli : demander à l'utilisateur d'exporter
  // l'audio (Fichier ▸ Exporter ▸ Média, WAV) puis pointer le fichier.
  throw new Error(
    "renderSequenceAudio : à implémenter pendant le spike encoder UXP. " +
      "Cible de sortie : " + outFile.nativePath
  );
}

module.exports = { renderSequenceAudio };
