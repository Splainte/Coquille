/*
 * Coquille — repli ExtendScript (CEP) pour l'insertion de sous-titres.
 *
 * À n'utiliser QUE si sequence.createCaptionTrack() côté UXP s'avère instable.
 * Importe un .srt et crée une piste de sous-titres dans la séquence active.
 *
 * Usage (depuis un panneau CEP) :
 *   csInterface.evalScript('coquilleInsertCaptions("' + srtPath + '", 0)');
 */

function coquilleInsertCaptions(srtPath, startAtTimeSeconds) {
  if (!app.project) {
    return "ERREUR: aucun projet ouvert";
  }
  var seq = app.project.activeSequence;
  if (!seq) {
    return "ERREUR: aucune séquence active";
  }

  // 1. Importer le SRT.
  var before = app.project.rootItem.children.numItems;
  var ok = app.project.importFiles(
    [srtPath],
    /* suppressUI */ true,
    app.project.rootItem,
    /* importAsNumberedStills */ false
  );
  if (!ok) {
    return "ERREUR: import du SRT échoué";
  }

  // Retrouver le projectItem fraîchement importé (le dernier ajouté).
  var root = app.project.rootItem;
  var captionItem = root.children[root.children.numItems - 1];

  // 2. Créer la piste de sous-titres.
  var startTicks = startAtTimeSeconds || 0; // selon version : secondes ou ticks
  var created = seq.createCaptionTrack(captionItem, startTicks);
  return created ? "OK" : "ERREUR: createCaptionTrack a renvoyé false";
}
