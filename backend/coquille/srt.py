"""Export SRT.

On reste sur une génération minimale et sans dépendance : le format SRT est
trivial et c'est ce que `project.importFiles()` attend côté Premiere.
"""

from __future__ import annotations

from .models import Cue


def _fmt_ts(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    ms_total = int(round(seconds * 1000))
    h, ms_total = divmod(ms_total, 3_600_000)
    m, ms_total = divmod(ms_total, 60_000)
    s, ms = divmod(ms_total, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(cues: list[Cue]) -> str:
    blocks = []
    for cue in cues:
        blocks.append(
            f"{cue.index}\n"
            f"{_fmt_ts(cue.start)} --> {_fmt_ts(cue.end)}\n"
            f"{cue.text}"
        )
    return "\n\n".join(blocks) + "\n"
