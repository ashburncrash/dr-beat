"""Offline tests for the LLM picker (fake HTTP; no network, no real keys).

Song titles below are real songs used as identifiers only; all "lyrics" are synthetic placeholders.
"""
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from songmatch.core import SongMatcher
from songmatch.llm import LLMClient, LLMError, SongPicker, clean_candidates, parse_json
from songmatch.lyrics import LyricsAnalyzer

NO_ENV = Path("/nonexistent/.env")
FAKE_LYRICS = {  # synthetic text, not real lyrics
    "Unfaithful": "i cheated and the guilt is killing me, i feel terrible about it, i am so sorry",
    "Before He Cheats": "you cheated on me, i am angry and furious",
    "Someone Like You": "i miss you, still love you, heartbroken and alone",
}


def lrclib(url):
    q = parse_qs(urlparse(url).query)
    name = q.get("track_name", [""])[0]
    if name in FAKE_LYRICS and "/get" in url:
        return 200, {}, json.dumps({"plainLyrics": FAKE_LYRICS[name], "instrumental": False}).encode()
    if "/search" in url:
        return 200, {}, b"[]"
    return 404, {}, b"{}"


def anthropic_reply(payload):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return 200, {}, json.dumps({"content": [{"type": "text", "text": text}]}).encode()


class FakeHTTP:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, method, url, headers, data):
        self.calls.append((method, url, headers, data))
        for key, resp in self.routes:
            if key in url:
                return resp(url) if callable(resp) else resp
        return 404, {}, b"{}"


def answer(cands, themes=("cheating",), safety=False, perspective="hurter"):
    return {"analysis": {"summary": "x", "perspective": perspective, "themes": list(themes),
                         "valence": 0.2, "energy": 0.4, "safety_concern": safety},
            "candidates": cands}


class PickerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.matcher = SongMatcher()

    def make(self, llm_resp, key="sk-test-SECRET"):
        llm = LLMClient("anthropic", "test-model", api_key=key, env_path=NO_ENV, sleep=lambda s: None,
                        http=FakeHTTP([("anthropic.com", llm_resp)]))
        lyr = LyricsAnalyzer(http=FakeHTTP([("lrclib.net", lrclib)]), cache_path=Path(tempfile.mkdtemp()) / "l.json")
        return SongPicker(self.matcher, llm=llm, lyrics=lyr)

    def test_returns_exactly_one_song(self):
        p = self.make(anthropic_reply(answer([{"title": "Unfaithful", "artist": "Rihanna", "confidence": 0.9, "why": "guilt"}])))
        out = p.pick("I cheated on my girlfriend and the guilt is eating me alive")
        self.assertIn("song", out)
        self.assertNotIn("results", out)
        self.assertEqual(out["song"]["title"], "Unfaithful")
        self.assertEqual(out["song"]["source"], "llm")

    def test_perspective_llm_beats_keyword_pick(self):
        # keyword matcher alone would lean to revenge songs; the LLM reads that the user is the cheater
        p = self.make(anthropic_reply(answer([
            {"title": "Unfaithful", "artist": "Rihanna", "confidence": 0.92, "why": "sung by the one who cheated"},
            {"title": "Before He Cheats", "artist": "Carrie Underwood", "confidence": 0.3, "why": "wrong side"}])))
        self.assertEqual(p.pick("I cheated and I feel so guilty")["song"]["title"], "Unfaithful")

    def test_hallucinated_song_dropped(self):
        p = self.make(anthropic_reply(answer([
            {"title": "Totally Made Up Ballad", "artist": "Rihanna", "confidence": 0.99, "why": "?"},
            {"title": "Unfaithful", "artist": "Rihanna", "confidence": 0.8, "why": "guilt"}])))
        out = p.pick("I cheated and I feel guilty", debug=True)
        self.assertEqual(out["song"]["title"], "Unfaithful")
        fake = [c for c in out["candidates"] if c["title"] == "Totally Made Up Ballad"][0]
        self.assertFalse(fake["verified"])

    def test_nothing_verified_falls_back(self):
        p = self.make(anthropic_reply(answer([{"title": "Not A Song", "artist": "Nobody", "confidence": 0.95}])))
        out = p.pick("he cheated on me")
        self.assertEqual(out["song"]["source"], "catalogue")
        self.assertIn("verified", out["note"])

    def test_low_confidence_falls_back(self):
        p = self.make(anthropic_reply(answer([{"title": "Unfaithful", "artist": "Rihanna", "confidence": 0.1}])))
        self.assertEqual(p.pick("he cheated on me")["song"]["source"], "catalogue")

    def test_fenced_json_and_percent_confidence(self):
        text = "Sure!\n```json\n" + json.dumps(answer([{"title": "Unfaithful", "artist": "Rihanna", "confidence": 88}])) + "\n```"
        out = self.make(anthropic_reply(text)).pick("I cheated and feel guilty")
        self.assertEqual(out["song"]["llm_confidence"], 0.88)

    def test_llm_error_falls_back_without_leaking_key(self):
        p = self.make((401, {}, b'{"error":{"type":"authentication_error","message":"invalid x-api-key"}}'))
        out = p.pick("he cheated on me")
        self.assertEqual(out["song"]["source"], "catalogue")
        self.assertNotIn("SECRET", json.dumps(out))

    def test_safety_note_survives_llm_failure(self):
        p = self.make((500, {}, b"{}"))
        self.assertIsNotNone(p.pick("he hits me and threatens me")["safety_note"])

    def test_safety_flag_from_llm(self):
        p = self.make(anthropic_reply(answer([{"title": "Unfaithful", "artist": "Rihanna", "confidence": 0.9}], safety=True)))
        self.assertIsNotNone(p.pick("things at home are scary lately")["safety_note"])

    def test_llm_called_once_per_input(self):
        p = self.make(anthropic_reply(answer([{"title": "Unfaithful", "artist": "Rihanna", "confidence": 0.9}])))
        p.pick("I cheated")
        p.pick("I  CHEATED ")
        self.assertEqual(len(p.llm.http.calls), 1)

    def test_no_llm_uses_catalogue(self):
        out = SongPicker(self.matcher).pick("we just got engaged!!")
        self.assertEqual(out["song"]["source"], "catalogue")
        self.assertIn("not configured", out["note"])


