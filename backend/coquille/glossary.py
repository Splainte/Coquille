"""Glossaire éditorial : cœur de l'« intelligence » de Coquille.

Chaque média a un glossaire (base commune + spécifique) listant les termes
techniques, marques et anglicismes avec leur **casse attendue** et les
**erreurs fréquentes** de reconnaissance (aliases).

Le glossaire sert à deux choses :
1. enrichir le prompt du LLM (liste des termes canoniques) ;
2. appliquer une correction déterministe (remplacement des aliases → canonique),
   utilisée comme pré-passe et comme repli hors-ligne.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import GLOSSARY_DIR
from .models import Word


def _norm_token(text: str) -> str:
    import unicodedata

    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"[^\w]", "", text.lower())


@dataclass
class Term:
    canonical: str
    aliases: list[str] = field(default_factory=list)
    note: str = ""


@dataclass
class Glossary:
    terms: list[Term] = field(default_factory=list)

    def canonical_list(self) -> list[str]:
        return [t.canonical for t in self.terms]

    def prompt_block(self) -> str:
        """Bloc texte injecté dans le prompt LLM."""
        lines = []
        for t in self.terms:
            entry = f"- {t.canonical}"
            if t.note:
                entry += f" ({t.note})"
            lines.append(entry)
        return "\n".join(lines)

    def apply(self, text: str) -> str:
        """Remplace les aliases par la forme canonique (insensible à la casse,
        sur des frontières de mots). Les termes les plus longs d'abord pour
        éviter qu'un alias court n'avale un alias long."""
        pairs: list[tuple[str, str]] = []
        for t in self.terms:
            for alias in t.aliases:
                pairs.append((alias, t.canonical))
            # corrige aussi la casse du terme canonique lui-même
            pairs.append((t.canonical, t.canonical))
        pairs.sort(key=lambda p: len(p[0]), reverse=True)

        for alias, canonical in pairs:
            pattern = re.compile(
                r"(?<!\w)" + re.escape(alias) + r"(?!\w)", re.IGNORECASE
            )
            text = pattern.sub(canonical, text)
        return text

    def apply_words(
        self, words: list[Word], trace: list[dict] | None = None
    ) -> list[Word]:
        """Version timing-safe : remplace les aliases (y compris multi-mots) en
        préservant les timecodes. Un alias multi-mots fusionne les `Word`
        correspondants en un seul, couvrant tout leur intervalle de temps.

        Si `trace` est fourni, chaque remplacement qui change réellement le texte
        y est journalisé : {original, corrected, start}."""
        # (tuple de tokens normalisés de l'alias) -> forme canonique
        entries: list[tuple[tuple[str, ...], str]] = []
        for t in self.terms:
            for alias in t.aliases:
                toks = tuple(tk for tk in (_norm_token(a) for a in alias.split()) if tk)
                if toks:
                    entries.append((toks, t.canonical))
            ctoks = tuple(tk for tk in (_norm_token(a) for a in t.canonical.split()) if tk)
            if ctoks:
                entries.append((ctoks, t.canonical))
        # Les aliases les plus longs d'abord (priorité aux correspondances longues).
        entries.sort(key=lambda e: len(e[0]), reverse=True)

        out: list[Word] = []
        i = 0
        while i < len(words):
            matched = False
            for toks, canonical in entries:
                n = len(toks)
                if i + n > len(words):
                    continue
                window = [_norm_token(words[i + k].text) for k in range(n)]
                if window == list(toks):
                    first, last = words[i], words[i + n - 1]
                    confs = [w.confidence for w in words[i : i + n] if w.confidence is not None]
                    if trace is not None:
                        original = " ".join(words[i + k].text for k in range(n))
                        if original != canonical:
                            trace.append({
                                "original": original,
                                "corrected": canonical,
                                "start": first.start,
                            })
                    out.append(
                        Word(
                            text=canonical,
                            start=first.start,
                            duration=last.end - first.start,
                            confidence=min(confs) if confs else None,
                            type="word",
                            is_eos=last.is_eos,
                            speaker=first.speaker,
                            tags=first.tags,
                        )
                    )
                    i += n
                    matched = True
                    break
            if not matched:
                out.append(words[i])
                i += 1
        return out


def _load_file(path: Path) -> list[Term]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        Term(
            canonical=t["canonical"],
            aliases=t.get("aliases", []),
            note=t.get("note", ""),
        )
        for t in data.get("terms", [])
    ]


def load_glossary(media: str, glossary_dir: Path | None = None) -> Glossary:
    """Charge base.json + <media>.json fusionnés (le média a priorité)."""
    glossary_dir = glossary_dir or GLOSSARY_DIR
    terms: dict[str, Term] = {}
    for name in ("base", media):
        for term in _load_file(glossary_dir / f"{name}.json"):
            key = term.canonical.lower()
            if key in terms:
                # fusionne les aliases
                existing = terms[key]
                merged = list(dict.fromkeys(existing.aliases + term.aliases))
                terms[key] = Term(term.canonical, merged, term.note or existing.note)
            else:
                terms[key] = term
    return Glossary(terms=list(terms.values()))
