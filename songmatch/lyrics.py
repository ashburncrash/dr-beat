"""Lyrics-based analysis via LRCLIB (https://lrclib.net — free, no key).

Spotify has no lyrics endpoint and no audio features for new apps, so to judge what a
*discovered* song is about we fetch its lyrics, run the same theme/mood detectors used
on the user's text, and cache ONLY the derived signals. Raw lyrics are never stored,
printed, or sent to the browser.
"""
import json
import threading
import urllib.parse
from pathlib import Path

from . import _net
from .core import detect_themes, estimate_mood
from .spotify import norm

CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "lyrics_cache.json"
API = "https://lrclib.net/api"


def fetch_lyrics(title, artist, duration_s=None, http=None):
    """Return plain lyrics text, or None (not found / instrumental / network error)."""
    http = http or _net.request
    q = {"track_name": title, "artist_name": artist}
    if duration_s:
        q["duration"] = int(duration_s)
    try:
        status, _, body = http("GET", API + "/get?" + urllib.parse.urlencode(q), {}, None)
        if status == 200:
            d = json.loads(body)
            if d.get("plainLyrics") and not d.get("instrumental"):
                return d["plainLyrics"]
        status, _, body = http("GET", API + "/search?" + urllib.parse.urlencode(
            {"track_name": title, "artist_name": artist}), {}, None)
        if status == 200:
            a = artist.lower().split(",")[0].strip()
            nt = norm(title)
            for d in json.loads(body) or []:
                ct = norm(d.get("trackName"))
                # artist AND title must match, so a made-up title by a real artist can't "verify"
                if (d.get("plainLyrics") and not d.get("instrumental") and a in (d.get("artistName") or "").lower()
                        and ct and (ct == nt or ct.startswith(nt) or nt.startswith(ct))):
                    return d["plainLyrics"]
    except (OSError, ValueError):
        return None
    return None


class LyricsAnalyzer:
    def __init__(self, http=None, cache_path=CACHE_PATH):
        self.http = http
        self.cache_path = Path(cache_path) if cache_path else None
        self._lock = threading.Lock()
        self.cache = {}
        if self.cache_path and self.cache_path.exists():
            try:
                self.cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
            except ValueError:
                self.cache = {}

    def analyze(self, title, artist, duration_s=None):
        """Return {themes, valence, energy} derived from lyrics, or None if unavailable."""
        key = "%s|%s" % (title.lower(), artist.lower())
        if key in self.cache:
            return self.cache[key]
        text = fetch_lyrics(title, artist, duration_s, http=self.http)
        if not text:
            return None  # don't cache misses: network blips shouldn't stick
        themes = {t: round(w, 2) for t, w in detect_themes(text).items() if w >= 1.0}
        v, e = estimate_mood(text, themes)
        out = {"themes": themes, "valence": v, "energy": e}
        with self._lock:
            self.cache[key] = out
            if self.cache_path:
                self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.cache_path.with_suffix(".tmp")
                tmp.write_text(json.dumps(self.cache, indent=1, ensure_ascii=False), encoding="utf-8")
                tmp.replace(self.cache_path)
        return out
