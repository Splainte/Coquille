"""Correction LLM contextuelle via OpenRouter.

Stratégie **timing-safe par plages** : le LLM remplace une PLAGE de mots
consécutifs [de, a] par un nouveau texte (« haute garde » → « haut de gamme »,
« compte la vol » → « qu'on te la vole »). Les bornes temporelles de la plage
(début du 1er mot, fin du dernier) sont celles de la transcription ; les mots
nouveaux s'y répartissent au prorata de leur longueur. Les erreurs courantes
tiennent en 1 à 4 mots, donc le timing reste ancré à quelques centièmes près.
(L'ancien contrat « 1 mot ↔ 1 id » forçait le modèle à faire glisser des mots
d'une case à l'autre pour corriger une expression : résultats absurdes.)

Étapes :
1. pré-passe glossaire déterministe (gère aussi les aliases multi-mots) ;
2. appel LLM avec le glossaire en contexte + les mots à faible confiance signalés ;
3. application (ou suggestion) des plages, après garde-fou sur les chiffres.

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

    index: int            # 1er mot de la plage (position après glossaire)
    original: str
    suggested: str        # "" = suppression proposée
    start: float          # timecode (s) du mot — pour le lien « localiser »
    end_index: int | None = None  # dernier mot de la plage (None = index)


@dataclass
class Span:
    """Remplacement de words[de..a] (bornes incluses) par `texte` ("" = suppression)."""

    de: int
    a: int
    texte: str


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
Tu corriges la transcription automatique d'une vidéo de médias high-tech et \
automobile francophones (Frandroid, Numerama, Clubic, Survoltés) : produits, \
marques, voitures électriques, jargon technique. Un glossaire déterministe a DÉJÀ \
été appliqué avant toi : tu interviens à la marge.

Tu reçois la transcription mot à mot : « id<TAB>mot ». Corrige UNIQUEMENT les \
erreurs de transcription MANIFESTES :
1. mots mal reconnus : homophones, noms propres, marques et modèles mal \
   orthographiés, anglicismes phonétiquement mal transcrits, expressions \
   déformées (« haute garde » → « haut de gamme ») ;
2. fautes de français que l'audio ne distingue pas : infinitif / participe \
   (« brancher » → « branché »), « a »/« à », « ou »/« où », « ces »/« ses », \
   « sa »/« ça », accords en genre et nombre. Corrige-les avec ASSURANCE quand le \
   contexte les rend certains.

Chaque correction remplace une PLAGE de mots consécutifs [de, a] (bornes \
incluses) par un nouveau texte : plusieurs mots possibles, ou "" pour supprimer \
(hésitation « euh »). Pour insérer un mot oublié, remplace le mot voisin par \
« voisin + mot ». Garde les plages les plus COURTES possible (1 à 4 mots).

RÈGLES ABSOLUES :
- Ne modifie JAMAIS un chiffre, un numéro de version ou de modèle (« S26 », \
  « One UI 8.5 », « EX60 »). Tu ne connais PAS les produits les plus récents : \
  « S26 » reste « S26 », même si tu crois que c'est « S25 ».
- N'INVENTE pas de faits : ne remplace pas un nom de produit ou de fonction par \
  un autre que tu crois plus probable (« Android Auto » reste « Android Auto »).
- Tu NE reformules PAS, NE résumes PAS, NE traduis PAS : le texte doit rester \
  exactement ce qui est dit à l'oral, tics de langage compris.
- Garde le style du média : « IA » reste « IA », « km » reste « km ».
- Respecte la casse et l'orthographe du glossaire fourni.
- Ne touche pas à ce qui est déjà correct. Dans le doute, NE CORRIGE PAS.

Réponds UNIQUEMENT via l'outil soumettre_corrections, avec la liste (souvent \
courte, parfois vide) des plages à remplacer.
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


_TRAILING_PUNCT = re.compile(r"[.,;:!?…»)]+$")


def _keep_trailing_punct(sp: Span, original: str) -> Span:
    """Le modèle perd parfois la ponctuation finale (« Haute Garde. » →
    « haut de gamme ») : on la remet, sinon la fin de phrase disparaît et le
    découpage en sous-titres avec. Une suppression ("") reste une suppression."""
    texte = sp.texte.strip()
    m = _TRAILING_PUNCT.search(original.strip())
    if texte and m and not _TRAILING_PUNCT.search(texte):
        return Span(sp.de, sp.a, texte + m.group())
    return sp


def _digits(s: str) -> str:
    """Suite des chiffres d'une chaîne (pour comparer S26 vs S25, 8.5 vs 8.8)."""
    return "".join(ch for ch in s if ch.isdigit())


def apply_spans(words: list[Word], spans: list[Span]) -> list[Word]:
    """Applique des remplacements de plages. Les plages qui se chevauchent sont
    ignorées (la première gagne). Les mots nouveaux se partagent l'intervalle
    [début du 1er mot, fin du dernier] au prorata de leur nombre de caractères."""
    out: list[Word] = []
    i = 0
    for sp in sorted(spans, key=lambda s: (s.de, s.a)):
        if sp.de < i or not (0 <= sp.de <= sp.a < len(words)):
            continue
        out.extend(words[i : sp.de])
        seg = words[sp.de : sp.a + 1]
        toks = sp.texte.split()
        if len(toks) == 1 and len(seg) == 1:
            out.append(seg[0].with_text(toks[0]))  # cas simple : timing intact
        elif toks:
            t0, t1 = seg[0].start, seg[-1].end
            total = sum(len(t) for t in toks)
            t = t0
            for k, tok in enumerate(toks):
                d = (t1 - t0) * len(tok) / total
                out.append(
                    replace(
                        seg[0] if k == 0 else seg[-1],
                        text=tok, start=t, duration=d, confidence=None,
                        is_eos=seg[-1].is_eos if k == len(toks) - 1 else False,
                    )
                )
                t += d
        i = sp.a + 1
    out.extend(words[i:])
    return out


