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
from dataclasses import replace

from .config import SegmentationRules
from .models import Cue, Word

# Caractères de tête qui se collent au mot précédent (pas d'espace avant) :
# ponctuation classique + trait d'union et apostrophe (high-tech, n'allez…).
_NO_SPACE_BEFORE = re.compile(r"^[-,.;:!?…)\]»%”'’‐]")
# Caractères de fin de mot après lesquels on ne met pas d'espace (high- + tech).
_NO_SPACE_AFTER = ("'", "’", "-", "‐")

# Espacement typographique français : espace INSÉCABLE (U+00A0) avant la
# ponctuation « haute » (? ! ; :) et avant », après «. Insécable → le signe ne
# part jamais seul en début de ligne. ":" épargné entre chiffres (heures 10:30).
_NBSP = "\u00A0"
_FR_HIGH = re.compile("(?<=\\S)[ \u00A0]*([!?;]+)")
_FR_COLON = re.compile("(?<=\\S)[ \u00A0]*:(?!\\d)")
_FR_GUILLEMET_CLOSE = re.compile("(?<=\\S)[ \u00A0]*\u00bb")
_FR_GUILLEMET_OPEN = re.compile("\u00ab[ \u00A0]*(?=\\S)")


def _french_spacing(s: str) -> str:
    """Applique l'espacement typographique français (espaces insécables)."""
    s = _FR_HIGH.sub(_NBSP + r"\1", s)
    s = _FR_COLON.sub(_NBSP + ":", s)
    s = _FR_GUILLEMET_CLOSE.sub(_NBSP + "\u00bb", s)
    s = _FR_GUILLEMET_OPEN.sub("\u00ab" + _NBSP, s)
    return s
# Mots-outils qu'on évite de laisser en fin de ligne (ils « pendent »).
_DANGLING = {
    "le", "la", "les", "un", "une", "des", "de", "du", "d'", "l'",
    "à", "au", "aux", "et", "ou", "où", "en", "dans", "sur", "sous",
    "pour", "par", "avec", "sans", "vers", "chez", "que", "qu'", "qui",
    "mon", "ma", "mes", "ton", "ta", "tes", "son", "sa", "ses",
    "ce", "cet", "cette", "ces", "ne", "se", "je", "tu", "il", "elle",
    "nous", "vous", "ils", "elles", "on", "n'", "s'", "c'", "j'", "t'",
}


# Unités qu'on ne sépare jamais du nombre qui les précède (« 370 » | « kW »).
_UNITS = {
    "km", "kilomètres", "kilomètre", "m", "mètres", "mètre", "cm", "mm", "nm",
    "nanomètres", "kg", "g", "grammes", "tonnes", "kw", "kwh", "w", "watts", "v",
    "volts", "ch", "chevaux", "cv", "€", "euros", "euro", "dollars", "$", "minutes",
    "minute", "min", "secondes", "seconde", "s", "heures", "heure", "h", "jours",
    "ans", "mois", "pouces", "pouce", "go", "to", "mo", "gb", "tb", "mbps", "gbps",
    "hz", "ghz", "mhz", "mah", "ah", "litres", "litre", "l", "fps", "mpx",
    "mégapixels", "km/h", "mph", "°c", "degrés", "fois", "%",
}
_ENDS_WITH_DIGIT = re.compile(r"\d$")


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
    if (_ENDS_WITH_DIGIT.search(words[k - 1].text)
            and words[k].text.lower().rstrip(".,;:!?…") in _UNITS):
        return False  # nombre + unité indissociables
    return True


def merge_glued(words: list[Word]) -> list[Word]:
    """Recolle les morceaux d'un même mot que la transcription sépare
    (« l » + « 'était », « haut » + « -parleur », « mot » + « , »).

    Whisper découpe ainsi les élisions : le LLM voit alors deux identifiants
    pour un seul mot et les « corrige » de travers (« 'était »→« était »). Le
    timing du mot recollé couvre les deux morceaux ; confiance = la plus basse."""
    out: list[Word] = []
    for w in words:
        if out and (_NO_SPACE_BEFORE.match(w.text) or out[-1].text.endswith(_NO_SPACE_AFTER)):
            prev = out[-1]
            confs = [c for c in (prev.confidence, w.confidence) if c is not None]
            out[-1] = replace(
                prev,
                text=prev.text + w.text,
                duration=max(prev.end, w.end) - prev.start,
                confidence=min(confs) if confs else None,
                is_eos=prev.is_eos or w.is_eos,
            )
        else:
            out.append(w)
    return out


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
    return _french_spacing(out)


