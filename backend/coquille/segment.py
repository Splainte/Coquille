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

# Caractères de tête qui se collent au mot précédent (pas d'espace avant) :
# ponctuation classique + trait d'union et apostrophe (high-tech, n'allez…).
_NO_SPACE_BEFORE = re.compile(r"^[-,.;:!?…)\]»%”'’‐]")
# Caractères de fin de mot après lesquels on ne met pas d'espace (high- + tech).
_NO_SPACE_AFTER = ("'", "’", "-", "‐")
# Mots-outils qu'on évite de laisser en fin de ligne (ils « pendent »).
_DANGLING = {
    "le", "la", "les", "un", "une", "des", "de", "du", "d'", "l'",
    "à", "au", "aux", "et", "ou", "où", "en", "dans", "sur", "sous",
    "pour", "par", "avec", "sans", "vers", "chez", "que", "qu'", "qui",
    "mon", "ma", "mes", "ton", "ta", "tes", "son", "sa", "ses",
    "ce", "cet", "cette", "ces", "ne", "se", "je", "tu", "il", "elle",
    "nous", "vous", "ils", "elles", "on", "n'", "s'", "c'", "j'", "t'",
}


def _can_break_before(words: list[Word], k: int) -> bool:
    """Vrai si on peut couper (ligne ou cue) juste avant words[k] sans casser un
    collage. On ne coupe jamais entre deux tokens collés (high / -tech, n' / allez)
    car rendus sur deux lignes/cues distincts ils perdraient leur jointure."""
    if k <= 0 or k >= len(words):
        return False
    if _NO_SPACE_BEFORE.match(words[k].text):
        return False
    if words[k - 1].text.endswith(_NO_SPACE_AFTER):
        return False
    return True


def render(words: list[Word]) -> str:
    """Rend une suite de mots en texte propre (gestion des espaces/ponctuation)."""
    out = ""
    for w in words:
        tok = w.text
        if not out:
            out = tok
        elif _NO_SPACE_BEFORE.match(tok) or out.endswith(_NO_SPACE_AFTER):
            out += tok
        else:
            out += " " + tok
    return out


def _wrap_greedy(words: list[Word], max_line: int) -> list[str]:
    """Emballage glouton : remplit chaque ligne jusqu'à `max_line` caractères.
    Garantit des lignes <= max_line (sauf mot isolé plus long que max_line)."""
    lines: list[str] = []
    line: list[Word] = []
    for i, w in enumerate(words):
        breakable = _can_break_before(words, i)
        if line and breakable and len(render(line + [w])) > max_line:
            lines.append(render(line))
            line = [w]
        else:
            line.append(w)
    if line:
        lines.append(render(line))
    return lines


_SENTENCE_END = (".", "!", "?", "…")
_CLAUSE_PUNCT = (".", "!", "?", "…", ",", ";", ":")


def _fits(words: list[Word], rules: SegmentationRules) -> bool:
    """Vrai si ces mots tiennent dans un cue (≤ max_lines lignes, ≤ max_duration)."""
    if not words:
        return True
    if (words[-1].end - words[0].start) > rules.max_duration:
        return False
    return len(_wrap_greedy(words, rules.max_chars_per_line)) <= rules.max_lines


def _best_cut_index(words: list[Word], k: int) -> int:
    """Meilleur point de coupure dans words[:k+...] : on privilégie la dernière
    frontière de proposition (ponctuation), sinon on évite de finir sur un
    mot-outil. Renvoie un index de coupure entre 1 et k."""
    for j in range(k, 0, -1):
        if _can_break_before(words, j) and words[j - 1].text.rstrip().endswith(_CLAUSE_PUNCT):
            return j
    j = k
    while j > 1 and (
        not _can_break_before(words, j)
        or words[j - 1].text.lower().strip("'’") in _DANGLING
    ):
        j -= 1
    return j


def _pack_segment(seg: list[Word], rules: SegmentationRules) -> list[list[Word]]:
    """Empaquette une phrase en autant de cues que nécessaire, en coupant aux
    meilleures frontières (ponctuation de proposition de préférence)."""
    cues: list[list[Word]] = []
    rest = seg
    while rest:
        if _fits(rest, rules):
            cues.append(rest)
            break
        # Plus grand préfixe qui tient dans un cue.
        k = 0
        for j in range(1, len(rest) + 1):
            if _fits(rest[:j], rules):
                k = j
            else:
                break
        if k == 0:  # un seul mot ne tient pas (cas extrême) : on avance quand même
            k = 1
        split = _best_cut_index(rest, k)
        cues.append(rest[:split])
        rest = rest[split:]
    return _avoid_orphan_cue(cues, rules)


