"""Offline tests for the Spotify + lyrics integration (fake HTTP; no network, no real credentials)."""
import json
import tempfile
import unittest
from pathlib import Path

from songmatch.discover import discover
from songmatch.lyrics import LyricsAnalyzer
from songmatch.spotify import SpotifyClient, SpotifyError, pick_best, primary_artist

NO_ENV = Path("/nonexistent/.env")


def track(tid, name, artist, album="Album", year="2011"):
    return {"id": tid, "name": name, "artists": [{"name": artist}], "duration_ms": 200000, "explicit": False,
            "album": {"name": album, "release_date": year + "-01-01", "images": [{"url": "https://img/" + tid}]},
            "external_urls": {"spotify": "https://open.spotify.com/track/" + tid}}


def search_body(*tracks):
    return json.dumps({"tracks": {"items": list(tracks)}}).encode()


class FakeHTTP:
    """Routes requests by URL substring; records calls."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, method, url, headers, data):
        self.calls.append((method, url, headers))
        for key, resp in self.routes:
            if key in url:
                r = resp(url) if callable(resp) else resp
                return r if len(r) == 3 else (r[0], {}, r[1])
        return 404, {}, b"{}"

    def count(self, key):
        return sum(1 for c in self.calls if key in c[1])


TOKEN = ("accounts.spotify.com", (200, b'{"access_token":"tok","expires_in":3600}'))


def client(routes, **kw):
    tmp = tempfile.mkdtemp()
    return SpotifyClient("id", "secret", http=FakeHTTP([TOKEN] + routes), env_path=NO_ENV,
                         cache_path=Path(tmp) / "c.json", sleep=lambda s: None, **kw)


class SpotifyClientTests(unittest.TestCase):
    def test_token_cached_across_calls(self):
        c = client([("/search", (200, search_body()))])
        c.search_tracks("a")
        c.search_tracks("b")
        self.assertEqual(c.http.count("accounts.spotify.com"), 1)

    def test_refresh_on_401(self):
        seq = iter([(401, b"{}"), (200, search_body(track("x", "X", "Y")))])
        c = client([("/search", lambda u: next(seq))])
        self.assertEqual(c.search_tracks("q")[0]["id"], "x")
        self.assertEqual(c.http.count("accounts.spotify.com"), 2)

    def test_retry_after_429(self):
        slept = []
        seq = iter([(429, {"Retry-After": "2"}, b"{}"), (200, search_body())])
        c = client([("/search", lambda u: next(seq))])
        c.sleep = slept.append
        c.search_tracks("q")
        self.assertEqual(slept, [2])

    def test_403_mentions_premium(self):
        c = client([("/search", (403, b'{"error":{"status":403,"message":"Forbidden"}}'))])
        with self.assertRaisesRegex(SpotifyError, "Premium"):
            c.search_tracks("q")

    def test_bad_credentials_message_has_no_secret(self):
        c = SpotifyClient("id", "supersecret", env_path=NO_ENV, cache_path=None,
                          http=FakeHTTP([("accounts", (400, b'{"error":"invalid_client"}'))]))
        with self.assertRaises(SpotifyError) as cm:
            c.search_tracks("q")
        self.assertNotIn("supersecret", str(cm.exception))

    def test_search_limit_capped_at_10(self):
        c = client([("/search", (200, search_body()))])
        c.search_tracks("q", limit=50)
        self.assertIn("limit=10", [u for _, u, _ in c.http.calls if "/search" in u][0])

    def test_missing_credentials(self):
        import os
        saved = {k: os.environ.pop(k) for k in ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET") if k in os.environ}
        try:
            self.assertIsNone(SpotifyClient.from_env(env_path=NO_ENV))
        finally:
            os.environ.update(saved)

    def test_resolve_prefers_original_and_caches(self):
        body = search_body(track("k", "Test Song - Karaoke Version", "Sing Along Band"),
                           track("c", "Test Song", "Some Cover Act"),
                           track("o", "Test Song", "Real Artist", album="Real Album"))
        c = client([("/search", (200, body))])
        r = c.resolve("Test Song", "Real Artist feat. Guest")
        self.assertEqual(r["id"], "o")
        self.assertEqual(r["embed"], "https://open.spotify.com/embed/track/o")
        n = c.http.count("/search")
        c.resolve("Test Song", "Real Artist feat. Guest")
        self.assertEqual(c.http.count("/search"), n)  # served from cache
        self.assertTrue(c.cache_path.exists())

    def test_resolve_none_when_no_title_match(self):
        c = client([("/search", (200, search_body(track("z", "Unrelated", "Real Artist"))))])
        self.assertIsNone(c.resolve("Test Song", "Real Artist"))

    def test_enrich_sets_real_link(self):
        c = client([("/search", (200, search_body(track("o", "Test Song", "Real Artist"))))])
        res = [{"title": "Test Song", "artist": "Real Artist", "links": {"spotify": "search-url"}}]
        self.assertEqual(c.enrich(res), 1)
        self.assertEqual(res[0]["links"]["spotify"], "https://open.spotify.com/track/o")

    def test_primary_artist(self):
        self.assertEqual(primary_artist("Timbaland feat. OneRepublic"), "Timbaland")
        self.assertEqual(primary_artist("A Great Big World & Christina Aguilera"), "A Great Big World")
        self.assertEqual(primary_artist("Peter, Paul and Mary"), "Peter, Paul and Mary")

    def test_pick_best_rejects_cover(self):
        items = [{"title": "Test Song", "artist": "Cover Band"}]
        self.assertIsNone(pick_best(items, "Test Song", "Real Artist"))


# Synthetic placeholder "lyrics" (not real song text) for offline tests.
FAKE_LYRICS = {
    "Liar Song": "you cheated on me, you lied to me, i am so angry and furious, you cheated again",
    "Happy Song": "sunshine and dancing, so happy, everything is wonderful and great",
}


def lrclib(url):
    from urllib.parse import parse_qs, urlparse
    q = parse_qs(urlparse(url).query)
    name = q.get("track_name", [""])[0]
    if name in FAKE_LYRICS:
        return 200, json.dumps({"plainLyrics": FAKE_LYRICS[name], "instrumental": False}).encode()
    return 404, b"{}"


class DiscoverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.lyr = LyricsAnalyzer(http=FakeHTTP([("lrclib.net", lrclib)]), cache_path=self.tmp / "l.json")

    def test_lyrics_verified_and_no_raw_text_cached(self):
        a = self.lyr.analyze("Liar Song", "Artist A")
        self.assertIn("cheating", a["themes"])
        raw = (self.tmp / "l.json").read_text()
        self.assertNotIn("you lied to me", raw)
        self.assertIsNone(self.lyr.analyze("No Lyrics Song", "Artist B"))

    def test_discover_filters_by_lyrics_and_excludes_catalogue(self):
        body = search_body(track("1", "Liar Song", "Artist A"), track("2", "Happy Song", "Artist B"),
                           track("3", "No Lyrics Song", "Artist C"), track("4", "Known Song", "Known Artist"))
        sp = client([("/search", (200, body))])
        analysis = {"themes": {"cheating": 1.0, "anger": 1.0}, "valence": 0.3, "energy": 0.8, "mood": "angry / defiant"}
        out = discover(analysis, sp, self.lyr, exclude=[("Known Song", "Known Artist")])
        self.assertEqual([d["title"] for d in out], ["Liar Song"])
        self.assertIn("cheating", out[0]["reasons"][0])

    def test_discover_without_themes_returns_empty(self):
        sp = client([("/search", (200, search_body()))])
        self.assertEqual(discover({"themes": {}, "valence": .5, "energy": .5, "mood": "x"}, sp, self.lyr), [])


if __name__ == "__main__":
    unittest.main()
