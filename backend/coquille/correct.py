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
from dataclasses import replace

from . import config
from .glossary import Glossary
from .models import Word

LOW_CONFIDENCE = 0.5  # seuil en dessous duquel un mot est signalé au LLM

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
) -> list[Word]:
    """Corrige les mots. Renvoie une nouvelle liste (entrée non mutée)."""
    # 1. Pré-passe glossaire (déterministe, multi-mots, timing-safe).
    words = glossary.apply_words([replace(w) for w in words])

    offline = config.CORRECT_OFFLINE if offline is None else offline
    if offline or not config.OPENROUTER_API_KEY:
        return words

    # 2-3. Correction LLM par identifiant.
    user = _build_user_prompt(words, glossary, media)
    raw = _call_openrouter(SYSTEM_PROMPT, user)
    corrections = _parse_corrections(raw, len(words))
    return _apply_corrections(words, corrections)
