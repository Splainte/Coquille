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

    def test_media_names_merge_multiword(self):
        # « fran droïde » (2 mots) -> « Frandroid » (fusion multi-mots du glossaire)
        g = load_glossary("frandroid")
        words = [W("sur", 0.0, 0.2), W("fran", 0.2, 0.2), W("droïde", 0.4, 0.3)]
        out = g.apply_words(words)
        self.assertEqual([w.text for w in out], ["sur", "Frandroid"])


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
        words = [W("Oui,", 0.0, 0.3), W("bien", 0.3, 0.3), W("sûr.", 0.6, 0.3),
                 W("Pas", 1.2, 0.3), W("du", 1.5, 0.3), W("tout.", 1.8, 0.3)]  # écart 0.3 < hold
        cues = segment(words, rules)
        self.assertEqual(len(cues), 2)
        self.assertAlmostEqual(cues[0].end, cues[1].start)  # coupe directe, sans blanc

    def test_keeps_blank_on_real_silence(self):
        rules = SegmentationRules(min_duration=0.1, gap_split=0.6, max_hold_gap=0.5,
                                  max_chars_per_line=40)
        words = [W("Oui,", 0.0, 0.3), W("bien", 0.3, 0.3), W("sûr.", 0.6, 0.3),
                 W("Pas", 2.0, 0.3), W("du", 2.3, 0.3), W("tout.", 2.6, 0.3)]  # écart 1.1 = vrai silence
        cues = segment(words, rules)
        self.assertEqual(len(cues), 2)
        self.assertLess(cues[0].end, cues[1].start)  # blanc conservé

    def test_no_overlap_when_cue_shorter_than_min_duration(self):
        # Un ST court ne doit jamais empiéter sur le suivant (régression :
        # le plancher de durée min poussait sa fin après le début du suivant).
        rules = SegmentationRules(min_duration=1.0, gap_split=0.6,
                                  max_hold_gap=0.5, max_chars_per_line=40)
        words = [W("C'est", 23.6, 0.1), W("vrai.", 23.7, 0.16), W("Bon", 24.04, 0.3),
                 W("alors", 24.34, 0.3), W("on", 24.64, 0.2), W("continue.", 24.84, 0.4)]
        cues = segment(words, rules)
        self.assertGreaterEqual(len(cues), 2)
        for a, b in zip(cues, cues[1:]):
            self.assertLessEqual(a.end, b.start)  # aucun chevauchement

    def test_reflow_merges_orphan_fragment(self):
        # Une phrase fragmentée par des micro-pauses ne doit pas laisser de mot
        # seul (« Galaxy, » / « Et oui, ») : on recolle au voisin de la phrase.
        rules = SegmentationRules(max_chars_per_line=24, max_duration=6.0,
                                  min_duration=0.1, gap_split=0.6)
        toks = ["Et", "oui,", "si", "vous", "avez", "un", "smartphone",
                "Samsung", "Galaxy,", "ce", "sont", "des", "puces"]
        t = 0.0; words = []
        for tk in toks:
            if tk in ("si", "Galaxy,"):
                t += 0.7  # micro-pause (≥ gap_split mais pas un vrai silence)
            words.append(W(tk, t, 0.3)); t += 0.33
        cues = segment(words, rules)
        for c in cues:
            words_in = " ".join(c.lines).split()
            self.assertGreater(len(words_in), 1, "cue orphelin : " + c.text)

    def test_real_silence_keeps_split(self):
        # Un vrai silence (long) NE doit PAS être recollé, même si court.
        rules = SegmentationRules(max_chars_per_line=24, min_duration=0.1, gap_split=0.6)
        words = [W("Bonjour,", 0.0, 0.4), W("alors", 5.0, 0.3), W("on", 5.3, 0.2),
                 W("y", 5.5, 0.2), W("va.", 5.7, 0.3)]  # silence de ~4.6 s
        cues = segment(words, rules)
        self.assertEqual(cues[0].text.strip().rstrip(), "Bonjour,")

    def test_no_single_word_on_second_line(self):
        # « à ses fonctionnalités IA. » (max 24) ne doit pas isoler « IA. ».
        rules = SegmentationRules(max_chars_per_line=24)
        words = [W(t, i * 0.4, 0.4) for i, t in enumerate(
            ["à", "ses", "fonctionnalités", "IA."])]
        lines = _split_lines(words, rules)
        self.assertEqual(len(lines), 2)
        for ln in lines:
            self.assertGreater(len(ln.split()), 1, "ligne d'un seul mot : " + ln)

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

    def test_no_short_sentence_tail(self):
        # Régression (vidéo Volvo) : un remplissage glouton laissait la fin de
        # phrase seule à l'écran (« de la batterie. » 0,4 s). Le découpage
        # optimal répartit la phrase pour qu'aucun cue ne flashe.
        rules = SegmentationRules(max_chars_per_line=24, min_duration=1.0)
        toks = ("Et ça charge entre 16 et 19 minutes selon "
                "la taille de la batterie.").split()
        words = [W(t, i * 0.25, 0.25) for i, t in enumerate(toks)]
        cues = segment(words, rules)
        self.assertGreaterEqual(len(cues), 2)
        for c in cues:
            self.assertGreaterEqual(c.duration, 0.9, c.text)

    def test_cue_does_not_end_on_function_word(self):
        # « le développement et la » | « production… » : interdit en fin de cue.
        rules = SegmentationRules(max_chars_per_line=24, min_duration=0.5)
        toks = ("le développement et la production de ce EX60 "
                "restent en Suède.").split()
        words = [W(t, i * 0.3, 0.3) for i, t in enumerate(toks)]
        cues = segment(words, rules)
        self.assertGreaterEqual(len(cues), 2)
        for c in cues[:-1]:
            last = c.text.split()[-1].lower()
            self.assertNotIn(last, ("la", "de", "et", "le", "ce"), c.text)

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

    def test_fw_drops_no_speech_hallucination(self):
        # « Générique » inventé sur de la musique : no_speech_prob élevé + doute.
        hallu = _FWSegment([_FWWord(" Générique", 0.0, 0.56, 0.69)])
        hallu.no_speech_prob, hallu.avg_logprob = 0.93, -0.66
        real = _FWSegment([_FWWord(" Bonjour", 1.0, 1.4, 0.95)])
        real.no_speech_prob, real.avg_logprob = 0.02, -0.2
        self.assertEqual([w.text for w in _fw_segments_to_words([hallu, real])], ["Bonjour"])

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

    def test_llm_cannot_change_digits(self):
        # S26 -> S25 et 8.5 -> 8.8 doivent être rejetés (chiffres protégés)
        words = [W("S26", 0.0, 0.3), W("OneUI", 0.4, 0.3), W("8.5", 0.7, 0.3)]
        raw = ('{"words": [{"id": 0, "text": "S25"}, '
               '{"id": 2, "text": "8.8"}]}')
        with mock.patch.object(correct_mod, "_call_openrouter", return_value=raw):
            res = correct(words, self.g, "frandroid")
        texts = [w.text for w in res.words]
        self.assertIn("S26", texts)        # inchangé
        self.assertIn("8.5", texts)        # inchangé
        self.assertNotIn("S25", texts)
        self.assertNotIn("8.8", texts)
        self.assertEqual(res.n_corrections, 0)
        self.assertTrue(res.warnings)      # garde-fou signalé

    def test_llm_casing_with_digits_allowed(self):
        # la casse autour d'un chiffre reste permise (mêmes chiffres)
        words = [W("iphone15", 0.0, 0.3)]
        raw = '{"words": [{"id": 0, "text": "iPhone15"}]}'
        with mock.patch.object(correct_mod, "_call_openrouter", return_value=raw):
            res = correct(words, self.g, "frandroid")
        self.assertEqual([w.text for w in res.words], ["iPhone15"])
        self.assertFalse(res.warnings)

    def test_suggest_mode_does_not_apply_llm(self):
        words = [W("le", 0.0, 0.2), W("pixelle", 0.2, 0.4), W("amoled", 0.6, 0.3)]
        raw = '{"words": [{"id": 1, "text": "Pixel"}]}'
        with mock.patch.object(correct_mod, "_call_openrouter", return_value=raw):
            res = correct(words, self.g, "frandroid", apply_llm=False)
        # le LLM n'est PAS appliqué : "pixelle" reste tel quel dans les mots
        self.assertEqual([w.text for w in res.words], ["le", "pixelle", "AMOLED"])
        # mais il est proposé en suggestion, avec index + timecode
        self.assertEqual(len(res.suggestions), 1)
        sg = res.suggestions[0]
        self.assertEqual(sg.index, 1)
        self.assertEqual(sg.original, "pixelle")
        self.assertEqual(sg.suggested, "Pixel")
        self.assertAlmostEqual(sg.start, 0.2)
        # le glossaire, lui, est bien appliqué (et listé dans items, pas suggestions)
        self.assertTrue(any(it.corrected == "AMOLED" for it in res.items))

    def test_span_replaces_expression_within_its_time_bounds(self):
        # « haute garde » → « haut de gamme » : 2 mots → 3, bornes conservées.
        words = [W("version", 0.0, 0.5), W("haute", 0.5, 0.3), W("garde.", 0.8, 0.4)]
        raw = '{"corrections": [{"de": 1, "a": 2, "texte": "haut de gamme."}]}'
        with mock.patch.object(correct_mod, "_call_openrouter", return_value=raw):
            res = correct(words, self.g, "frandroid")
        self.assertEqual([w.text for w in res.words], ["version", "haut", "de", "gamme."])
        self.assertAlmostEqual(res.words[1].start, 0.5)
        self.assertAlmostEqual(res.words[-1].end, 1.2)
        for a, b in zip(res.words, res.words[1:]):
            self.assertLessEqual(a.end, b.start + 1e-9)  # ordre temporel intact
        self.assertEqual(res.items[-1].original, "haute garde.")

    def test_span_keeps_trailing_punctuation(self):
        words = [W("version", 0.0, 0.5), W("Haute", 0.5, 0.3), W("Garde.", 0.8, 0.4)]
        raw = '{"corrections": [{"de": 1, "a": 2, "texte": "haut de gamme"}]}'
        with mock.patch.object(correct_mod, "_call_openrouter", return_value=raw):
            res = correct(words, self.g, "frandroid", apply_llm=False)
        self.assertEqual(res.suggestions[0].suggested, "haut de gamme.")

    def test_span_digit_guard_and_overlap(self):
        words = [W("plus", 0.0, 0.2), W("de", 0.2, 0.2), W("demi", 0.4, 0.2),
                 W("chevaux.", 0.6, 0.3)]
        raw = ('{"corrections": [{"de": 2, "a": 3, "texte": "1000 chevaux."},'
               ' {"de": 0, "a": 1, "texte": "plus des"},'
               ' {"de": 1, "a": 2, "texte": "x"}]}')
        with mock.patch.object(correct_mod, "_call_openrouter", return_value=raw):
            res = correct(words, self.g, "frandroid", apply_llm=False)
        # chiffre ajouté → rejeté ; la plage qui chevauche la 1re gardée → ignorée
        self.assertEqual([(s.index, s.end_index, s.suggested) for s in res.suggestions],
                         [(0, 1, "plus des")])
        self.assertTrue(res.warnings)

    def test_segment_endpoint_applies_accepted_spans(self):
        from fastapi.testclient import TestClient
        from coquille.app import app
        words = [{"text": t, "start": i * 0.3, "duration": 0.3}
                 for i, t in enumerate("on a un tailleur très pur.".split())]
        r = TestClient(app).post("/segment", json={
            "media": "frandroid", "words": words,
            "spans": [{"de": 2, "a": 3, "texte": "un intérieur"}]})
        self.assertEqual(r.status_code, 200)
        self.assertIn("intérieur", r.json()["srt"])
        self.assertNotIn("tailleur", r.json()["srt"])

    def test_suggest_mode_respects_digit_guard(self):
        words = [W("S26", 0.0, 0.3)]
        raw = '{"words": [{"id": 0, "text": "S25"}]}'
        with mock.patch.object(correct_mod, "_call_openrouter", return_value=raw):
            res = correct(words, self.g, "frandroid", apply_llm=False)
        self.assertEqual(res.suggestions, [])   # chiffre protégé : pas même suggéré
        self.assertTrue(res.warnings)

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
    def test_media_presets_present(self):
        self.assertEqual(set(list_presets()), {"frandroid", "numerama", "clubic", "survoltes"})

    def test_preset_fields(self):
        p = load_preset("numerama")
        self.assertEqual(p.track_style, "Coquille — Numerama")
        self.assertGreater(p.segmentation.max_chars_per_line, 0)


