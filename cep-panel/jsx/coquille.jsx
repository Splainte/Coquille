/*
 * Coquille — ExtendScript (côté hôte Premiere) du panneau CEP.
 *
 * Exposé au panneau via window.__adobe_cep__.evalScript(...).
 * Trois fonctions = les trois points du spike :
 *   1. coquilleExportAudio  — rendu WAV de la séquence (pour Whisper)         [exportAsMediaDirect]
 *   2. coquilleInsertCaptions — import .srt + création de la piste caption     [createCaptionTrack]
 *   3. coquilleProbe        — sonde des capacités API à l'exécution (diag)
 *
 * Convention de retour : chaîne préfixée "OK" ou "ERREUR: ...". Les fonctions
 * renvoyant des données utiles renvoient "OK:<payload>".
 */

// --- 1. Rendu audio de la séquence active -----------------------------------
// presetPath = chemin d'un .epr audio (WAV). workAreaType : 0=séquence entière,
// 1=in→out, 2=zone de travail. exportAsMediaDirect est synchrone côté script.
function coquilleExportAudio(outputPath, presetPath, workAreaType) {
  if (!app.project) { return "ERREUR: aucun projet ouvert"; }
  var seq = app.project.activeSequence;
  if (!seq) { return "ERREUR: aucune séquence active"; }
  if (!presetPath) { return "ERREUR: presetPath (.epr audio WAV) manquant"; }

  var wat = (workAreaType === undefined || workAreaType === null) ? 0 : workAreaType;
  try {
    // Retourne le chemin du fichier rendu, ou lève si échec.
    seq.exportAsMediaDirect(outputPath, presetPath, wat);
  } catch (e) {
    return "ERREUR: exportAsMediaDirect: " + e.toString();
  }
  return "OK:" + outputPath;
}

// --- 2. Insertion des sous-titres (import .srt + piste caption) --------------
function coquilleInsertCaptions(srtPath, startAtTimeSeconds) {
  if (!app.project) { return "ERREUR: aucun projet ouvert"; }
  var seq = app.project.activeSequence;
  if (!seq) { return "ERREUR: aucune séquence active"; }

  var root = app.project.rootItem;
  var ok = app.project.importFiles(
    [srtPath],
    /* suppressUI */ true,
    root,
    /* importAsNumberedStills */ false
  );
  if (!ok) { return "ERREUR: import du SRT échoué"; }

  // Retrouver le projectItem importé par son chemin média (plus fiable que
  // « le dernier enfant » : l'ordre du chutier n'est pas garanti).
  var wanted = String(srtPath).replace(/\\/g, "/");
  var captionItem = null;
  for (var i = root.children.numItems - 1; i >= 0; i--) {
    var it = root.children[i];
    var mp = "";
    try { mp = it.getMediaPath(); } catch (e) {}
    if (mp && mp.replace(/\\/g, "/") === wanted) { captionItem = it; break; }
  }
  if (!captionItem) { captionItem = root.children[root.children.numItems - 1]; }

  var startTicks = startAtTimeSeconds || 0; // selon version : secondes ou ticks
  var created;
  try {
    created = seq.createCaptionTrack(captionItem, startTicks);
  } catch (e) {
    return "ERREUR: createCaptionTrack: " + e.toString();
  }
  if (!created) { return "ERREUR: createCaptionTrack a renvoyé false"; }

  // La piste caption est autonome une fois créée : on retire le clip importé du
  // chutier. L'API ne permet PAS de supprimer un projectItem directement
  // (deleteClip n'existe pas), mais deleteBin() supprime un bin ET son contenu.
  // Contournement : bin tampon → y déplacer le clip → supprimer le bin.
  // On rapporte le résultat ("OK:1" retiré, "OK:0:<raison>" sinon) pour que le
  // panneau ne supprime le fichier que si le clip a bien quitté le chutier.
  try {
    var trash = root.createBin("__coquille_tmp__");
    if (!trash) { return "OK:0:createBin a échoué"; }
    captionItem.moveBin(trash);
    trash.deleteBin();
  } catch (e) {
    return "OK:0:" + e.toString();
  }
  return "OK:1";
}

// --- Déplacement de la tête de lecture (lien « localiser » du panneau) -------
// Premiere compte le temps en ticks : 254016000000 ticks = 1 seconde.
// setPlayerPosition attend une chaîne de ticks.
function coquilleSeek(seconds) {
  if (!app.project) { return "ERREUR: aucun projet ouvert"; }
  var seq = app.project.activeSequence;
  if (!seq) { return "ERREUR: aucune séquence active"; }
  var ticks = Math.round((Number(seconds) || 0) * 254016000000);
  try {
    seq.setPlayerPosition(ticks.toString());
  } catch (e) {
    return "ERREUR: setPlayerPosition: " + e.toString();
  }
  return "OK:" + seconds;
}

// --- 3. Sonde de capacités (diagnostic du spike) ----------------------------
// Renvoie un JSON décrivant ce qui est réellement disponible dans CET hôte.
function coquilleProbe() {
  var r = {
    hasProject: !!app.project,
    hasActiveSequence: !!(app.project && app.project.activeSequence),
    hasImportFiles: !!(app.project && typeof app.project.importFiles === "function"),
    hasCreateCaptionTrack: false,
    hasExportAsMediaDirect: false,
    appVersion: (typeof app.version !== "undefined") ? app.version : "?"
  };
  var seq = app.project ? app.project.activeSequence : null;
  if (seq) {
    r.hasCreateCaptionTrack = (typeof seq.createCaptionTrack === "function");
    r.hasExportAsMediaDirect = (typeof seq.exportAsMediaDirect === "function");
  }
  // Sérialisation manuelle (ExtendScript n'a pas JSON.stringify partout).
  return "OK:{" +
    '"hasProject":' + r.hasProject + "," +
    '"hasActiveSequence":' + r.hasActiveSequence + "," +
    '"hasImportFiles":' + r.hasImportFiles + "," +
    '"hasCreateCaptionTrack":' + r.hasCreateCaptionTrack + "," +
    '"hasExportAsMediaDirect":' + r.hasExportAsMediaDirect + "," +
    '"appVersion":"' + r.appVersion + '"}';
}
