#!/usr/bin/env bash
#
# Coquille — installation tout-en-un pour macOS (dev/test).
#
# Fait, de façon idempotente (re-lançable sans risque) :
#   1. active le mode debug CEP (panneau non signé autorisé)
#   2. lie le panneau dans le dossier des extensions CEP
#   3. crée le venv Python + installe les dépendances du backend
#
# Usage :  bash setup-mac.sh
#
set -euo pipefail

# Répertoire du repo = dossier de ce script.
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Petits helpers d'affichage.
ok()   { printf "  \033[32m✓\033[0m %s\n" "$1"; }
info() { printf "\033[1m▸ %s\033[0m\n" "$1"; }
warn() { printf "  \033[33m!\033[0m %s\n" "$1"; }

# --- Garde-fou : macOS uniquement -------------------------------------------
if [[ "$(uname)" != "Darwin" ]]; then
  echo "Ce script est prévu pour macOS. Système détecté : $(uname)." >&2
  exit 1
fi

echo
info "Coquille — installation macOS"
echo "  Repo : $REPO"
echo

# --- 1. Mode debug CEP ------------------------------------------------------
info "1/3  Activation du mode debug CEP"
for v in 10 11 12; do
  defaults write "com.adobe.CSXS.$v" PlayerDebugMode 1 2>/dev/null || true
done
killall cfprefsd 2>/dev/null || true
ok "PlayerDebugMode activé (CSXS 10/11/12)"

# --- 2. Lien du panneau dans les extensions CEP -----------------------------
info "2/3  Installation du panneau CEP"
EXT_DIR="$HOME/Library/Application Support/Adobe/CEP/extensions"
LINK="$EXT_DIR/com.coquille.subtitles"
mkdir -p "$EXT_DIR"
ln -sfn "$REPO/cep-panel" "$LINK"
ok "Panneau lié : $LINK → cep-panel/"

# --- 3. Backend Python ------------------------------------------------------
info "3/3  Backend Python (venv + dépendances)"
if ! command -v python3 >/dev/null 2>&1; then
  echo "  python3 introuvable. Installe Python 3.10+ puis relance." >&2
  exit 1
fi
cd "$REPO/backend"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
  ok "venv créé (backend/.venv)"
else
  ok "venv déjà présent"
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python3 -m pip install --quiet --upgrade pip
python3 -m pip install --quiet -r requirements.txt
ok "dépendances installées"
# Recalage précis du timing des mots (PyTorch, optionnel : repli sur Whisper).
if python3 -m pip install --quiet -r requirements-align.txt; then
  ok "recalage du timing installé"
else
  warn "recalage du timing non installé (timing Whisper utilisé)"
fi

# Vérif rapide : la suite de tests passe ?
if python3 -m unittest discover -s tests >/dev/null 2>&1; then
  ok "tests backend : OK"
else
  warn "les tests backend n'ont pas tous passé (à vérifier plus tard)"
fi

# --- Récapitulatif final ----------------------------------------------------
echo
printf "\033[1m✅ Installation automatique terminée.\033[0m\n"
cat <<EOF

Pour lancer le backend (à garder ouvert dans un terminal) :

    bash "$REPO/run-backend.sh"

Puis dans Premiere Pro :

  1. Ouvre un projet avec une séquence active.
  2. Menu  Fenêtre ▸ Extensions ▸ « Coquille ».
  3. Choisis le média, clique « 1. Générer les sous-titres ».

Correction IA : mets ta clé OpenRouter dans backend/.env (modèle : backend/.env.example).
Le préréglage audio WAV est celui livré avec Premiere, trouvé automatiquement.

EOF
