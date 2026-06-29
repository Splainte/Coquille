"""Correction LLM contextuelle via OpenRouter.

Stratégie **timing-safe** : on corrige mot à mot (1 ↔ 1 par identifiant). Le LLM
peut corriger l'orthographe, la casse, les noms propres et la ponctuation d'un
mot, mais ne réécrit pas la phrase — donc chaque timecode reste ancré.

Étapes :
1. pré-passe glossaire déterministe (gère aussi les aliases multi-mots) ;
2. appel LLM avec le glossaire en contexte + les mots à faible confiance signalés ;
3. application des corrections par identifiant (mots inchangés conservés tels quels).

Hors-ligne (pas de clé, ou COQUILLE_CORRECT_OFFLINE=1), on s'arrête après l'étape 1.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace

from . import config
from .glossary import Glossary
from .models import Word

LOW_CONFIDENCE = 0.5  # seuil en dessous duquel un mot est signalé au LLM

# Le LLM emballe parfois le JSON dans un bloc Markdown ```json … ``` malgré la
# consigne : on le retire avant parsing.
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


@dataclass
class Correction:
    """Une correction unitaire APPLIQUÉE (glossaire), pour affichage."""

    original: str
    corrected: str        # "" = mot supprimé (parasite)
    start: float          # timecode (s) du mot — sert au déplacement de la tête
    source: str           # "glossary" | "llm"


@dataclass
class Suggestion:
    """Une proposition LLM NON appliquée, à valider par l'utilisateur."""

    index: int            # position du mot dans la liste (après glossaire)
    original: str
    suggested: str        # "" = suppression proposée
    start: float          # timecode (s) du mot — pour le lien « localiser »


@dataclass
class CorrectionResult:
    """Résultat de la passe de correction (pour remontée au panneau)."""

    words: list[Word]
    used_llm: bool = False          # le LLM a bien été appelé
    model: str | None = None        # modèle utilisé si LLM
    n_corrections: int = 0          # nb de corrections LLM (appliquées OU suggérées)
    error: str | None = None        # raison du repli glossaire si le LLM a échoué
    warnings: list[str] = field(default_factory=list)
    items: list[Correction] = field(default_factory=list)        # appliquées
    suggestions: list[Suggestion] = field(default_factory=list)  # à valider (mode suggest)

SYSTEM_PROMPT = """\
Tu es correcteur de transcriptions automatiques pour des médias high-tech \
francophones (Frandroid, Numerama, Clubic). Le texte vient d'une vidéo où l'on \
parle de produits, marques et concepts technologiques. Un glossaire \
déterministe a DÉJÀ été appliqué avant toi : tu interviens à la marge.

Ta mission : corriger UNIQUEMENT les erreurs MANIFESTES de reconnaissance \
vocale — orthographe, casse des noms propres et marques, anglicismes \
phonétiquement mal transcrits.

RÈGLES ABSOLUES :
- Ne modifie JAMAIS un chiffre, un numéro de version ou de modèle (ex. « S26 », \
  « One UI 8.5 », « 5G »). Ils sont fiables et tu n'as PAS connaissance des \
  produits les plus récents : « S26 » reste « S26 », même si tu crois que \
  ça devrait être « S25 ». Pareil pour « 8.5 » → ne le passe pas à « 8.8 ».
- Dans le DOUTE, NE CORRIGE PAS. Mieux vaut laisser un mot tel quel que de le \
  remplacer par une supposition. Ne « complète » ni ne « devine » un mot plausible.
- Tu NE reformules PAS, NE traduis PAS, ne changes NI le sens NI l'ordre des mots.
- 1 identifiant = 1 mot : tu ne peux NI fusionner NI scinder. Si un nom propre \
  est scindé sur plusieurs mots, laisse-les tels quels (le glossaire fusionne).
- Respecte la casse et l'orthographe du glossaire fourni.

Format de sortie (STRICT) :
- JSON {"words": [{"id": <int>, "text": "<corrigé>"}, ...]}.
- N'inclus QUE les mots que tu modifies réellement.
- Un seul mot par identifiant (pas de fusion/découpe).
- "" comme text pour supprimer un mot parasite évident (hésitation « euh »).
"""


def _build_user_prompt(words: list[Word], glossary: Glossary, media: str) -> str:
    items = []
    for i, w in enumerate(words):
        flag = " ⚠FAIBLE" if (w.confidence is not None and w.confidence < LOW_CONFIDENCE) else ""
        items.append(f"{i}\t{w.text}{flag}")
    glossary_block = glossary.prompt_block() or "(aucun terme spécifique)"
    return (
        f"Média : {media}\n\n"
        f"Glossaire de référence (casse et orthographe à respecter) :\n"
        f"{glossary_block}\n\n"
        f"Les mots marqués ⚠FAIBLE ont une confiance basse : examine-les en priorité.\n\n"
        f"Transcription (id<TAB>mot) :\n" + "\n".join(items)
    )


def _digits(s: str) -> str:
    """Suite des chiffres d'une chaîne (pour comparer S26 vs S25, 8.5 vs 8.8)."""
    return "".join(ch for ch in s if ch.isdigit())


