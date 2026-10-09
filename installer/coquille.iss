; Coquille — installeur Windows en un seul fichier, sans droits admin.
; Compilé par GitHub Actions (.github/workflows/installer.yml), version injectée
; via /DAppVersion. Le dossier ..\stage est préparé par le workflow :
;   stage\app\python   Python autonome + dépendances du backend (faster-whisper…)
;   stage\app\backend  code du backend
;   stage\app\tools    stop-backend.ps1
;   stage\panel        panneau CEP
;
; Le panneau lance lui-même le backend (Python embarqué, fenêtre cachée) : plus
; de run-backend.bat à ouvrir. Les gros téléchargements (bibliothèques NVIDIA,
; PyTorch pour le recalage du timing, modèles) sont des tâches facultatives
; exécutées pendant l'installation, pour ne pas alourdir l'installeur.
; La clé OpenRouter se saisit sur une page dédiée (facultative).

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define PanelDir "{userappdata}\Adobe\CEP\extensions\com.coquille.subtitles"

[Setup]
AppId={{C0C4DDDC-637E-4626-ADA0-3B9D92E238D5}
AppName=Coquille
AppVersion={#AppVersion}
AppPublisher=Splainte
AppPublisherURL=https://github.com/Splainte/Coquille
UninstallDisplayName=Coquille (sous-titres Premiere Pro)
; Tout vit dans le profil utilisateur (AppData + HKCU) → pas d'élévation.
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DefaultDirName={localappdata}\Coquille
DisableDirPage=yes
DisableProgramGroupPage=yes
OutputDir=Output
OutputBaseFilename=Coquille-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Premiere peut rester ouvert : il suffit de le redémarrer après l'installation.
CloseApplications=no

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Messages]
FinishedLabel=Coquille est installé.%n%nRedémarrez Premiere Pro puis ouvrez Fenêtre > Extensions > Coquille — Sous-titres. Le moteur de transcription démarre tout seul à l'ouverture du panneau.

[Tasks]
Name: "gpu"; Description: "Transcription sur la carte graphique NVIDIA : 10 à 20 fois plus rapide et modèle plus précis (téléchargement d'environ 1,3 Go)"; Check: HasNvidia
Name: "align"; Description: "Timing des sous-titres calé précisément sur la voix (téléchargement d'environ {code:AlignSize})"
Name: "models"; Description: "Télécharger les modèles maintenant (sinon au premier sous-titrage, ce qui le rend très long)"

[InstallDelete]
; Code du backend et panneau : repartent propres à chaque mise à jour. Le Python
; embarqué est conservé (les paquets ajoutés par les tâches ci-dessus pèsent
; plusieurs Go) et .env (clé OpenRouter) n'est jamais touché.
Type: filesandordirs; Name: "{app}\backend\coquille"
Type: filesandordirs; Name: "{#PanelDir}"

[Files]
Source: "..\stage\app\tools\stop-backend.ps1"; Flags: dontcopy
Source: "..\stage\app\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "..\stage\panel\*"; DestDir: "{#PanelDir}"; Flags: recursesubdirs createallsubdirs ignoreversion

[UninstallDelete]
; Les paquets ajoutés après coup (NVIDIA, PyTorch) et .env : on nettoie tout.
Type: filesandordirs; Name: "{app}"
Type: filesandordirs; Name: "{#PanelDir}"

[Registry]
; Panneaux CEP non signés : PlayerDebugMode pour toutes les versions CSXS visées.
Root: HKCU; Subkey: "Software\Adobe\CSXS.9"; ValueType: string; ValueName: "PlayerDebugMode"; ValueData: "1"
Root: HKCU; Subkey: "Software\Adobe\CSXS.10"; ValueType: string; ValueName: "PlayerDebugMode"; ValueData: "1"
Root: HKCU; Subkey: "Software\Adobe\CSXS.11"; ValueType: string; ValueName: "PlayerDebugMode"; ValueData: "1"
Root: HKCU; Subkey: "Software\Adobe\CSXS.12"; ValueType: string; ValueName: "PlayerDebugMode"; ValueData: "1"
Root: HKCU; Subkey: "Software\Adobe\CSXS.13"; ValueType: string; ValueName: "PlayerDebugMode"; ValueData: "1"

[Code]
const
  PipFlags = '-m pip install --no-warn-script-location --disable-pip-version-check ';

var
  KeyPage: TInputQueryWizardPage;
  KeyNote: TNewStaticText;
  KeyValue: String;

function SetEnvironmentVariable(lpName, lpValue: String): BOOL;
  external 'SetEnvironmentVariableW@kernel32.dll stdcall';

// Le pilote NVIDIA installe nvcuda.dll dans System32.
function HasNvidia: Boolean;
begin
  Result := FileExists(ExpandConstant('{sys}\nvcuda.dll'));
end;

function AlignSize(Param: String): String;
begin
  if HasNvidia then Result := '2,5 Go' else Result := '250 Mo';
end;

// --- Page « Clé API OpenRouter » ---------------------------------------------

// Nettoie une clé collée : espaces, guillemets, « OPENROUTER_API_KEY= », « Bearer ».
function CleanKey(S: String): String;
begin
  S := Trim(S);
  StringChangeEx(S, '"', '', True);
  StringChangeEx(S, '''', '', True);
  if Pos('OPENROUTER_API_KEY=', S) = 1 then Delete(S, 1, Length('OPENROUTER_API_KEY='));
  if Pos('Bearer ', S) = 1 then Delete(S, 1, Length('Bearer '));
  Result := Trim(S);
end;

function LooksLikeKey(S: String): Boolean;
var
  I: Integer;
  C: Char;
begin
  Result := (Length(S) >= 20) and (Length(S) <= 200);
  for I := 1 to Length(S) do begin
    C := S[I];
    if not (((C >= 'a') and (C <= 'z')) or ((C >= 'A') and (C <= 'Z')) or
            ((C >= '0') and (C <= '9')) or (C = '-') or (C = '_')) then Result := False;
  end;
end;

// Code HTTP d'OpenRouter pour cette clé (GET /key, gratuit) ; 0 = injoignable.
function CheckKey(Key: String): Integer;
var
  Http: Variant;
begin
  Result := 0;
  try
    Http := CreateOleObject('WinHttp.WinHttpRequest.5.1');
    Http.SetTimeouts(5000, 5000, 10000, 10000);
    Http.Open('GET', 'https://openrouter.ai/api/v1/key', False);
    Http.SetRequestHeader('Authorization', 'Bearer ' + Key);
    Http.Send('');
    Result := Http.Status;
  except
    Result := 0;
  end;
end;

function HasSavedKey: Boolean;
var
  Lines: TArrayOfString;
  I: Integer;
begin
  Result := False;
  if LoadStringsFromFile(ExpandConstant('{localappdata}\Coquille\backend\.env'), Lines) then
    for I := 0 to GetArrayLength(Lines) - 1 do
      if (Pos('OPENROUTER_API_KEY=', Trim(Lines[I])) = 1) and
         (Length(Trim(Lines[I])) > Length('OPENROUTER_API_KEY=')) then Result := True;
end;

procedure KeyLinkClick(Sender: TObject);
var
  Code: Integer;
begin
  ShellExec('open', 'https://openrouter.ai/settings/keys', '', '', SW_SHOWNORMAL, ewNoWait, Code);
end;

procedure InitializeWizard;
var
  Link: TNewStaticText;
begin
  KeyPage := CreateInputQueryPage(wpSelectTasks,
    'Clé API OpenRouter',
    'Correction des sous-titres par IA (facultatif)',
    'Coquille peut proposer des corrections par IA (noms de marques, jargon, homophones) ' +
    'grâce à OpenRouter. Collez votre clé ci-dessous, ou laissez vide pour passer cette ' +
    'étape : vous pourrez l''ajouter plus tard depuis le panneau (bouton « Ajouter la clé »).');
  KeyPage.Add('Clé OpenRouter (sk-or-v1-…) :', True);
  KeyPage.Values[0] := ExpandConstant('{param:KEY|}');

  Link := TNewStaticText.Create(KeyPage);
  Link.Parent := KeyPage.Surface;
  Link.Top := KeyPage.Edits[0].Top + KeyPage.Edits[0].Height + ScaleY(10);
  Link.Caption := 'Créer une clé sur openrouter.ai';
  Link.Font.Style := [fsUnderline];
  Link.Font.Color := clHotLight;
  Link.Cursor := crHand;
  Link.OnClick := @KeyLinkClick;

  KeyNote := TNewStaticText.Create(KeyPage);
  KeyNote.Parent := KeyPage.Surface;
  KeyNote.Top := Link.Top + Link.Height + ScaleY(14);
  KeyNote.Width := KeyPage.SurfaceWidth;
  KeyNote.WordWrap := True;
  if HasSavedKey then
    KeyNote.Caption := 'Une clé est déjà enregistrée sur ce PC : laissez vide pour la conserver.'
  else
    KeyNote.Caption := 'La clé est vérifiée auprès d''OpenRouter puis enregistrée uniquement sur ce PC.';
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Status: Integer;
begin
  Result := True;
  if CurPageID <> KeyPage.ID then Exit;
  KeyValue := CleanKey(KeyPage.Values[0]);
  if KeyValue = '' then Exit;  // étape passée
  // Installation silencieuse (/KEY=…) : personne pour répondre à une question.
  // On n'interroge pas OpenRouter ; une clé mal formée est ignorée (le panneau
  // permettra de l'ajouter).
  if WizardSilent then begin
    if not LooksLikeKey(KeyValue) then begin
      Log('Clé OpenRouter ignorée : format invalide.');
      KeyValue := '';
      KeyPage.Values[0] := '';
    end;
    Exit;
  end;
  if not LooksLikeKey(KeyValue) then begin
    MsgBox('Ce texte ne ressemble pas à une clé OpenRouter (elle commence par sk-or-v1-).' + #13#10 +
      'Corrigez-la, ou videz le champ pour passer cette étape.', mbError, MB_OK);
    Result := False;
    Exit;
  end;
  WizardForm.NextButton.Enabled := False;
  try
    Status := CheckKey(KeyValue);
  finally
    WizardForm.NextButton.Enabled := True;
  end;
  if (Status = 401) or (Status = 403) then begin
    MsgBox('OpenRouter refuse cette clé. Vérifiez-la (copiez-la en entier), ' +
      'ou videz le champ pour passer cette étape.', mbError, MB_OK);
    Result := False;
  end else if Status <> 200 then
    Result := MsgBox('Impossible de vérifier la clé (pas d''accès à Internet ?).' + #13#10 +
      'L''enregistrer quand même ?', mbConfirmation, MB_YESNO) = IDYES;
end;

// --- Installation --------------------------------------------------------------

procedure StopBackend;
var
  Code: Integer;
  Script: String;
begin
  Script := ExpandConstant('{tmp}\stop-backend.ps1');
  if not FileExists(Script) then Script := ExpandConstant('{app}\tools\stop-backend.ps1');
  if FileExists(Script) then
    Exec('powershell.exe', '-NoProfile -ExecutionPolicy Bypass -File "' + Script + '"', '',
      SW_HIDE, ewWaitUntilTerminated, Code);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  Result := '';
  // Un backend lancé verrouille les fichiers de Python : on l'arrête.
  ExtractTemporaryFile('stop-backend.ps1');
  StopBackend;
  // Ancienne installation par setup-windows.bat : lien « Coquille » vers le
  // dépôt, même identifiant de panneau → doublon dans Premiere. rmdir sans /s
  // retire le lien sans toucher à sa cible (et échoue sur un vrai dossier plein).
  Exec(ExpandConstant('{cmd}'), '/c rmdir "' + ExpandConstant('{userappdata}\Adobe\CEP\extensions\Coquille') + '"',
    '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

procedure OnPythonLog(const S: String; const Error, FirstLine: Boolean);
var
  Line: String;
begin
  Line := Trim(S);
  if Line = '' then Exit;
  if Length(Line) > 90 then Line := Copy(Line, 1, 87) + '…';
  WizardForm.FilenameLabel.Caption := Line;
end;

// Lance le Python embarqué ; un échec n'interrompt pas l'installation.
function RunPython(const Caption, Params, Failure: String): Boolean;
var
  Code: Integer;
begin
  WizardForm.StatusLabel.Caption := Caption;
  WizardForm.FilenameLabel.Caption := '';
  Result := ExecAndLogOutput(ExpandConstant('{app}\python\python.exe'), Params,
    ExpandConstant('{app}\backend'), SW_HIDE, ewWaitUntilTerminated, Code, @OnPythonLog) and (Code = 0);
  if not Result then
    SuppressibleMsgBox(Failure + #13#10#13#10 + 'Coquille fonctionne quand même. Relancez l''installeur ' +
      'plus tard pour réessayer.', mbInformation, MB_OK, IDOK);
end;

function JsPath(S: String): String;
begin
  StringChangeEx(S, '\', '\\', True);
  StringChangeEx(S, '"', '\"', True);
  Result := S;
end;

procedure WritePanelConfig;
var
  Lines: TArrayOfString;
begin
  SetArrayLength(Lines, 7);
  Lines[0] := '// Généré par l''installeur Coquille : le panneau lance lui-même le moteur.';
  Lines[1] := 'window.COQUILLE = {';
  Lines[2] := '  backendPython: "' + JsPath(ExpandConstant('{app}\python\pythonw.exe')) + '",';
  Lines[3] := '  backendDir: "' + JsPath(ExpandConstant('{app}\backend')) + '",';
  Lines[4] := '  backendLog: "' + JsPath(ExpandConstant('{app}\backend.log')) + '"';
  Lines[5] := '};';
  Lines[6] := '';
  SaveStringsToUTF8File(ExpandConstant('{#PanelDir}\js\config.local.js'), Lines, False);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Index: String;
begin
  if CurStep <> ssPostInstall then Exit;
  WritePanelConfig;

  // Installation silencieuse (/KEY=…) : la page n'a pas été validée.
  if KeyValue = '' then KeyValue := CleanKey(KeyPage.Values[0]);
  if KeyValue <> '' then begin
    // Passée par l'environnement, jamais en argument (liste des processus).
    SetEnvironmentVariable('COQUILLE_SETUP_KEY', KeyValue);
    RunPython('Enregistrement de la clé OpenRouter…', '-m coquille.llm_key',
      'La clé OpenRouter n''a pas pu être enregistrée : ajoutez-la depuis le panneau (bouton « Ajouter la clé »).');
    SetEnvironmentVariable('COQUILLE_SETUP_KEY', '');
  end;

  WizardForm.ProgressGauge.Style := npbstMarquee;
  try
    if WizardIsTaskSelected('gpu') then
      RunPython('Bibliothèques NVIDIA (environ 1,3 Go)…',
        PipFlags + 'nvidia-cublas-cu12 nvidia-cudnn-cu12 nvidia-cuda-runtime-cu12',
        'Les bibliothèques NVIDIA n''ont pas pu être installées : la transcription se fera sur le processeur.');
    if WizardIsTaskSelected('align') then begin
      if HasNvidia then Index := 'cu128' else Index := 'cpu';
      RunPython('Recalage du timing : PyTorch (' + AlignSize('') + ')…',
        PipFlags + '-r requirements-align.txt --index-url https://download.pytorch.org/whl/' + Index +
        ' --extra-index-url https://pypi.org/simple',
        'Le recalage du timing n''a pas pu être installé : Coquille utilisera le timing de Whisper.');
    end;
    if WizardIsTaskSelected('models') then
      RunPython('Téléchargement des modèles (plusieurs Go)…', '-m coquille.prefetch',
        'Un modèle n''a pas pu être téléchargé : il le sera au premier sous-titrage.');
  finally
    WizardForm.ProgressGauge.Style := npbstNormal;
  end;
end;

function InitializeUninstall: Boolean;
begin
  StopBackend;
  Result := True;
end;
