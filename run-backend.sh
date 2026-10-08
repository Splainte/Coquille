#!/usr/bin/env bash
#
# Coquille — lance le backend local (FastAPI) sur http://127.0.0.1:8765.
# À garder ouvert dans un terminal pendant l'utilisation du panneau.
#
# Correction LLM (optionnelle) : exporte OPENROUTER_API_KEY avant de lancer,
# sinon coche « Mode hors-ligne » dans le panneau (glossaire seul).
#
#   export OPENROUTER_API_KEY=sk-or-...      (ou dans backend/.env)
#   bash run-backend.sh
#
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO/backend"

if [[ ! -d .venv ]]; then
  echo "venv absent — lance d'abord :  bash \"$REPO/setup-mac.sh\"" >&2
  exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate
echo "Backend Coquille → http://127.0.0.1:8765  (Ctrl+C pour arrêter)"
exec uvicorn coquille.app:app --host 127.0.0.1 --port 8765