def _wrap_greedy(words: list[Word], max_line: int) -> list[str]:
    """Emballage glouton : remplit chaque ligne jusqu'à `max_line` caractères.
    Garantit des lignes <= max_line (sauf mot isolé plus long que max_line)."""
    # On emballe des BLOCS insécables (tokens collés : « l » + « 'adorer. »), sinon
    # le morceau collé s'ajoute à la ligne courante même s'il la fait déborder.
    blocks: list[list[Word]] = []
    for i, w in enumerate(words):
        if blocks and not _can_break_before(words, i):
            blocks[-1].append(w)
        else:
            blocks.append([w])
    lines: list[str] = []
    line: list[Word] = []
    for block in blocks:
        if line and len(render(line + block)) > max_line:
            lines.append(render(line))
            line = list(block)
        else:
            line.extend(block)
    if line:
        lines.append(render(line))
    return lines


_SENTENCE_END = (".", "!", "?", "…")
_CLAUSE_PUNCT = (".", "!", "?", "…", ",", ";", ":")

def _ends_sentence(w: Word) -> bool:
    """Vrai si ce mot termine une phrase (marqueur eos ou ponctuation forte)."""
    return bool(w.is_eos) or w.text.rstrip().endswith(_SENTENCE_END)


def _fits(words: list[Word], rules: SegmentationRules) -> bool:
    """Vrai si ces mots tiennent dans un cue (≤ max_lines lignes, ≤ max_duration)."""
    if not words:
        return True
    if (words[-1].end - words[0].start) > rules.max_duration:
        return False
    return len(_wrap_greedy(words, rules.max_chars_per_line)) <= rules.max_lines


# --- Découpage en cues : programmation dynamique ---------------------------
# Chaque façon de couper la suite de mots en cues reçoit un coût ; on retient
# la moins chère (optimum global, comme la césure de paragraphes de TeX). Un
# remplissage glouton, lui, fait des cues pleins puis laisse des queues de
# phrase isolées (« de la batterie. » 0,4 s à l'écran).
_CUE_COST = 1.0            # coût fixe par cue : évite de fragmenter sans raison
_CROSS_SENTENCE = 10.0     # garder deux phrases dans un même cue
_CROSS_PAUSE = 3.0         # enjamber une pause >= gap_split (+ par seconde de pause)
_END_DANGLING = 10.0       # finir un cue sur un mot-outil (« la », « de »…)
_END_WEAK = 3.0            # finir sur un mot faible (« a », « est », « très »…)
_END_PLAIN = 2.5           # finir au milieu d'une proposition, sans ponctuation
_END_CLAUSE = 0.5          # finir sur une virgule / deux-points
_SHORT = 8.0               # par seconde manquante sous la durée minimale
_TINY_TEXT = 2.0           # cue de moins de 10 caractères
_ONE_WORD = 4.0            # cue d'un seul mot

# Mots qui terminent mal un cue ou une ligne sans être des mots-outils stricts.
_WEAK_END = {
    "a", "as", "ont", "est", "sont", "été", "être", "avoir", "va", "vont",
    "peut", "plus", "moins", "très", "trop", "assez", "pas", "tout", "bien",
    "aussi", "comme", "mais", "donc", "car", "si", "quand", "c'est",
    "n'est", "qu'il", "qu'on", "parce",
}


def _last_key(w: Word) -> str:
    return w.text.lower().strip("'’")