class TestLlmKey(unittest.TestCase):
    """Clé OpenRouter saisie depuis le panneau : .env réécrit, jamais renvoyée."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        from coquille import llm_key
        self.llm_key = llm_key
        self.dir = tempfile.TemporaryDirectory()
        self.env = Path(self.dir.name) / ".env"
        self.patches = [
            mock.patch.object(config, "ENV_PATH", self.env),
            mock.patch.object(config, "OPENROUTER_API_KEY", ""),
            mock.patch.dict(os.environ, {}, clear=False),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.dir.cleanup()

    def test_write_keeps_other_lines(self):
        self.env.write_text("# commentaire\nOPENROUTER_API_KEY=ancienne\nCOQUILLE_ENGINE=auto\n",
                            encoding="utf-8")
        self.llm_key.write_env_value("OPENROUTER_API_KEY", "nouvelle")
        self.assertEqual(self.env.read_text(encoding="utf-8"),
                         "# commentaire\nOPENROUTER_API_KEY=nouvelle\nCOQUILLE_ENGINE=auto\n")

    def test_write_appends_when_missing(self):
        self.env.write_text("COQUILLE_ENGINE=auto", encoding="utf-8")
        self.llm_key.write_env_value("OPENROUTER_API_KEY", "k")
        self.assertEqual(self.env.read_text(encoding="utf-8"),
                         "COQUILLE_ENGINE=auto\nOPENROUTER_API_KEY=k\n")

    def test_clean_and_mask(self):
        k = "sk-or-v1-" + "a" * 60 + "b1c2"
        self.assertEqual(self.llm_key.clean(f'  OPENROUTER_API_KEY="{k}" '), k)
        self.assertEqual(self.llm_key.clean(f"Bearer {k}"), k)
        self.assertEqual(self.llm_key.mask(k), "sk-or-v1-…b1c2")
        self.assertFalse(self.llm_key.looks_valid("ma clé"))

    def _client(self):
        from fastapi.testclient import TestClient
        from coquille.app import app
        return TestClient(app)

    def test_endpoint_saves_and_never_returns_key(self):
        k = "sk-or-v1-" + "f" * 64
        ok = {"state": "ok", "detail": "clé valide", "label": "Vic", "limit_remaining": None}
        with mock.patch.object(self.llm_key, "verify", return_value=ok):
            r = self._client().post("/llm/key", json={"key": k}, headers={"Origin": "null"})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn(k, r.text)
        self.assertEqual(r.json()["key_hint"], "sk-or-v1-…ffff")
        self.assertEqual(config.OPENROUTER_API_KEY, k)  # effet immédiat
        self.assertIn(f"OPENROUTER_API_KEY={k}", self.env.read_text(encoding="utf-8"))
        st = self._client().get("/llm").json()
        self.assertTrue(st["configured"])
        self.assertNotIn(k, str(st))

    def test_endpoint_rejects_invalid_key_without_saving(self):
        bad = {"state": "invalide", "detail": "clé refusée par OpenRouter"}
        with mock.patch.object(self.llm_key, "verify", return_value=bad):
            r = self._client().post("/llm/key", json={"key": "sk-or-v1-" + "0" * 64})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(self.env.exists())
        self.assertEqual(config.OPENROUTER_API_KEY, "")

    def test_web_page_cannot_set_key(self):
        r = self._client().post("/llm/key", json={"key": "sk-or-v1-" + "0" * 64},
                                headers={"Origin": "https://exemple.com"})
        self.assertEqual(r.status_code, 403)

    def test_setup_cli_writes_key_from_env(self):
        k = "sk-or-v1-" + "c" * 64
        with mock.patch.dict(os.environ, {"COQUILLE_SETUP_KEY": f" {k} "}):
            self.assertEqual(self.llm_key.main(), 0)
        self.assertIn(f"OPENROUTER_API_KEY={k}\n", self.env.read_text(encoding="utf-8"))

    def test_delete_key(self):
        self.env.write_text("OPENROUTER_API_KEY=abc\n", encoding="utf-8")
        config.OPENROUTER_API_KEY = "abc"
        r = self._client().delete("/llm/key")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["configured"])
        self.assertEqual(self.env.read_text(encoding="utf-8"), "OPENROUTER_API_KEY=\n")


class TestZoneEtProgression(unittest.TestCase):
    """Transcription limitée à une zone + suivi d'avancement (/progress)."""

    def test_zone_times_stay_absolute(self):
        import numpy as np
        from coquille import transcribe as tr
        seen = {}

        def fake_fw(audio, model, lang, prompt, progress):
            seen["len"] = len(audio)
            progress("transcription", 1.0)
            return [Word(text="bonjour", start=0.5, duration=0.4)]

        calls = []
        with mock.patch("faster_whisper.audio.decode_audio", return_value=np.zeros(16000 * 30, dtype="float32")), \
             mock.patch.object(tr, "_faster_whisper_transcribe", side_effect=fake_fw):
            words = tr.transcribe_audio("x.wav", engine="small", refine_timing=False,
                                        start=12.0, end=20.0, progress=lambda p, f: calls.append(p))
        self.assertEqual(seen["len"], 16000 * 8)          # seulement la zone
        self.assertAlmostEqual(words[0].start, 12.5)      # temps du fichier entier
        self.assertEqual(calls, ["transcription"])

    def test_zone_outside_file_is_empty(self):
        import numpy as np
        from coquille import transcribe as tr
        with mock.patch("faster_whisper.audio.decode_audio", return_value=np.zeros(16000 * 5, dtype="float32")):
            self.assertEqual(tr.transcribe_audio("x.wav", engine="small", refine_timing=False,
                                                 start=10.0, end=20.0), [])

    def test_progress_endpoint(self):
        from fastapi.testclient import TestClient
        from coquille import app as app_mod
        report = app_mod._tracker("job1")
        report("transcription", 0.5)
        r = TestClient(app_mod.app).get("/progress/job1").json()
        self.assertEqual((r["phase"], r["fraction"]), ("transcription", 0.5))
        self.assertIsNotNone(r["remaining"])
        app_mod._done("job1")
        self.assertEqual(TestClient(app_mod.app).get("/progress/job1").json()["phase"], "inconnu")


if __name__ == "__main__":
    unittest.main()
