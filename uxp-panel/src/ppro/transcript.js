/*
 * Lecture du transcript natif de Premiere via l'API UXP.
 *
 * ⚠ SPIKE À VALIDER : les noms exacts (getTranscript / transcript export) varient
 * selon la version de Premiere. Cible : récupérer le JSON décrit dans
 * transcript_format_spec.json (mots horodatés + confiance). Le backend le parse
 * via load_premiere_transcript(), tolérant aux variantes de noms de champs.
 */

const ppro = require("premierepro");

/**
 * Tente de récupérer le transcript de la séquence active au format JSON Premiere.
 * Retourne null si aucun transcript n'est disponible (l'utilisateur devra lancer
 * la transcription native, ou on s'appuiera uniquement sur Whisper).
 */
async function getActiveSequenceTranscript() {
  const project = await ppro.Project.getActiveProject();
  const sequence = await project.getActiveSequence();
  if (!sequence) {
    throw new Error("Aucune séquence active.");
  }

  // Chemin nominal (à confirmer pendant le spike) :
  //   const transcript = await sequence.getTranscript();
  //   return transcript.toJSON();  // ou transcript.exportJSON()
  //
  // Repli robuste : exporter le transcript en fichier puis le relire (voir README).
  if (typeof sequence.getTranscript === "function") {
    const transcript = await sequence.getTranscript();
    if (transcript && typeof transcript.toJSON === "function") {
      return transcript.toJSON();
    }
    return transcript ?? null;
  }
  return null;
}

module.exports = { getActiveSequenceTranscript };
