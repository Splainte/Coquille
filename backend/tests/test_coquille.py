"""Tests du cœur déterministe (stdlib unittest, sans dépendance).

Lancer depuis backend/ :  python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
        self.assertTrue(first.rstrip().endswith("7?"), first)
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
