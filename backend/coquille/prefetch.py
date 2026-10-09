"""Pré-téléchargement des modèles (appelé par l'installeur Windows).

Sans lui, la première transcription télécharge en silence ~3 Go (large-v3 sur
GPU NVIDIA, ~0,5 Go pour small sur processeur) puis ~1,2 Go pour l'alignement :
le monteur croirait le panneau planté. Chaque étape est indépendante : un échec
n'empêche rien, le modèle sera simplement téléchargé au premier usage.

    python -m coquille.prefetch
"""

from __future__ import annotations

import sys


def main() -> int:
    from . import forced_align
    from .transcribe import _ensure_cuda_dll_path, resolve_engine

    ok = True
    _ensure_cuda_dll_path()
    name = resolve_engine("auto")
    print(f"Modèle de transcription : {name}…", flush=True)
    try:
        from faster_whisper import download_model

        download_model(name)
        print(f"Modèle {name} prêt.", flush=True)
    except Exception as exc:  # réseau, disque…
        ok = False
        print(f"Modèle {name} non téléchargé ({exc}) : il le sera au premier usage.", flush=True)

    if forced_align.available():
        print("Modèle d'alignement (recalage du timing)…", flush=True)
        try:
            forced_align._load()
            print("Modèle d'alignement prêt.", flush=True)
        except Exception as exc:
            ok = False
            print(f"Modèle d'alignement non téléchargé ({exc}).", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