def _cue_cost(words: list[Word], i: int, j: int, rules: SegmentationRules) -> float:
    """Coût du cue words[i:j] (j exclu) au sein de la séquence `words`."""
    cost = _CUE_COST
    for k in range(i + 1, j):  # ce que le cue enjambe
        if _ends_sentence(words[k - 1]):
            cost += _CROSS_SENTENCE
        gap = words[k].start - words[k - 1].end
        if gap >= rules.gap_split:
            cost += _CROSS_PAUSE * (1.0 + gap)
    last = words[j - 1]
    if j < len(words):  # qualité de la coupe en fin de cue
        gap = words[j].start - last.end
        if _ends_sentence(last):
            pass
        elif last.text.rstrip().endswith(_CLAUSE_PUNCT) or gap >= rules.gap_split:
            cost += _END_CLAUSE
        elif _last_key(last) in _DANGLING:
            cost += _END_DANGLING
        elif _last_key(last) in _WEAK_END:
            cost += _END_WEAK
        else:
            cost += _END_PLAIN
    # Temps d'affichage réel : jusqu'au mot suivant (le cue est tenu jusque-là).
    shown_until = words[j].start if j < len(words) else last.end
    shown = shown_until - words[i].start
    if shown < rules.min_duration:
        cost += _SHORT * (rules.min_duration - shown)
    if len(render(words[i:j])) < 10:
        cost += _TINY_TEXT
    cost += _line_break_penalty(words[i:j], rules)
    if j - i == 1:
        cost += _ONE_WORD
    return cost


_LINE_DANGLING = 6.0  # retour à la ligne forcé après un mot-outil (« sur la » / …)
_LINE_WEAK = 1.5


def _line_break_penalty(words: list[Word], rules: SegmentationRules) -> float:
    """Pénalité du meilleur retour à la ligne de ce cue (0 si une seule ligne)."""
    lines = _split_lines(words, rules)
    if len(lines) < 2:
        return 0.0
    end = (lines[0].split() or [""])[-1].lower().strip("'’")
    if end in _DANGLING:
        return _LINE_DANGLING
    if end in _WEAK_END:
        return _LINE_WEAK
    return 0.0


def _best_cues(seq: list[Word], rules: SegmentationRules) -> list[list[Word]]:
    """Découpage optimal d'une séquence (sans vrai silence) en cues."""
    n = len(seq)
    inf = float("inf")
    best = [0.0] + [inf] * n
    back = [0] * (n + 1)
    for j in range(1, n + 1):
        if j < n and not _can_break_before(seq, j):
            continue  # jamais de coupe entre deux tokens collés
        for i in range(j - 1, -1, -1):
            if not _fits(seq[i:j], rules) and j - i > 1:
                break  # en reculant, le cue ne fait que grandir
            if best[i] == inf:
                continue
            c = best[i] + _cue_cost(seq, i, j, rules)
            if c < best[j]:
                best[j], back[j] = c, i
    cues: list[list[Word]] = []
    j = n
    while j > 0:
        i = back[j]
        cues.append(seq[i:j])
        j = i
    return cues[::-1]


def _group_into_cues(words: list[Word], rules: SegmentationRules) -> list[list[Word]]:
    """Découpe la suite de mots en groupes (un groupe = un futur cue).

    Seuls les VRAIS silences (>= gap_split + 1 s) sont des frontières imposées ;
    fins de phrase et micro-pauses sont des coupes fortement préférées mais pas
    obligatoires, arbitrées par le coût global (_best_cues)."""
    real_silence = rules.gap_split + 1.0
    seqs: list[list[Word]] = []
    current: list[Word] = []
    for w in words:
        if current and w.start - current[-1].end >= real_silence:
            seqs.append(current)
            current = []
        current.append(w)
    if current:
        seqs.append(current)
    groups: list[list[Word]] = []
    for seq in seqs:
        groups.extend(_best_cues(seq, rules))
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
            s -= 3                                     # bonus ponctuation
        if last.lower().strip("'’") in _DANGLING:
            s += 10                                    # éviter un mot-outil pendu
        elif last.lower().strip("'’") in _WEAK_END:
            s += 3                                     # ni un mot faible (« est »)
        if k == 1 or k == len(words) - 1:
            s += 12                                    # ne pas isoler un seul mot
        #                                                sur une ligne (« IA. » seul)
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
        start = max(0.0, group[0].start - rules.lead_in)
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
