@echo off
setlocal enabledelayedexpansion

REM ============================================================
REM  Coquille - installation tout-en-un (Windows)
REM  - active PlayerDebugMode (registre, plusieurs versions CEP)
REM  - lie le panneau dans %APPDATA%\Adobe\CEP\extensions
REM  - cree config.local.js si absent
REM  - prepare le backend Python (venv + dependances)
REM  Necessite les droits admin (mklink + registre) : auto-elevation.
REM ============================================================

REM --- Auto-elevation administrateur ---
net session >nul 2>&1
if %errorLevel% neq 0 (
  echo Elevation des privileges administrateur...
  powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

set "ROOT=%~dp0"
set "PANEL=%ROOT%cep-panel"
set "CEP_DIR=%APPDATA%\Adobe\CEP\extensions"

echo ============================================
echo  Coquille - installation CEP (Windows)
echo ============================================

REM --- 1. PlayerDebugMode ---
echo [1/4] Activation du mode debug CEP (registre)...
for %%V in (9 10 11 12) do (
  reg add "HKCU\Software\Adobe\CSXS.%%V" /v PlayerDebugMode /t REG_SZ /d 1 /f >nul
)

REM --- 2. Lien symbolique du panneau ---
echo [2/4] Lien du panneau dans %CEP_DIR% ...
if not exist "%CEP_DIR%" mkdir "%CEP_DIR%"
if exist "%CEP_DIR%\Coquille" rmdir "%CEP_DIR%\Coquille"
mklink /D "%CEP_DIR%\Coquille" "%PANEL%"
if errorlevel 1 (
  echo   ERREUR : creation du lien echouee. Lance ce script en administrateur.
  pause
  exit /b 1
)

REM --- 3. config.local.js ---
echo [3/4] Config machine...
if not exist "%PANEL%\js\config.local.js" (
  copy "%PANEL%\js\config.local.example.js" "%PANEL%\js\config.local.js" >nul
  echo   -^> config.local.js cree : EDITE-LE pour renseigner audioPresetPath (.epr^)
) else (
  echo   -^> config.local.js deja present, inchange
)

REM --- 4. Backend Python ---
echo [4/4] Backend Python (venv + dependances)...
where py >nul 2>&1 && (set "PY=py") || (set "PY=python")
pushd "%ROOT%backend"
%PY% -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul
pip install -r requirements.txt
echo   Libs CUDA 12 (cuBLAS/cuDNN) pour le GPU...
pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
popd

echo.
echo ============================================
echo  Termine.
echo  1^) Edite cep-panel\js\config.local.js (audioPresetPath^)
echo  2^) Lance run-backend.bat
echo  3^) Redemarre Premiere : Fenetre ^> Extensions ^> Coquille
echo.
echo  Si erreur CUDA au 1er rendu (4070/4090^), installe les libs GPU :
echo     backend\.venv\Scripts\activate.bat
echo     pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
echo ============================================
pause
