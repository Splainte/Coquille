"""Lanceur du backend utilisé par le panneau (installation Coquille-Setup.exe).

    pythonw -m coquille.serve --port 8765 --log C:\\...\\backend.log

Sous pythonw (pas de console), sys.stdout/sys.stderr valent None : uvicorn
plante dès sa configuration des journaux. On les redirige vers un fichier
journal (ou vers nul) avant de démarrer.
"""

from __future__ import annotations

import argparse
import os
import sys


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="coquille.serve")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--log", help="fichier journal (écrasé à chaque démarrage)")
    a = ap.parse_args(argv)

    if a.log:
        stream = open(a.log, "w", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = stream
    else:
        if sys.stdout is None:
            sys.stdout = open(os.devnull, "w")
        if sys.stderr is None:
            sys.stderr = sys.stdout

    import uvicorn

    uvicorn.run("coquille.app:app", host=a.host, port=a.port, use_colors=False)


if __name__ == "__main__":
    main()
