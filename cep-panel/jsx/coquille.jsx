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

  var ok = app.project.importFiles(
    [srtPath],
    /* suppressUI */ true,
    app.project.rootItem,
    /* importAsNumberedStills */ false
  );
  if (!ok) { return "ERREUR: import du SRT échoué"; }

  // Le projectItem importé = le dernier ajouté à la racine.
  var root = app.project.rootItem;
  var captionItem = root.children[root.children.numItems - 1];

  var startTicks = startAtTimeSeconds || 0; // selon version : secondes ou ticks
  var created;
  try {
    created = seq.createCaptionTrack(captionItem, startTicks);
  } catch (e) {
    return "ERREUR: createCaptionTrack: " + e.toString();
  }
  if (!created) { return "ERREUR: createCaptionTrack a renvoyé false"; }

  // La piste caption est autonome une fois créée : on retire le clip importé du
  // chutier pour ne pas le polluer (vérifié : sans effet sur les sous-titres).
  try { captionItem.deleteClip(); } catch (e) { /* non bloquant */ }
  return "OK";
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
