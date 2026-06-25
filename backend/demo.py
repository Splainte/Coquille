"""Démo hors-ligne du pipeline (sans GPU ni clé API).

Simule une transcription Whisper + un transcript Premiere divergents, puis
montre l'alignement hybride, la correction par glossaire et la segmentation.

    python3 demo.py [frandroid|numerama|clubic]
"""

import sys

from coquille.models import Word
from coquille.pipeline import process
from coquille.serialize import cue_to_dict


def w(text, start, dur, conf=None):
    return Word(text=text, start=start, duration=dur, confidence=conf)


# Phrase réelle « Le nouveau Pixel embarque un écran AMOLED et la 5G, mais
#                 surtout une puce pensée pour l'intelligence artificielle. »
# Whisper : bon texte, timecodes corrects mais pas dans le repère séquence.
WHISPER = [
    w("Le", 0.10, 0.18), w("nouveau", 0.30, 0.40), w("Pixel", 0.72, 0.45),
    w("embarque", 1.20, 0.55), w("un", 1.78, 0.12), w("écran", 1.92, 0.40),
    w("AMOLED", 2.35, 0.60), w("et", 3.00, 0.15), w("la", 3.18, 0.15),
    w("5G", 3.36, 0.45), w("mais", 4.10, 0.30), w("surtout", 4.42, 0.45),
    w("une", 4.90, 0.18), w("puce", 5.10, 0.35), w("pensée", 5.48, 0.40),
    w("pour", 5.90, 0.22), w("l'intelligence", 6.14, 0.70),
    w("artificielle", 6.86, 0.80),
]

# Premiere : timecodes très précis (repère séquence) mais texte fautif sur le jargon.
PREMIERE = [
    w("Le", 0.08, 0.18, 0.99), w("nouveau", 0.28, 0.42, 0.98), w("pixelle", 0.70, 0.46, 0.41),
    w("embarque", 1.18, 0.56, 0.95), w("un", 1.76, 0.13, 0.97), w("écran", 1.90, 0.41, 0.93),
    w("a", 2.33, 0.30, 0.35), w("moled", 2.65, 0.31, 0.33), w("et", 2.98, 0.16, 0.96),
    w("la", 3.16, 0.16, 0.95), w("5g", 3.34, 0.46, 0.55), w("mais", 4.08, 0.31, 0.97),
    w("surtout", 4.40, 0.46, 0.96), w("une", 4.88, 0.19, 0.97), w("puce", 5.08, 0.36, 0.9),
    w("pensée", 5.46, 0.41, 0.92), w("pour", 5.88, 0.23, 0.96),
    w("l'intelligence", 6.12, 0.72, 0.88), w("artificielle", 6.84, 0.82, 0.9),
]


def main():
    media = sys.argv[1] if len(sys.argv) > 1 else "frandroid"
    result = process(media, whisper_words=WHISPER, premiere_words=PREMIERE, offline=True)

    print(f"=== Média : {media}  |  Track Style : {result.track_style} ===\n")
    print("Mots après alignement + correction glossaire :")
    print("  " + " ".join(x.text for x in result.words) + "\n")

    print("Cues :")
    for c in result.cues:
        d = cue_to_dict(c)
        print(f"  [{d['start']:6.2f} → {d['end']:6.2f}]  CPS={d['cps']:.1f}")
        for line in d["lines"]:
            print(f"      {line}")
    print("\n--- SRT ---")
    print(result.srt)


if __name__ == "__main__":
    main()