def _avoid_orphan_cue(
    cues: list[list[Word]], rules: SegmentationRules
) -> list[list[Word]]:
    """Évite qu'un cue ne contienne qu'un seul mot (« meilleur. » seul) : on
    refusionne avec le cue précédent puis on recoupe au point le plus équilibré
    (en nombre de mots) où chaque moitié tient encore dans un cue."""
    if len(cues) < 2 or len(cues[-1]) != 1:
        return cues
    merged = cues[-2] + cues[-1]
    best: tuple[int, int] | None = None
    for k in range(1, len(merged)):
        if not _can_break_before(merged, k):
            continue
        left, right = merged[:k], merged[k:]
        if len(right) < 2 or not _fits(left, rules) or not _fits(right, rules):
            continue
        score = abs(len(left) - len(right))
        if best is None or score < best[0]:
            best = (score, k)
    if best is not None:
        cues[-2:] = [merged[: best[1]], merged[best[1] :]]
    return cues


def _group_into_cues(words: list[Word], rules: SegmentationRules) -> list[list[Word]]:
    """Découpe la suite de mots en groupes (un groupe = un futur cue).

    Deux temps : (1) on segmente d'abord aux frontières *dures* — vraie pause ou
    fin de phrase — pour des coupes cohérentes ; (2) on empaquette chaque phrase
    en cues seulement si elle dépasse les limites de taille/durée.
    """
    segments: list[list[Word]] = []
    current: list[Word] = []
    for w in words:
        if current:
            prev = current[-1]
            gap = w.start - prev.end
            ends_sentence = prev.is_eos or prev.text.rstrip().endswith(_SENTENCE_END)
            if gap >= rules.gap_split or ends_sentence:
                segments.append(current)
                current = []
        current.append(w)
    if current:
        segments.append(current)

    groups: list[list[Word]] = []
    for seg in segments:
        groups.extend(_pack_segment(seg, rules))
    return groups


def _split_lines(words: list[Word], rules: SegmentationRules) -> list[str]:
    """Découpe les mots d'un cue en 1 ou 2 lignes, AU PLUS équilibrées.

    Parmi toutes les coupures où *les deux lignes* tiennent dans la largeur, on
    prend la plus équilibrée (écart de longueur minimal). La ponctuation et les
    mots-outils ne servent que de départage léger — ils ne peuvent pas imposer
    un déséquilibre (pas de "22 puis 4")."""
    max_line = rules.max_chars_per_line
    full = render(words)
    if len(full) <= max_line or len(words) < 2:
        return [full]

    candidates = [
        k for k in range(1, len(words))
        if _can_break_before(words, k)
        and len(render(words[:k])) <= max_line and len(render(words[k:])) <= max_line
    ]
    if not candidates:
        # Aucune coupure 2 lignes valide (souvent un mot plus long que la ligne) :
        # repli sur l'emballage glouton.
        return _wrap_greedy(words, max_line)

    def score(k: int) -> float:
        l1, l2 = len(render(words[:k])), len(render(words[k:]))
        s = float(abs(l1 - l2))                       # priorité : équilibre
        last = words[k - 1].text
        if last.rstrip().endswith((".", "!", "?", "…", ",", ";", ":")):
            s -= 2                                     # léger bonus ponctuation
        if last.lower().strip("'’") in _DANGLING:
            s += 6                                     # éviter un mot-outil pendu
        return s

    best_k = min(candidates, key=score)
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
    _close_small_gaps(cues, rules)
    return cues


def _enforce_gaps(cues: list[Cue], rules: SegmentationRules) -> None:
    """Empêche tout chevauchement : la fin d'un cue ne dépasse jamais le début
    du suivant (moins min_gap). Contrainte dure, prioritaire sur la durée
    minimale — sinon un cue court pourrait empiéter sur le suivant."""
    for a, b in zip(cues, cues[1:]):
        latest_end = b.start - rules.min_gap
        if a.end > latest_end:
            a.end = max(a.start, latest_end)  # jamais < a.start (durée >= 0)


def _close_small_gaps(cues: list[Cue], rules: SegmentationRules) -> None:
    """Supprime les images vides quand l'écart n'est pas un vrai silence :
    on tient le sous-titre jusqu'au suivant (coupe directe ST→ST). Un écart
    >= max_hold_gap est considéré comme un vrai silence et laissé tel quel."""
    for a, b in zip(cues, cues[1:]):
        gap = b.start - a.end
        if 0 < gap < rules.max_hold_gap:
            a.end = b.start
