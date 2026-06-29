"""Tests du cœur déterministe (stdlib unittest, sans dépendance).

Lancer depuis backend/ :  python -m unittest discover -s tests -v
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from coquille import config, correct as correct_mod
from coquille.correct import Correction, CorrectionResult, _parse_corrections, correct
from coquille.align import align
from coquille.config import SegmentationRules, load_preset, list_presets
from coquille.glossary import load_glossary, Glossary, Term
from coquille.models import Cue, Word
from coquille.pipeline import process
from coquille.segment import segment, render, _split_lines
from coquille.srt import to_srt
from coquille.transcribe import (
    _fw_segments_to_words,
    _voxtral_json_to_words,
    list_engines,
    load_premiere_transcript,
    resolve_engine,
)


def W(text, start, dur, conf=None, **kw):
    return Word(text=text, start=start, duration=dur, confidence=conf, **kw)


class TestGlossary(unittest.TestCase):
    def setUp(self):
        self.g = Glossary(terms=[
            Term("iPhone", ["i phone", "iphone"]),
            Term("Wi-Fi", ["wifi", "wi fi"]),
        ])

    def test_apply_words_multiword_merges_timing(self):
        words = [W("le", 0.0, 0.2), W("i", 0.2, 0.2), W("phone", 0.4, 0.3)]
        out = self.g.apply_words(words)
        self.assertEqual([w.text for w in out], ["le", "iPhone"])
        merged = out[1]
        self.assertAlmostEqual(merged.start, 0.2)
        self.assertAlmostEqual(merged.end, 0.7)  # couvre i + phone

    def test_apply_words_casing(self):
        words = [W("iphone", 0.0, 0.3), W("wifi", 0.3, 0.3)]
        out = self.g.apply_words(words)
        self.assertEqual([w.text for w in out], ["iPhone", "Wi-Fi"])

    def test_apply_words_trace_records_changes(self):
        words = [W("le", 0.0, 0.2), W("i", 0.2, 0.2), W("phone", 0.4, 0.3)]
        trace = []
        self.g.apply_words(words, trace=trace)
        self.assertEqual(len(trace), 1)
        self.assertEqual(trace[0]["original"], "i phone")
        self.assertEqual(trace[0]["corrected"], "iPhone")
        self.assertAlmostEqual(trace[0]["start"], 0.2)

    def test_apply_words_trace_skips_noop(self):
        # déjà bien orthographié : aucune correction à journaliser
        words = [W("iPhone", 0.0, 0.3)]
        trace = []
        self.g.apply_words(words, trace=trace)
        self.assertEqual(trace, [])

    def test_apply_words_preserves_attached_punctuation(self):
        # un terme suivi d'une ponctuation garde sa virgule / son point final
        words = [W("iphone,", 0.0, 0.3), W("wifi.", 0.3, 0.3)]
        trace = []
        out = self.g.apply_words(words, trace=trace)
        self.assertEqual([w.text for w in out], ["iPhone,", "Wi-Fi."])
        # casse déjà correcte + ponctuation conservée : "Samsung," n'est pas un faux positif
        only_casing = self.g.apply_words([W("Wi-Fi.", 0.0, 0.3)], trace=(t2 := []))
        self.assertEqual(t2, [])  # rien à corriger, donc rien dans la liste

    def test_real_glossaries_load(self):
        for media in ("frandroid", "numerama", "clubic"):
            g = load_glossary(media)
            self.assertTrue(g.canonical_list())
            # le glossaire de base est fusionné
            self.assertIn("iPhone", g.canonical_list())


class TestAlign(unittest.TestCase):
    def test_borrows_premiere_timecodes_on_match(self):
        whisper = [W("le", 0.05, 0.2), W("Pixel", 0.30, 0.4)]
        premiere = [W("le", 0.00, 0.25, conf=0.99), W("pixelle", 0.27, 0.45, conf=0.40)]
        out = align(whisper, premiere)
        self.assertEqual([w.text for w in out], ["le", "Pixel"])  # texte Whisper
        self.assertAlmostEqual(out[0].start, 0.00)                # timecode Premiere
        self.assertEqual(out[0].confidence, 0.99)

    def test_divergence_lowers_confidence(self):
        whisper = [W("Snapdragon", 0.0, 0.5, conf=0.9)]
        premiere = [W("snap", 0.0, 0.25, conf=0.3), W("dragonne", 0.25, 0.25, conf=0.3)]
        out = align(whisper, premiere)
        self.assertEqual(out[0].text, "Snapdragon")
        self.assertLessEqual(out[0].confidence, 0.3)  # signalé comme douteux

    def test_no_premiere_returns_whisper(self):
        whisper = [W("salut", 0.0, 0.3)]
        self.assertEqual(align(whisper, []), whisper)


class TestSegment(unittest.TestCase):
    def setUp(self):
        self.rules = SegmentationRules(max_chars_per_line=20, max_duration=4.0, gap_split=0.6)

    def test_splits_on_long_pause(self):
        words = [W("Bonjour", 0.0, 0.5), W("à", 0.6, 0.2), W("tous", 0.8, 0.3),
                 W("Aujourd'hui", 3.0, 0.8)]  # grande pause avant "Aujourd'hui"
        cues = segment(words, self.rules)
        self.assertEqual(len(cues), 2)
        self.assertIn("Aujourd'hui", cues[1].text)

    def test_line_balancing_two_lines(self):
        rules = SegmentationRules(max_chars_per_line=18)
        words = [W(w, i * 0.3, 0.3) for i, w in enumerate(
            ["Voici", "un", "test", "de", "retour", "à", "la", "ligne"])]
        lines = _split_lines(words, rules)
        self.assertEqual(len(lines), 2)
        for ln in lines:
            self.assertLessEqual(len(ln), rules.max_chars_per_line)
        # équilibre raisonnable
        self.assertLessEqual(abs(len(lines[0]) - len(lines[1])), 8)

    def test_avoids_dangling_function_word(self):
        # Coupe de préférence sans laisser "de" en fin de ligne 1.
        rules = SegmentationRules(max_chars_per_line=14)
        words = [W(w, i * 0.3, 0.3) for i, w in enumerate(
            ["parler", "de", "la", "nouvelle", "puce"])]
        lines = _split_lines(words, rules)
        self.assertFalse(lines[0].rstrip().endswith(" de"))

    def test_min_duration_enforced(self):
        rules = SegmentationRules(min_duration=1.0)
        words = [W("Court", 0.0, 0.2)]
        cues = segment(words, rules)
        self.assertGreaterEqual(cues[0].duration, 1.0)

    def test_cuts_at_sentence_end_even_without_pause(self):
        # Deux phrases qui s'enchaînent sans pause : la coupe doit tomber à la fin
        # de la 1re phrase, pas au milieu de la 2nde.
        rules = SegmentationRules(max_chars_per_line=60, max_lines=2,
                                  max_duration=20, gap_split=0.6, min_duration=0.1)
        words = [W("Le", 0.0, 0.3), W("Pixel", 0.3, 0.3), W("est", 0.6, 0.3),
                 W("rapide.", 0.9, 0.3), W("Il", 1.3, 0.3), W("chauffe", 1.6, 0.3),
                 W("un", 1.9, 0.3), W("peu.", 2.2, 0.3)]
        cues = segment(words, rules)
        self.assertEqual(len(cues), 2)
        self.assertIn("rapide", cues[0].text)
        self.assertIn("chauffe", cues[1].text)

    def test_long_sentence_cuts_at_question_mark(self):
        # Reproduit le cas réel : "…Galaxy Z Fold 7? Alors vous le savez…" doit
        # couper après le "?", pas au milieu de "Alors vous le savez".
        rules = load_preset("frandroid").segmentation
        text = ("Et si vous tiriez vraiment parti de votre Galaxy Z Fold 7? "
                "Alors vous le savez, chez Frandroid, on aime bidouiller.")
        toks = text.split()
        words = [W(t, i * 0.3, 0.3) for i, t in enumerate(toks)]
        cues = segment(words, rules)
        first = cues[0].text.replace("\n", " ")
        # Typo FR : espace insécable avant le "?" (cf. test_french_spacing_*).
        self.assertTrue(first.rstrip().endswith("7\u00A0?"), first)
        self.assertNotIn("Alors", first)
        self.assertIn("Alors", cues[1].text)

    def test_no_empty_frame_when_no_real_silence(self):
        rules = SegmentationRules(min_duration=0.1, gap_split=0.6, max_hold_gap=0.5,
                                  max_chars_per_line=40)
        words = [W("Oui.", 0.0, 0.3), W("Non.", 0.6, 0.3)]  # écart 0.3 < hold
        cues = segment(words, rules)
        self.assertEqual(len(cues), 2)
        self.assertAlmostEqual(cues[0].end, cues[1].start)  # coupe directe, sans blanc

    def test_keeps_blank_on_real_silence(self):
        rules = SegmentationRules(min_duration=0.1, gap_split=0.6, max_hold_gap=0.5,
                                  max_chars_per_line=40)
        words = [W("Oui.", 0.0, 0.3), W("Non.", 1.4, 0.3)]  # écart 1.1 = vrai silence
        cues = segment(words, rules)
        self.assertEqual(len(cues), 2)
        self.assertLess(cues[0].end, cues[1].start)  # blanc conservé

    def test_no_overlap_when_cue_shorter_than_min_duration(self):
        # Un ST court ne doit jamais empiéter sur le suivant (régression :
        # le plancher de durée min poussait sa fin après le début du suivant).
        rules = SegmentationRules(min_duration=1.0, gap_split=0.6,
                                  max_hold_gap=0.5, max_chars_per_line=40)
        words = [W("Oui.", 23.9, 0.16), W("Bon", 24.04, 0.3), W("alors", 24.34, 0.3)]
        cues = segment(words, rules)
        self.assertGreaterEqual(len(cues), 2)
        for a, b in zip(cues, cues[1:]):
            self.assertLessEqual(a.end, b.start)  # aucun chevauchement

    def test_split_lines_picks_most_balanced(self):
        # 2 lignes : on veut l'équilibre, pas "presque tout puis un reste".
        rules = SegmentationRules(max_chars_per_line=24)
        toks = "Salut, voici un petit essai".split()
        words = [W(t, i * 0.3, 0.3) for i, t in enumerate(toks)]
        lines = _split_lines(words, rules)
        self.assertEqual(len(lines), 2)
        self.assertLessEqual(abs(len(lines[0]) - len(lines[1])), 4)
        for ln in lines:
            self.assertLessEqual(len(ln), 24)

    def test_no_orphan_word_as_own_cue(self):
        # Régression : "...encore meilleur." laissait "meilleur." seul dans un
        # cue. On veut le rééquilibrer avec le cue précédent (>= 2 mots partout).
        rules = SegmentationRules(max_chars_per_line=24, gap_split=5.0)
        toks = "et qui vont rendre votre quotidien encore meilleur.".split()
        words = [W(t, i * 0.3, 0.3) for i, t in enumerate(toks)]
        cues = segment(words, rules)
        self.assertGreaterEqual(len(cues), 2)
        # Aucun cue ne se réduit à un mot solitaire.
        for c in cues:
            self.assertGreater(len(c.text.split()), 1, c.text)

    def test_never_breaks_glued_tokens_across_lines(self):
        # Régression : la coupure tombait entre "high" et "-tech" → "high\n-tech".
        rules = SegmentationRules(max_chars_per_line=24)
        toks = ["appareils", "high", "-tech", "favoris."]
        words = [W(t, i * 0.3, 0.3) for i, t in enumerate(toks)]
        lines = _split_lines(words, rules)
        joined = "\n".join(lines)
        self.assertIn("high-tech", joined)
        self.assertNotIn("high\n", joined)
        self.assertNotIn("\n-tech", joined)

    def test_max_chars_caps_cue(self):
        rules = SegmentationRules(max_chars_per_line=10, gap_split=5.0, max_duration=60)
        words = [W(w, i * 0.2, 0.2) for i, w in enumerate(
            ["un", "deux", "trois", "quatre", "cinq", "six", "sept", "huit"])]
        cues = segment(words, rules)
        for c in cues:
            self.assertLessEqual(c.char_count, rules.max_chars_per_line * rules.max_lines)


class TestRender(unittest.TestCase):
    def test_punctuation_no_leading_space(self):
        words = [W("Bonjour", 0, 0.3), W(",", 0.3, 0.0, type="punctuation"),
                 W("monde", 0.3, 0.3)]
        self.assertEqual(render(words), "Bonjour, monde")

    def test_french_spacing_before_high_punctuation(self):
        # Typo FR : espace insécable avant ? ! ; : (collé OU token séparé).
        nbsp = "\u00A0"
        self.assertEqual(render([W("Fold", 0, 0.3), W("7?", 0.3, 0.3)]),
                         "Fold 7" + nbsp + "?")
        self.assertEqual(render([W("Quoi", 0, 0.3), W("?", 0.3, 0.0)]),
                         "Quoi" + nbsp + "?")
        self.assertEqual(render([W("Génial", 0, 0.3), W("!", 0.3, 0.0)]),
                         "Génial" + nbsp + "!")
        self.assertEqual(render([W("Attention", 0, 0.3), W(":", 0.3, 0.0),
                                 W("danger", 0.3, 0.3)]),
                         "Attention" + nbsp + ": danger")
        # Une heure (chiffres) ne prend pas d'espace avant ":".
        self.assertEqual(render([W("à", 0, 0.2), W("10:30", 0.2, 0.3)]), "à 10:30")

    def test_hyphen_and_apostrophe_glue(self):
        # Token suivant à tête '-' ou apostrophe : pas d'espace parasite.
        self.assertEqual(render([W("high", 0, 0.3), W("-tech", 0.3, 0.3)]), "high-tech")
        self.assertEqual(render([W("n", 0, 0.2), W("'allez", 0.2, 0.3)]), "n'allez")
        # Variante tokenisée en trois morceaux.
        self.assertEqual(
            render([W("high", 0, 0.2), W("-", 0.2, 0.0), W("tech", 0.2, 0.2)]),
            "high-tech",
        )


class TestSrt(unittest.TestCase):
    def test_format(self):
        cues = [Cue(1, 0.0, 1.5, ["Bonjour", "le monde"])]
        out = to_srt(cues)
        self.assertIn("00:00:00,000 --> 00:00:01,500", out)
        self.assertIn("Bonjour\nle monde", out)
        self.assertTrue(out.startswith("1\n"))

    def test_timecode_hours(self):
        cues = [Cue(1, 3661.5, 3662.0, ["x"])]
        self.assertIn("01:01:01,500 --> 01:01:02,000", to_srt(cues))


class TestPremiereTranscript(unittest.TestCase):
    def test_parse_basic(self):
        data = {
            "language": "fr-FR",
            "speakers": [{"id": "spk-1", "name": "Présentateur"}],
            "transcript": [{
                "speaker": "spk-1",
                "words": [
                    {"text": "Bonjour", "start": 0.0, "duration": 0.4, "confidence": 0.98},
                    {"text": ",", "start": 0.4, "duration": 0.0, "type": "punctuation"},
                    {"text": "Pixelle", "start": 0.5, "duration": 0.5, "confidence": 0.42},
                ],
            }],
        }
        words = load_premiere_transcript(data)
        self.assertEqual([w.text for w in words], ["Bonjour", ",", "Pixelle"])
        self.assertEqual(words[0].speaker, "Présentateur")
        self.assertEqual(words[1].type, "punctuation")
        self.assertEqual(words[2].confidence, 0.42)


class _FWWord:
    """Imite un mot faster-whisper (attributs .word/.start/.end/.probability)."""
    def __init__(self, word, start, end, probability=None):
        self.word, self.start, self.end, self.probability = word, start, end, probability


class _FWSegment:
    def __init__(self, words):
        self.words = words


class TestTranscriptionLayer(unittest.TestCase):
    def test_resolve_engine_explicit(self):
        self.assertEqual(resolve_engine("large-v3"), "large-v3")
        self.assertEqual(resolve_engine("small"), "small")
        self.assertEqual(resolve_engine("voxtral"), "voxtral")

    def test_resolve_engine_auto_picks_by_hardware(self):
        # En environnement de test (pas de GPU) → small. Sinon large-v3.
        self.assertIn(resolve_engine("auto"), ("small", "large-v3"))
        self.assertEqual(resolve_engine(""), resolve_engine("auto"))

    def test_resolve_engine_unknown_raises(self):
        with self.assertRaises(ValueError):
            resolve_engine("gpt-9")

    def test_list_engines_catalog(self):
        cat = list_engines()
        ids = {e["id"] for e in cat["engines"]}
        self.assertEqual(ids, {"auto", "large-v3", "small", "voxtral"})
        self.assertIn(cat["auto_resolves_to"], ("small", "large-v3"))

    def test_fw_segments_mapping(self):
        segs = [_FWSegment([
            _FWWord(" Le", 0.0, 0.3, 0.91),
            _FWWord(" ", 0.3, 0.3, 0.5),       # blanc → ignoré
            _FWWord("Pixel", 0.4, 0.8, None),  # sans proba → confidence None
        ])]
        words = _fw_segments_to_words(segs)
        self.assertEqual([w.text for w in words], ["Le", "Pixel"])
        self.assertAlmostEqual(words[0].duration, 0.3)
        self.assertEqual(words[0].confidence, 0.91)
        self.assertIsNone(words[1].confidence)

    def test_voxtral_mapping_words_and_segment_fallback(self):
        flat = {"words": [{"text": "Bonjour", "start": 0.0, "end": 0.5}]}
        self.assertEqual([w.text for w in _voxtral_json_to_words(flat)], ["Bonjour"])
        nested = {"segments": [{"words": [{"word": "Salut", "start": 1.0, "end": 1.4}]}]}
        out = _voxtral_json_to_words(nested)
        self.assertEqual(out[0].text, "Salut")
        self.assertAlmostEqual(out[0].duration, 0.4)


class TestCorrectLLM(unittest.TestCase):
    """Passe de correction LLM (appel réseau mocké)."""

    def setUp(self):
        self.g = Glossary(terms=[Term("AMOLED", ["a moled", "amoled"])])
        # Active le chemin LLM : clé présente, hors-ligne désactivé.
        self._prev_key = config.OPENROUTER_API_KEY
        self._prev_off = config.CORRECT_OFFLINE
        config.OPENROUTER_API_KEY = "test-key"
        config.CORRECT_OFFLINE = False

    def tearDown(self):
        config.OPENROUTER_API_KEY = self._prev_key
        config.CORRECT_OFFLINE = self._prev_off

    def test_parse_corrections_strips_markdown_fence(self):
        raw = '```json\n{"words": [{"id": 0, "text": "Pixel"}]}\n```'
        self.assertEqual(_parse_corrections(raw, 3), {0: "Pixel"})

    def test_parse_corrections_ignores_out_of_range_ids(self):
        raw = '{"words": [{"id": 9, "text": "x"}, {"id": 1, "text": "ok"}]}'
        self.assertEqual(_parse_corrections(raw, 3), {1: "ok"})

    def test_llm_applies_corrections_by_id(self):
        words = [W("le", 0.0, 0.2), W("pixelle", 0.2, 0.4), W("écran", 0.6, 0.5)]
        raw = '{"words": [{"id": 1, "text": "Pixel"}]}'
        with mock.patch.object(correct_mod, "_call_openrouter", return_value=raw):
            res = correct(words, self.g, "frandroid")
        self.assertIsInstance(res, CorrectionResult)
        self.assertTrue(res.used_llm)
        self.assertEqual([w.text for w in res.words], ["le", "Pixel", "écran"])
        self.assertEqual(res.n_corrections, 1)
        # le timecode du mot corrigé est préservé (timing-safe)
        self.assertAlmostEqual(res.words[1].start, 0.2)

    def test_llm_count_ignores_noop_corrections(self):
        words = [W("Pixel", 0.0, 0.2), W("écran", 0.2, 0.4)]
        # le LLM renvoie un mot identique : ne doit pas compter comme correction
        raw = '{"words": [{"id": 0, "text": "Pixel"}]}'
        with mock.patch.object(correct_mod, "_call_openrouter", return_value=raw):
            res = correct(words, self.g, "frandroid")
        self.assertEqual(res.n_corrections, 0)

    def test_llm_failure_falls_back_to_glossary(self):
        words = [W("a", 0.0, 0.2), W("moled", 0.2, 0.4)]
        boom = mock.Mock(side_effect=RuntimeError("réseau coupé"))
        with mock.patch.object(correct_mod, "_call_openrouter", boom):
            res = correct(words, self.g, "frandroid")
        self.assertFalse(res.used_llm)
        self.assertIsNotNone(res.error)
        self.assertTrue(res.warnings)
        # le repli glossaire a quand même corrigé "a moled" -> "AMOLED"
        self.assertEqual([w.text for w in res.words], ["AMOLED"])

    def test_offline_skips_llm(self):
        words = [W("amoled", 0.0, 0.3)]
        with mock.patch.object(correct_mod, "_call_openrouter") as called:
            res = correct(words, self.g, "frandroid", offline=True)
        called.assert_not_called()
        self.assertFalse(res.used_llm)
        self.assertEqual([w.text for w in res.words], ["AMOLED"])

    def test_items_combine_glossary_and_llm_with_source_and_tc(self):
        words = [W("a", 0.0, 0.2), W("moled", 0.2, 0.4), W("pixelle", 0.6, 0.4)]
        # le glossaire fusionne "a moled" -> "AMOLED" : "pixelle" passe en id 1,
        # c'est l'indexation APRÈS glossaire que voit le LLM.
        raw = '{"words": [{"id": 1, "text": "Pixel"}]}'
        with mock.patch.object(correct_mod, "_call_openrouter", return_value=raw):
            res = correct(words, self.g, "frandroid")
        by_src = {it.source: it for it in res.items}
        self.assertEqual(by_src["glossary"].corrected, "AMOLED")
        self.assertAlmostEqual(by_src["glossary"].start, 0.0)
        self.assertEqual(by_src["llm"].original, "pixelle")
        self.assertEqual(by_src["llm"].corrected, "Pixel")
        self.assertAlmostEqual(by_src["llm"].start, 0.6)

    def test_items_present_on_llm_failure(self):
        words = [W("a", 0.0, 0.2), W("moled", 0.2, 0.4)]
        boom = mock.Mock(side_effect=RuntimeError("ko"))
        with mock.patch.object(correct_mod, "_call_openrouter", boom):
            res = correct(words, self.g, "frandroid")
        # même en repli, la correction glossaire est listée
        self.assertEqual([it.corrected for it in res.items], ["AMOLED"])


class TestPipelineEndToEnd(unittest.TestCase):
    def test_hybrid_offline(self):
        # Whisper a un meilleur texte, Premiere de meilleurs timecodes.
        whisper = [
            W("Le", 0.05, 0.2), W("nouveau", 0.25, 0.4), W("Pixel", 0.65, 0.4),
            W("a", 1.05, 0.1), W("un", 1.15, 0.2), W("écran", 1.35, 0.5),
            W("AMOLED", 1.85, 0.6),
        ]
        premiere = [
            W("Le", 0.00, 0.22, conf=0.99), W("nouveau", 0.24, 0.42, conf=0.97),
            W("pixelle", 0.64, 0.42, conf=0.38), W("a", 1.04, 0.12, conf=0.95),
            W("un", 1.14, 0.22, conf=0.96), W("écran", 1.34, 0.52, conf=0.9),
            W("a", 1.84, 0.3, conf=0.3), W("moled", 2.14, 0.32, conf=0.3),
        ]
        result = process("frandroid", whisper_words=whisper,
                         premiere_words=premiere, offline=True)
        text = " ".join(w.text for w in result.words)
        self.assertIn("Pixel", text)
        self.assertNotIn("pixelle", text.lower())
        self.assertIn("AMOLED", text)        # corrigé par le glossaire Frandroid
        self.assertTrue(result.srt.strip())
        self.assertTrue(result.cues)
        # timecode du premier cue ancré sur Premiere (0.00)
        self.assertAlmostEqual(result.cues[0].start, 0.0, places=2)
        # aucune ligne ne dépasse la largeur du preset, jamais plus de 2 lignes
        rules = load_preset("frandroid").segmentation
        for c in result.cues:
            self.assertLessEqual(len(c.lines), rules.max_lines)
            for line in c.lines:
                self.assertLessEqual(len(line), rules.max_chars_per_line)

    def test_requires_a_source(self):
        with self.assertRaises(ValueError):
            process("clubic")


class TestPresets(unittest.TestCase):
    def test_three_presets_present(self):
        self.assertEqual(set(list_presets()), {"frandroid", "numerama", "clubic"})

    def test_preset_fields(self):
        p = load_preset("numerama")
        self.assertEqual(p.track_style, "Coquille — Numerama")
        self.assertGreater(p.segmentation.max_chars_per_line, 0)


if __name__ == "__main__":
    unittest.main()
