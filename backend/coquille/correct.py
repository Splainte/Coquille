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
    """Une correction unitaire, pour affichage dans le panneau (lien « localiser »)."""

    original: str
    corrected: str        # "" = mot supprimé (parasite)
    start: float          # timecode (s) du mot — sert au déplacement de la tête
    source: str           # "glossary" | "llm"


@dataclass
class CorrectionResult:
    """Résultat de la passe de correction (pour remontée au panneau)."""

    words: list[Word]
    used_llm: bool = False          # le LLM a bien été appelé et appliqué
    model: str | None = None        # modèle utilisé si LLM
    n_corrections: int = 0          # nb de mots modifiés par le LLM (hors glossaire)
    error: str | None = None        # raison du repli glossaire si le LLM a échoué
    warnings: list[str] = field(default_factory=list)
    items: list[Correction] = field(default_factory=list)  # détail (glossaire + LLM)

SYSTEM_PROMPT = """\
Tu es correcteur de transcriptions pour des médias high-tech francophones \
(Frandroid, Numerama, Clubic). On te donne une transcription automatique \
découpée en mots numérotés, issue d'une vidéo où l'on parle de produits, \
marques et concepts technologiques (souvent en anglais).

Ta mission : corriger UNIQUEMENT les erreurs de reconnaissance vocale — \
orthographe, casse des noms propres et marques, anglicismes mal transcrits, \
ponctuation évidente. Tu NE reformules PAS, tu NE traduis PAS, tu NE changes \
PAS le sens ni l'ordre des mots.

Contraintes STRICTES de sortie :
- Tu renvoies un JSON {"words": [{"id": <int>, "text": "<corrigé>"}, ...]}.
- Un seul objet par identifiant fourni, dans le même ordre.
- N'inclus QUE les mots que tu modifies (les autres restent inchangés).
- Garde un seul mot par identifiant (pas de fusion/découpe).
- Mets "" comme text pour supprimer un mot parasite (hésitation, répétition).
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
) -> CorrectionResult:
    """Corrige les mots. Renvoie un CorrectionResult (entrée non mutée).

    Le LLM ne fait que de la correction timing-safe (1 mot ↔ 1 id). En cas
    d'échec (réseau, JSON illisible…) on **dégrade gracieusement** vers le seul
    résultat glossaire : on ne perd jamais la transcription pour un hoquet LLM.
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

    # 2-3. Correction LLM par identifiant — protégée contre toute exception.
    try:
        user = _build_user_prompt(glossed, glossary, media)
        raw = _call_openrouter(SYSTEM_PROMPT, user)
        corrections = _parse_corrections(raw, len(glossed))
    except Exception as exc:  # réseau, HTTP, JSON, clé invalide…
        return CorrectionResult(
            words=glossed,
            used_llm=False,
            model=config.OPENROUTER_MODEL,
            error=f"{type(exc).__name__}: {exc}",
            warnings=["Correction LLM indisponible : repli sur le glossaire."],
            items=gloss_items,
        )

    llm_items = [
        Correction(glossed[i].text, text, glossed[i].start, "llm")
        for i, text in sorted(corrections.items())
        if 0 <= i < len(glossed) and text != glossed[i].text
    ]
    return CorrectionResult(
        words=_apply_corrections(glossed, corrections),
        used_llm=True,
        model=config.OPENROUTER_MODEL,
        n_corrections=len(llm_items),
        items=gloss_items + llm_items,
    )