class ClientTests(unittest.TestCase):
    def test_config_errors(self):
        import os
        saved = {k: os.environ.pop(k) for k in ("LLM_PROVIDER", "LLM_MODEL", "ANTHROPIC_API_KEY") if k in os.environ}
        try:
            self.assertEqual(LLMClient.from_env(env_path=NO_ENV)[0], None)
            with self.assertRaises(LLMError):
                LLMClient("anthropic", "m", env_path=NO_ENV)  # no key
            with self.assertRaises(LLMError):
                LLMClient("bogus", "m", env_path=NO_ENV)
            LLMClient("ollama", "m", env_path=NO_ENV)  # local: no key needed
        finally:
            os.environ.update(saved)

    def test_openai_retries_without_temperature(self):
        seq = iter([(400, {}, b'{"error":{"message":"Unsupported value: temperature"}}'),
                    (200, {}, json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode())])
        http = FakeHTTP([("openai.com", lambda u: next(seq))])
        c = LLMClient("openai", "m", api_key="k", env_path=NO_ENV, http=http)
        self.assertEqual(c.complete("s", "u"), "{}")
        self.assertNotIn(b"temperature", http.calls[1][3])

    def test_parse_and_clean(self):
        self.assertEqual(parse_json('blah {"a": 1} blah'), {"a": 1})
        with self.assertRaises(LLMError):
            parse_json("no json here")
        c = clean_candidates([{"title": "A", "artist": "B", "confidence": "x"}, {"title": "A", "artist": "B feat. C"},
                              {"title": "", "artist": "B"}, "junk"])
        self.assertEqual(len(c), 1)
        self.assertEqual(c[0]["confidence"], 0.0)


class FakeRun:
    def __init__(self, stdout="", returncode=0, stderr="", exc=None):
        self.stdout, self.returncode, self.stderr, self.exc, self.calls = stdout, returncode, stderr, exc, []

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        if self.exc:
            raise self.exc
        import types
        return types.SimpleNamespace(stdout=self.stdout, returncode=self.returncode, stderr=self.stderr)


class ClaudeCliTests(unittest.TestCase):
    def cli(self, run):
        return LLMClient("claude-cli", None, env_path=NO_ENV, run=run, cli_path="/bin/claude")

    def test_success_and_no_api_key_leak(self):
        import os
        run = FakeRun(json.dumps({"type": "result", "is_error": False, "result": '{"ok": 1}'}))
        os.environ["ANTHROPIC_API_KEY"] = "sk-should-not-pass"
        try:
            c = self.cli(run)
            self.assertEqual(c.model, "sonnet")
            self.assertEqual(c.complete("sys", "my private story"), '{"ok": 1}')
        finally:
            os.environ.pop("ANTHROPIC_API_KEY")
        cmd, kw = run.calls[0]
        self.assertNotIn("ANTHROPIC_API_KEY", kw["env"])          # bills the subscription, not an API key
        self.assertNotIn("my private story", " ".join(cmd))        # text goes via stdin, not argv
        self.assertEqual(kw["input"], "my private story")
        self.assertIn("--tools", cmd)

    def test_not_logged_in(self):
        run = FakeRun(json.dumps({"is_error": True, "result": "Invalid API key · Please run /login"}), returncode=1)
        with self.assertRaisesRegex(LLMError, "not logged in"):
            self.cli(run).complete("s", "u")

    def test_timeout_and_missing_binary(self):
        import subprocess
        with self.assertRaisesRegex(LLMError, "timed out"):
            self.cli(FakeRun(exc=subprocess.TimeoutExpired("claude", 1))).complete("s", "u")
        with self.assertRaisesRegex(LLMError, "could not start"):
            self.cli(FakeRun(exc=FileNotFoundError())).complete("s", "u")

    def test_picker_end_to_end_via_cli(self):
        ans = json.dumps(answer([{"title": "Unfaithful", "artist": "Rihanna", "confidence": 0.9, "why": "guilt"}]))
        llm = self.cli(FakeRun(json.dumps({"is_error": False, "result": "```json\n" + ans + "\n```"})))
        lyr = LyricsAnalyzer(http=FakeHTTP([("lrclib.net", lrclib)]), cache_path=Path(tempfile.mkdtemp()) / "l.json")
        out = SongPicker(SongMatcher(), llm=llm, lyrics=lyr).pick("I cheated and feel guilty")
        self.assertEqual((out["song"]["title"], out["song"]["source"]), ("Unfaithful", "llm"))
        self.assertIn("subscription", out["song"]["model"])


if __name__ == "__main__":
    unittest.main()
