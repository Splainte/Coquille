@echo off
REM Lance le backend Coquille (FastAPI/uvicorn) sur 127.0.0.1:8765.
cd /d "%~dp0backend"
if not exist ".venv\Scripts\activate.bat" (
  echo Environnement non installe. Lance d'abord setup-windows.bat
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
echo Backend Coquille sur http://127.0.0.1:8765  (Ctrl+C pour arreter)
python -m uvicorn coquille.app:app --host 127.0.0.1 --port 8765
