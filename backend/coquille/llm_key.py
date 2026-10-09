"""Clé OpenRouter : état, vérification et enregistrement depuis le panneau.

La clé vit dans ``backend/.env`` (non versionné). Le panneau peut la saisir sans
que le monteur ait à ouvrir le dossier : on réécrit seulement la ligne
``OPENROUTER_API_KEY=`` du fichier (le reste est conservé) et on met à jour la
valeur en mémoire, prise en compte dès la correction suivante (pas de
redémarrage). La clé complète n'est JAMAIS renvoyée au panneau : seulement une
forme masquée (``sk-or-v1-…a1b2``).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from . import config

ENV_KEY = "OPENROUTER_API_KEY"
_KEY_RE = re.compile(r"^[A-Za-z0-9_\-]{20,200}$")


def mask(key: str) -> str:
    if not key:
        return ""
    head = "sk-or-v1-" if key.startswith("sk-or-v1-") else key[:3]
    return f"{head}…{key[-4:]}"


def clean(key: str) -> str:
    """Nettoie une clé collée : espaces, guillemets, « Bearer », ligne KEY=…"""
    k = (key or "").strip().strip('"').strip("'").strip()
    if k.startswith(ENV_KEY + "="):
        k = k.split("=", 1)[1].strip().strip('"').strip("'")
    if k.lower().startswith("bearer "):
        k = k[7:].strip()
    return k


def looks_valid(key: str) -> bool:
    return bool(_KEY_RE.match(key))


def verify(key: str, timeout: float = 10.0) -> dict:
    """Interroge OpenRouter (GET /key, gratuit). Renvoie
    {"state": "ok"|"invalide"|"injoignable", "detail": str, "label", "limit_remaining"}."""
    import httpx

    try:
        r = httpx.get(
            f"{config.OPENROUTER_BASE_URL}/key",
            headers={"Authorization": f"Bearer {key}"},
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        return {"state": "injoignable", "detail": f"OpenRouter injoignable ({exc.__class__.__name__})"}
    if r.status_code in (401, 403):
        return {"state": "invalide", "detail": "clé refusée par OpenRouter"}
    if r.status_code != 200:
        return {"state": "injoignable", "detail": f"OpenRouter a répondu HTTP {r.status_code}"}
    try:
        data = r.json().get("data") or {}
    except ValueError:
        data = {}
    label = data.get("label") or ""
    if label.startswith("sk-or-"):  # libellé par défaut = clé tronquée : sans intérêt
        label = ""
    return {
        "state": "ok",
        "detail": "clé valide",
        "label": label,
        "limit_remaining": data.get("limit_remaining"),
    }


def status(check: bool = False) -> dict:
    """État courant pour l'indicateur du panneau."""
    key = config.OPENROUTER_API_KEY
    out = {
        "configured": bool(key),
        "key_hint": mask(key),
        "model": config.OPENROUTER_MODEL,
        "offline_forced": config.CORRECT_OFFLINE,
        # Clé fournie par une vraie variable d'environnement Windows/macOS : elle
        # l'emporte sur le .env au prochain démarrage (on le signale au panneau).
        "from_system_env": config.KEY_FROM_SYSTEM_ENV,
        "state": "absente" if not key else "non vérifiée",
        "detail": "",
    }
    if key and check:
        out.update(verify(key))
    return out


def write_env_value(name: str, value: str, path: Path | None = None) -> None:
    """Remplace (ou ajoute) la ligne ``name=value`` du .env en gardant le reste.
    Le fichier est créé depuis .env.example s'il n'existe pas encore."""
    path = path or config.ENV_PATH
    if path.exists():
        text = path.read_text(encoding="utf-8-sig", errors="ignore")
    else:
        example = path.with_name(".env.example")
        text = example.read_text(encoding="utf-8") if example.exists() else ""
    lines = text.splitlines()
    pat = re.compile(rf"^\s*{re.escape(name)}\s*=")
    done = False
    for i, line in enumerate(lines):
        if pat.match(line):
            if not done:
                lines[i] = f"{name}={value}"
                done = True
            else:
                lines[i] = ""  # doublon : la première occurrence fait foi
    if not done:
        lines.append(f"{name}={value}")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    os.replace(tmp, path)


def set_key(key: str, path: Path | None = None) -> None:
    """Enregistre la clé (vide = supprime) dans le .env ET en mémoire."""
    write_env_value(ENV_KEY, key, path)
    config.OPENROUTER_API_KEY = key
    if key:
        os.environ[ENV_KEY] = key
    else:
        os.environ.pop(ENV_KEY, None)


def main() -> int:
    """Utilisé par l'installeur Windows : écrit dans backend/.env la clé passée
    par la variable d'environnement COQUILLE_SETUP_KEY (jamais en argument, pour
    qu'elle n'apparaisse pas dans la liste des processus)."""
    key = clean(os.environ.get("COQUILLE_SETUP_KEY", ""))
    if not key:
        return 0
    write_env_value(ENV_KEY, key)
    print(f"Clé OpenRouter enregistrée ({mask(key)}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
