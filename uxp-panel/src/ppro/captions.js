/*
 * Insertion des sous-titres dans la séquence.
 *
 * Méthode confirmée (UXP) : écrire le .srt sur disque → importFiles() → le
 * projectItem importé sert de source à createCaptionTrack() sur la séquence.
 * Le style de marque (Track Style) reste appliqué manuellement en 1 clic par le
 * monteur (l'API ne permet pas encore d'appliquer un style de caption).
 */

const ppro = require("premierepro");
const uxp = require("uxp");

/**
 * Écrit le SRT dans un fichier temporaire et renvoie son chemin natif.
 */
async function writeSrt(srtText) {
  const tmp = await uxp.storage.localFileSystem.getTemporaryFolder();
  const file = await tmp.createFile(`coquille-${Date.now()}.srt`, {
    overwrite: true,
  });
  await file.write(srtText, { format: uxp.storage.formats.utf8 });
  return file.nativePath;
}

/**
 * Importe le SRT et crée une piste de sous-titres dans la séquence active.
 * @param {string} srtText  contenu SRT généré par le backend
 * @param {number} startAtTime  temps de départ en secondes (souvent 0)
 */
async function insertCaptions(srtText, startAtTime = 0) {
  const srtPath = await writeSrt(srtText);

  const project = await ppro.Project.getActiveProject();
  const sequence = await project.getActiveSequence();
  if (!sequence) {
    throw new Error("Aucune séquence active.");
  }

  // 1. Importer le SRT comme projectItem.
  const imported = await project.importFiles(
    [srtPath],
    /* suppressUI */ true,
    /* targetBin */ await project.getRootItem(),
    /* importAsNumberedStills */ false
  );
  const projectItem = Array.isArray(imported) ? imported[0] : imported;
  if (!projectItem) {
    throw new Error("Échec de l'import du fichier SRT.");
  }

  // 2. Créer la piste de sous-titres à partir du projectItem.
  //    Signature : sequence.createCaptionTrack(projectItem, startAtTime, [captionFormat])
  const ok = await sequence.createCaptionTrack(projectItem, startAtTime);
  if (!ok) {
    throw new Error(
      "createCaptionTrack a échoué. Repli : glisser le SRT importé sur la timeline, " +
        "ou utiliser le script ExtendScript legacy-jsx/insert_captions.jsx."
    );
  }
  return true;
}

module.exports = { insertCaptions, writeSrt };
