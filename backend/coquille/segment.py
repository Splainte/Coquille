"""Segmentation et équilibrage des lignes.

Transforme une liste de mots horodatés en cues (sous-titres) en respectant :
- la longueur max de ligne (caractères),
- le max de 2 lignes,
- la vitesse de lecture (CPS),
- les durées min/max d'un cue,
- les pauses (un silence assez long démarre un nouveau cue),
- les coupures aux frontières naturelles (ponctuation, fin de phrase),
- l'équilibrage des deux lignes (longueurs proches),
- en évitant de terminer une ligne par un mot-outil (article, préposition…).
"""

from __future__ import annotations

import re

from .config import SegmentationRules
from .models import Cue, Word

# Ponctuation qui se colle au mot précédent (pas d'espace avant).
_TRAILING_PUNCT = re.compile(r"^[,.;:!?…)\]»%»”]")
# Mots-outils qu'on évite de laisser en fin de ligne (ils « pendent »).
_DANGLING = {
    "le", "la", "les", "un", "une", "des", "de", "du", "d'", "l'",
    "à", "au", "aux", "et", "ou", "où", "en", "dans", "sur", "sous",
    "pour", "par", "avec", "sans", "vers", "chez", "que", "qu'", "qui",
    "mon", "ma", "mes", "ton", "ta", "tes", "son", "sa", "ses",
    "ce", "cet", "cette", "ces", "ne", "se", "je", "tu", "il", "elle",
    "nous", "vous", "ils", "elles", "on", "n'", "s'", "c'", "j'", "t'",
}


def render(words: list[Word]) -> str:
    """Rend une suite de mots en texte propre (gestion des espaces/ponctuation)."""
    out = ""
    for w in words:
        tok = w.text
        if not out:
            out = tok
        elif _TRAILING_PUNCT.match(tok) or out.endswith("'") or out.endswith("’"):
            out += tok
        else:
            out += " " + tok
    return out


def _wrap_greedy(words: list[Word], max_line: int) -> list[str]:
    """Emballage glouton : remplit chaque ligne jusqu'à `max_line` caractères.
    Garantit des lignes <= max_line (sauf mot isolé plus long que max_line)."""
    lines: list[str] = []
    line: list[Word] = []
    for w in words:
        if line and len(render(line + [w])) > max_line:
            lines.append(render(line))
            line = [w]
        else:
            line.append(w)
    if line:
        lines.append(render(line))
    return lines


def _group_into_cues(words: list[Word], rules: SegmentationRules) -> list[list[Word]]:
    """Découpe la suite de mots en groupes (un groupe = un futur cue)."""
    groups: list[list[Word]] = []
    current: list[Word] = []

    for i, w in enumerate(words):
        if current:
            prev = current[-1]
            gap = w.start - prev.end
            # Déborde si l'emballage du groupe dépasse le nombre de lignes autorisé.
            wrapped = _wrap_greedy(current + [w], rules.max_chars_per_line)
            would_overflow = len(wrapped) > rules.max_lines
            would_be_too_long = (w.end - current[0].start) > rules.max_duration
            # Une pause franche, un débordement, une durée trop longue, ou une
            # fin de phrase précédente → on clôt le cue courant.
            if (
                gap >= rules.gap_split
                or would_overflow
                or would_be_too_long
                or (prev.is_eos and len(render(current)) >= rules.max_chars_per_line // 2)
            ):
                groups.append(current)
                current = []
        current.append(w)

    if current:
        groups.append(current)
    return groups


def _score_break(left: list[Word], right: list[Word], max_line: int) -> float:
    """Plus le score est bas, meilleure est la coupure entre `left` et `right`."""
    l1, l2 = len(render(left)), len(render(right))
    if l1 > max_line or l2 > max_line:
        return float("inf")
    score = abs(l1 - l2)  # équilibre des longueurs
    last = left[-1].text.lower().strip(".,;:!?")
    first_raw = right[0].text
    # Coupure juste après une ponctuation forte → bonus.
    if left[-1].text.endswith((".", ",", ";", ":", "!", "?", "…")):
        score -= 6
    # Coupure après un mot-outil → malus (le mot pendrait en fin de ligne).
    if last in _DANGLING or left[-1].text.lower() in _DANGLING:
        score += 8
    # Coupure juste avant une ponctuation collante → interdit de fait.
    if _TRAILING_PUNCT.match(first_raw):
        score += 50
    return score


def _split_lines(words: list[Word], rules: SegmentationRules) -> list[str]:
    """Découpe les mots d'un cue en 1 ou 2 lignes équilibrées."""
    full = render(words)
    if len(full) <= rules.max_chars_per_line or len(words) < 2:
        return [full]

    best_k, best_score = None, float("inf")
    for k in range(1, len(words)):
        score = _score_break(words[:k], words[k:], rules.max_chars_per_line)
        if score < best_score:
            best_score, best_k = score, k

    if best_k is None or best_score == float("inf"):
        # Aucune coupure en 2 lignes équilibrées : repli sur l'emballage glouton
        # (le groupe a été borné pour tenir en max_lines lignes).
        return _wrap_greedy(words, rules.max_chars_per_line)
    return [render(words[:best_k]), render(words[best_k:])]


def segment(words: list[Word], rules: SegmentationRules) -> list[Cue]:
    """Pipeline complet de segmentation."""
    words = [w for w in words if w.text.strip()]
    if not words:
        return []

    groups = _group_into_cues(words, rules)
    cues: list[Cue] = []

    for idx, group in enumerate(groups, start=1):
        start = group[0].start
        end = group[-1].end
        lines = _split_lines(group, rules)

        # Durée minimale : on étire la fin sans empiéter sur le cue suivant.
        if end - start < rules.min_duration:
            end = start + rules.min_duration

        cues.append(Cue(index=idx, start=start, end=end, lines=lines))

    _enforce_gaps(cues, rules)
    return cues


def _enforce_gaps(cues: list[Cue], rules: SegmentationRules) -> None:
    """Évite les chevauchements et garde un écart minimal entre cues."""
    for a, b in zip(cues, cues[1:]):
        if a.end > b.start - rules.min_gap:
            a.end = max(a.start + rules.min_duration / 2, b.start - rules.min_gap)