# Outil imposé au modèle : garantit une sortie STRUCTURÉE (JSON conforme au
# schéma), au lieu de prose. response_format=json_object n'est pas respecté par
# les modèles Anthropic via OpenRouter → le tool calling est la voie fiable.
_TOOL = {
    "type": "function",
    "function": {
        "name": "soumettre_corrections",
        "description": "Soumet les plages de mots à remplacer.",
        "parameters": {
            "type": "object",
            "properties": {
                "corrections": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "de": {"type": "integer"},
                            "a": {"type": "integer"},
                            "texte": {"type": "string"},
                        },
                        "required": ["de", "a", "texte"],
                    },
                }
            },
            "required": ["corrections"],
        },
    },
}


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
        "max_tokens": 4096,
        "tools": [_TOOL],
        "tool_choice": {
            "type": "function",
            "function": {"name": "soumettre_corrections"},
        },
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if config.OPENROUTER_REASONING_EFFORT:
        payload["reasoning"] = {"effort": config.OPENROUTER_REASONING_EFFORT}
    with httpx.Client(timeout=120) as client:
        resp = client.post(
            f"{config.OPENROUTER_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
        )
        if resp.status_code == 400 and "tool_choice" in resp.text:
            # Certains modèles récents (claude-sonnet-5.5 via Bedrock) refusent
            # l'outil IMPOSÉ mais l'appellent d'eux-mêmes en mode "auto".
            payload["tool_choice"] = "auto"
            resp = client.post(
                f"{config.OPENROUTER_BASE_URL}/chat/completions",
                headers=headers,
                json=payload,
            )
        resp.raise_for_status()
        msg = resp.json()["choices"][0]["message"]
        tool_calls = msg.get("tool_calls") or []
        if tool_calls:
            return tool_calls[0]["function"]["arguments"]
        return msg.get("content") or ""  # repli (modèle non conforme)


def _parse_spans(raw: str, n_words: int) -> list[Span]:
    """Plages renvoyées par le LLM. Accepte aussi l'ancien format mot à mot
    {"words": [{"id", "text"}]} (plage d'un mot)."""
    data = json.loads(_FENCE.sub("", raw.strip()))
    spans: list[Span] = []
    for item in data.get("corrections", []) or []:
        try:
            de, a = int(item["de"]), int(item["a"])
            texte = str(item["texte"])
        except (KeyError, ValueError, TypeError):
            continue
        if 0 <= de <= a < n_words:
            spans.append(Span(de, a, texte))
    for i, text in _parse_corrections(raw, n_words).items():
        spans.append(Span(i, i, text))
    return spans


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
    raw_spans: list[Span] | None = None
    last_exc: Exception | None = None
    for _attempt in range(3):  # OpenRouter renvoie parfois un contenu vide
        try:
            raw = _call_openrouter(SYSTEM_PROMPT, user)
            raw_spans = _parse_spans(raw, len(glossed))
            break
        except Exception as exc:  # réseau, HTTP, JSON vide, clé invalide…
            last_exc = exc
    if raw_spans is None:
        return CorrectionResult(
            words=glossed,
            used_llm=False,
            model=config.OPENROUTER_MODEL,
            error=f"{type(last_exc).__name__}: {last_exc}",
            warnings=["Correction LLM indisponible : repli sur le glossaire."],
            items=gloss_items,
        )

    def original(sp: Span) -> str:
        return " ".join(w.text for w in glossed[sp.de : sp.a + 1])

    # Garde-fou déterministe : le LLM n'a JAMAIS le droit de modifier les chiffres
    # (S26→S25, 8.5→8.8…). L'audio transcrit les nombres de façon fiable, et le
    # modèle a tendance à les « corriger » vers ce qu'il croit savoir.
    spans: list[Span] = []
    rejected = 0
    for sp in raw_spans:
        sp = _keep_trailing_punct(sp, original(sp))
        if _digits(sp.texte) != _digits(original(sp)):
            rejected += 1
            continue
        if sp.texte.split() == original(sp).split():
            continue  # aucun changement réel
        spans.append(sp)
    # Plages qui se chevauchent : on garde la première (ordre du texte).
    spans.sort(key=lambda sp: (sp.de, sp.a))
    kept: list[Span] = []
    for sp in spans:
        if kept and sp.de <= kept[-1].a:
            continue
        kept.append(sp)

    warnings: list[str] = []
    if rejected:
        warnings.append(
            f"{rejected} correction(s) LLM ignorée(s) : modification de chiffres "
            f"interdite (numéros de version/modèle protégés)."
        )

    if not apply_llm:
        # Mode suggestion : on n'applique RIEN du LLM, on remonte des propositions.
        suggestions = [
            Suggestion(sp.de, original(sp), sp.texte, glossed[sp.de].start, sp.a)
            for sp in kept
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
        Correction(original(sp), sp.texte, glossed[sp.de].start, "llm") for sp in kept
    ]
    return CorrectionResult(
        words=apply_spans(glossed, kept),
        used_llm=True,
        model=config.OPENROUTER_MODEL,
        n_corrections=len(llm_items),
        warnings=warnings,
        items=gloss_items + llm_items,
    )
