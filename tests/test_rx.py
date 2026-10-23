"""Prescription (diagnosis/dosage/cocktail) + free-provider presets. Offline, fake HTTP only."""
import json
import os
import re
import tempfile
import unittest
from pathlib import Path

from songmatch.cocktails import Bar, prescribe
from songmatch.core import SongMatcher
from songmatch.llm import LLMClient, LLMError, SongPicker
from songmatch.lyrics import LyricsAnalyzer
from tests.test_llm import NO_ENV, FakeHTTP, anthropic_reply, answer, lrclib

GLASSES = {"coupe", "rocks", "highball", "flute", "mug", "wine"}


def picker(reply, **kw):
    llm = LLMClient("anthropic", "m", api_key="k", env_path=NO_ENV, sleep=lambda s: None,
                    http=FakeHTTP([("anthropic.com", reply)]))
    lyr = LyricsAnalyzer(http=FakeHTTP([("lrclib.net", lrclib)]), cache_path=Path(tempfile.mkdtemp()) / "l.json")
    return SongPicker(SongMatcher(), llm=llm, lyrics=lyr, **kw)


def with_rx(rx, **kw):
    a = answer([{"title": "Unfaithful", "artist": "Rihanna", "confidence": 0.9, "why": "guilt"}], **kw)
    a["rx"] = rx
    return anthropic_reply(a)


class BarTests(unittest.TestCase):
    def test_data_is_well_formed(self):
        bar = Bar()
        self.assertGreaterEqual(len(bar.drinks), 20)
        self.assertEqual(len(bar.names()), len({n.lower() for n in bar.names()}))
        for d in bar.drinks:
            self.assertIn(d["glass"], GLASSES, d["name"])
            self.assertRegex(d["color"], r"^#[0-9a-f]{6}$")
            self.assertTrue(d["ingredients"] and d["mocktail"]["ingredients"], d["name"])
            self.assertNotIn("abuse", d["themes"])

    def test_local_match_and_mood(self):
        bar = Bar()
        d = bar.best({"themes": {"long_distance": 1.0}, "mood": "wistful / bittersweet"})
        self.assertIn("long_distance", d["themes"])
        self.assertTrue(bar.best({"themes": {}, "mood": "euphoric / upbeat"}))


class PrescriptionTests(unittest.TestCase):
    def test_llm_cocktail_used(self):
        out = picker(with_rx({"diagnosis": "Guilty conscience.", "dosage": "Twice daily.",
                              "cocktail": {"name": "sidecar", "why": "a way to say sorry"}})).pick("I cheated and feel guilty")
        rx = out["rx"]
        self.assertEqual((rx["drink"]["name"], rx["drink"]["source"]), ("Sidecar", "llm"))
        self.assertEqual(rx["diagnosis"], "Guilty conscience.")
        self.assertTrue(rx["drink"]["mocktail"])  # 0% option always offered

    def test_unknown_cocktail_falls_back_to_bar(self):
        out = picker(with_rx({"cocktail": {"name": "Made Up Sunrise 3000"}})).pick("I cheated and feel guilty")
        self.assertIn(out["rx"]["drink"]["name"], Bar().names())
        self.assertEqual(out["rx"]["drink"]["source"], "bar")
        self.assertTrue(out["rx"]["diagnosis"] and out["rx"]["dosage"])

    def test_catalogue_mode_still_prescribes(self):
        out = SongPicker(SongMatcher()).pick("we just got engaged!!")
        self.assertEqual(out["song"]["source"], "catalogue")
        self.assertIn(out["rx"]["drink"]["name"], Bar().names())

    def test_no_alcohol_serves_mocktail(self):
        out = SongPicker(SongMatcher()).pick("we just got engaged!!", no_alcohol=True)
        self.assertTrue(out["rx"]["drink"]["alcohol_free"])
        self.assertIsNone(out["rx"]["drink"]["mocktail"])

    def test_safety_case_is_alcohol_free_and_not_jokey(self):
        out = picker(with_rx({"diagnosis": "lol", "cocktail": {"name": "Negroni"}}, safety=True)).pick("he threatens me")
        rx = out["rx"]
        self.assertTrue(rx["serious"] and rx["drink"]["alcohol_free"])
        self.assertIsNone(rx["diagnosis"])
        self.assertIsNotNone(out["safety_note"])
        # keyword-only path too
        self.assertTrue(SongPicker(SongMatcher()).pick("he hits me and threatens me")["rx"]["drink"]["alcohol_free"])

    def test_junk_rx_ignored(self):
        rx = prescribe(Bar(), {"themes": {"cheating": 1.0}, "mood": "angry / defiant"}, rx="not a dict")
        self.assertIn(rx["drink"]["name"], Bar().names())


class FreeProviderTests(unittest.TestCase):
    def setUp(self):
        self.saved = {k: os.environ.pop(k) for k in ("LLM_MODEL", "LLM_BASE_URL") if k in os.environ}

    def tearDown(self):
        os.environ.update(self.saved)

    def ok(self, text="{}"):
        return 200, {}, json.dumps({"choices": [{"message": {"content": text}}]}).encode()

    def test_presets(self):
        for prov, host, model in [("groq", "api.groq.com", "openai/gpt-oss-120b"),
                                  ("gemini", "generativelanguage.googleapis.com", "gemini-3.5-flash")]:
            http = FakeHTTP([(host, self.ok())])
            c = LLMClient(prov, None, api_key="k", env_path=NO_ENV, http=http)
            self.assertEqual(c.model, model)
            self.assertEqual(c.complete("s", "u"), "{}")
            self.assertEqual(http.calls[0][2]["Authorization"], "Bearer k")

    def test_missing_key_named(self):
        saved = os.environ.pop("GROQ_API_KEY", None)
        try:
            with self.assertRaisesRegex(LLMError, "GROQ_API_KEY"):
                LLMClient("groq", None, env_path=NO_ENV)
        finally:
            if saved:
                os.environ["GROQ_API_KEY"] = saved

    def test_openrouter_needs_model(self):
        with self.assertRaisesRegex(LLMError, "LLM_MODEL"):
            LLMClient("openrouter", None, api_key="k", env_path=NO_ENV)

    def test_retry_without_json_mode(self):
        seq = iter([(400, {}, b'{"error":{"message":"response_format json_object is not supported"}}'), self.ok()])
        http = FakeHTTP([("groq.com", lambda u: next(seq))])
        LLMClient("groq", None, api_key="k", env_path=NO_ENV, http=http).complete("s", "u")
        self.assertNotIn(b"response_format", http.calls[1][3])

    def test_rate_limit_message(self):
        http = FakeHTTP([("groq.com", (429, {}, b"{}"))])
        c = LLMClient("groq", None, api_key="k", env_path=NO_ENV, http=http, sleep=lambda s: None)
        with self.assertRaisesRegex(LLMError, "rate limit"):
            c.complete("s", "u")


if __name__ == "__main__":
    unittest.main()