def _apply_corrections(words: list[Word], corrections: dict[int, str]) -> list[Word]:
    out: list[Word] = []
    for i, w in enumerate(words):
        if i in corrections:
            new_text = corrections[i]
            if new_text == "":
                continue  # mot supprimé
            out.append(w.with_text(new_text))
        else:
            out.append(w)
    return out


def _call_openrouter(system: str, user: str) -> str:
    import httpx  # importé à la demande pour ne pas l'exiger en mode hors-ligne

    headers = {
        "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/coquille",
        "X-Title": "Coquille",
    }
    payload = {
        "model": config.OPENROUTER_MODEL,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    with httpx.Client(timeout=120) as client:
        resp = client.post(
            f"{config.OPENROUTER_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


def _parse_corrections(raw: str, n_words: int) -> dict[int, str]:
    raw = _FENCE.sub("", raw.strip())
    data = json.loads(raw)
    corrections: dict[int, str] = {}
    for item in data.get("words", []):
        try:
            idx = int(item["id"])
        except (KeyError, ValueError, TypeError):
            continue
        if 0 <= idx < n_words and "text" in item:
            corrections[idx] = str(item["text"])
    return corrections


def correct(
    words: list[Word],
    glossary: Glossary,
    media: str,
    *,
    offline: bool | None = None,
    apply_llm: bool = True,
) -> CorrectionResult:
    """Corrige les mots. Renvoie un CorrectionResult (entrée non mutée).

    Le glossaire (déterministe) est TOUJOURS appliqué. Le LLM ne fait que de la
    correction timing-safe (1 mot ↔ 1 id) :
    - apply_llm=True  : ses corrections sont appliquées (mode historique) ;
    - apply_llm=False : elles sont renvoyées comme `suggestions` à valider, sans
      modifier les mots (revue avant insertion).
    En cas d'échec LLM (réseau, JSON illisible…) on **dégrade gracieusement** vers
    le seul résultat glossaire : on ne perd jamais la transcription.
    """
    # 1. Pré-passe glossaire (déterministe, multi-mots, timing-safe), tracée.
    gloss_trace: list[dict] = []
    glossed = glossary.apply_words([replace(w) for w in words], trace=gloss_trace)
    gloss_items = [
        Correction(t["original"], t["corrected"], t["start"], "glossary")
        for t in gloss_trace
    ]

    offline = config.CORRECT_OFFLINE if offline is None else offline
    if offline or not config.OPENROUTER_API_KEY:
        return CorrectionResult(words=glossed, used_llm=False, items=gloss_items)

    # 2-3. Correction LLM par identifiant — protégée contre toute exception, avec
    # un retry (OpenRouter renvoie parfois une réponse vide / un hoquet réseau).
    user = _build_user_prompt(glossed, glossary, media)
    raw_corrections: dict[int, str] | None = None
    last_exc: Exception | None = None
    for _attempt in range(2):
        try:
            raw = _call_openrouter(SYSTEM_PROMPT, user)
            raw_corrections = _parse_corrections(raw, len(glossed))
            break
        except Exception as exc:  # réseau, HTTP, JSON vide, clé invalide…
            last_exc = exc
    if raw_corrections is None:
        return CorrectionResult(
            words=glossed,
            used_llm=False,
            model=config.OPENROUTER_MODEL,
            error=f"{type(last_exc).__name__}: {last_exc}",
            warnings=["Correction LLM indisponible : repli sur le glossaire."],
            items=gloss_items,
        )

    # Garde-fou déterministe : le LLM n'a JAMAIS le droit de modifier les chiffres
    # (S26→S25, 8.5→8.8…). L'audio transcrit les nombres de façon fiable, et le
    # modèle a tendance à les « corriger » vers ce qu'il croit savoir.
    corrections: dict[int, str] = {}
    rejected = 0
    for i, text in raw_corrections.items():
        if not (0 <= i < len(glossed)):
            continue
        if _digits(text) != _digits(glossed[i].text):
            rejected += 1
            continue
        corrections[i] = text

    warnings: list[str] = []
    if rejected:
        warnings.append(
            f"{rejected} correction(s) LLM ignorée(s) : modification de chiffres "
            f"interdite (numéros de version/modèle protégés)."
        )

    changed = {
        i: text for i, text in corrections.items() if text != glossed[i].text
    }

    if not apply_llm:
        # Mode suggestion : on n'applique RIEN du LLM, on remonte des propositions.
        suggestions = [
            Suggestion(i, glossed[i].text, text, glossed[i].start)
            for i, text in sorted(changed.items())
        ]
        return CorrectionResult(
            words=glossed,
            used_llm=True,
            model=config.OPENROUTER_MODEL,
            n_corrections=len(suggestions),
            warnings=warnings,
            items=gloss_items,
            suggestions=suggestions,
        )

    llm_items = [
        Correction(glossed[i].text, text, glossed[i].start, "llm")
        for i, text in sorted(changed.items())
    ]
    return CorrectionResult(
        words=_apply_corrections(glossed, changed),
        used_llm=True,
        model=config.OPENROUTER_MODEL,
        n_corrections=len(llm_items),
        warnings=warnings,
        items=gloss_items + llm_items,
    )
