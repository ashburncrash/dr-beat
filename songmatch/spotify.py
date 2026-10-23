"""Spotify Web API client — Client Credentials flow, stdlib only.

Written against Spotify's Feb-2026 Development Mode rules (apply to all new apps):
  * the app OWNER needs Spotify Premium, or every call fails
  * GET /search: limit max 10 (paginate with offset)
  * no `popularity` field, no batch GET /tracks, no artist top-tracks / browse
  * playlist contents only for playlists you own -> no mining public playlists
  * audio-features / audio-analysis / recommendations are not available to new apps
So Spotify is used to RETRIEVE real tracks (ids, links, album art, embeds).
Deciding what a song is *about* is done separately from its lyrics (see lyrics.py).

CLI:
  python3 -m songmatch.spotify check   # verify credentials with one real call
  python3 -m songmatch.spotify sync    # resolve the whole curated catalogue
"""
import base64
import json
import os
import re
import sys
import threading
import time
import urllib.parse
from pathlib import Path

from . import _net

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
CACHE_PATH = ROOT / "data" / "spotify_cache.json"
_BAD_VERSION = re.compile(r"karaoke|instrumental|cover|tribute|8-bit|lullaby|made famous|originally performed|sped up|slowed", re.I)


class SpotifyError(RuntimeError):
    pass


def load_env(path=ENV_PATH):
    """Minimal .env reader (KEY=VALUE). Does not override real env vars."""
    path = Path(path)
    if not path.exists():
        return
    vals = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'")
        if v or k.strip() not in vals:  # last non-empty value wins, so duplicate lines behave as expected
            vals[k.strip()] = v
    for k, v in vals.items():
        os.environ.setdefault(k, v)


def norm(s):
    """Normalise a title/artist for comparison: drop (feat...)/[...]/' - Remastered', keep alnum."""
    s = (s or "").lower()
    s = re.sub(r"\(.*?\)|\[.*?\]", " ", s)
    s = re.split(r"\s+-\s+", s)[0]
    return re.sub(r"[^a-z0-9]", "", s)


def primary_artist(artist):
    """'Timbaland feat. OneRepublic' -> 'Timbaland'; 'Peter, Paul and Mary' stays whole."""
    return re.split(r"\s+(?:feat\.?|ft\.?|featuring|x)\s+|\s+&\s+", artist or "")[0].strip()


def _safe_err(body):
    try:
        d = json.loads(body)
        e = d.get("error")
        if isinstance(e, dict):
            return e.get("message") or str(e.get("status"))
        return d.get("error_description") or str(e)
    except Exception:
        return (body or b"")[:120].decode("utf-8", "replace")


def norm_track(t):
    album = t.get("album") or {}
    imgs = album.get("images") or []
    tid = t["id"]
    return {
        "id": tid,
        "title": t.get("name", ""),
        "artist": ", ".join(a.get("name", "") for a in t.get("artists") or []),
        "album": album.get("name"),
        "year": (album.get("release_date") or "")[:4] or None,
        "duration_s": round((t.get("duration_ms") or 0) / 1000),
        "explicit": bool(t.get("explicit")),
        "image": imgs[0]["url"] if imgs else None,
        "url": (t.get("external_urls") or {}).get("spotify") or "https://open.spotify.com/track/" + tid,
        "embed": "https://open.spotify.com/embed/track/" + tid,
    }


class SpotifyClient:
    API = "https://api.spotify.com/v1"
    TOKEN_URL = "https://accounts.spotify.com/api/token"

    def __init__(self, client_id=None, client_secret=None, market="US", http=None,
                 cache_path=CACHE_PATH, env_path=ENV_PATH, sleep=time.sleep):
        load_env(env_path)
        self.client_id = client_id or os.environ.get("SPOTIFY_CLIENT_ID")
        self._secret = client_secret or os.environ.get("SPOTIFY_CLIENT_SECRET")
        if not (self.client_id and self._secret):
            raise SpotifyError("Spotify credentials missing: set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET in .env")
        self.market = market or os.environ.get("SPOTIFY_MARKET") or "US"
        self.http = http or _net.request
        self.sleep = sleep
        self._token, self._exp = None, 0.0
        self._lock = threading.Lock()
        self.cache_path = Path(cache_path) if cache_path else None
        self.cache = self._load_cache()

    @classmethod
    def from_env(cls, **kw):
        """Return a client, or None if credentials aren't configured (offline mode)."""
        try:
            return cls(**kw)
        except SpotifyError:
            return None

    # ------------------------------------------------------------ plumbing
    def _load_cache(self):
        if self.cache_path and self.cache_path.exists():
            try:
                return json.loads(self.cache_path.read_text(encoding="utf-8"))
            except ValueError:
                pass
        return {}

    def _save_cache(self):
        if not self.cache_path:
            return
        with self._lock:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.cache, indent=1, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.cache_path)

    def _get_token(self, force=False):
        with self._lock:
            if self._token and not force and time.time() < self._exp - 60:
                return self._token
            auth = base64.b64encode(("%s:%s" % (self.client_id, self._secret)).encode()).decode()
            status, _, body = self.http(
                "POST", self.TOKEN_URL,
                {"Authorization": "Basic " + auth, "Content-Type": "application/x-www-form-urlencoded"},
                b"grant_type=client_credentials",
            )
            if status != 200:
                raise SpotifyError("Spotify token request failed (%s): %s — check the Client ID/secret in .env"
                                   % (status, _safe_err(body)))
            d = json.loads(body)
            self._token = d["access_token"]
            self._exp = time.time() + int(d.get("expires_in", 3600))
            return self._token

    def get(self, path, params=None):
        url = self.API + path + ("?" + urllib.parse.urlencode(params) if params else "")
        force = False
        refreshed = False
        for attempt in range(4):
            token = self._get_token(force=force)
            force = False
            status, headers, body = self.http("GET", url, {"Authorization": "Bearer " + token}, None)
            if status == 200:
                return json.loads(body)
            if status == 401 and not refreshed:
                refreshed = force = True
                continue
            if status == 429:
                wait = int(_net.header(headers, "Retry-After") or 1)
                if wait > 30:
                    raise SpotifyError("Rate-limited by Spotify for %ss; try again later." % wait)
                self.sleep(wait)
                continue
            if status == 403:
                raise SpotifyError("Spotify refused %s (403: %s). Development-mode apps need the app owner to have "
                                   "Spotify Premium, and some endpoints are restricted." % (path, _safe_err(body)))
            if status >= 500 and attempt < 3:
                self.sleep(1 + attempt)
                continue
            raise SpotifyError("Spotify %s on %s: %s" % (status, path, _safe_err(body)))
        raise SpotifyError("Spotify request kept failing: " + path)

    # ------------------------------------------------------------ features
    def search_tracks(self, q, limit=10, offset=0):
        d = self.get("/search", {"q": q, "type": "track", "limit": max(1, min(int(limit), 10)),
                                 "offset": offset, "market": self.market})
        return [norm_track(t) for t in ((d.get("tracks") or {}).get("items") or []) if t and t.get("id")]

    def resolve(self, title, artist):
        """Find the real Spotify track for a curated (title, artist). Cached; None if no confident match."""
        key = "%s|%s|%s" % (norm(title), norm(artist), self.market)
        if key in self.cache:
            return self.cache[key]
        prim = primary_artist(artist)
        items = self.search_tracks('track:"%s" artist:"%s"' % (title, prim), limit=10)
        best = pick_best(items, title, prim)
        if best is None:
            best = pick_best(self.search_tracks("%s %s" % (title, prim), limit=10), title, prim)
        self.cache[key] = best
        self._save_cache()
        return best

    def enrich(self, results):
        """Attach real Spotify track data to matcher results (in place). Returns count resolved."""
        n = 0
        for r in results:
            s = self.resolve(r["title"], r["artist"])
            r["spotify"] = s
            if s:
                r["links"]["spotify"] = s["url"]
                n += 1
        return n


def pick_best(items, title, artist):
    nt, na = norm(title), norm(artist)
    best, best_score = None, 0
    for it in items:
        ct = norm(it["title"])
        if not ct or not (ct == nt or ct.startswith(nt) or (len(ct) > 3 and nt.startswith(ct))):
            continue
        score = 2 if ct == nt else 1
        if na and na in norm(it["artist"].replace(",", "")):
            score += 3
        elif na:
            continue  # right title, wrong artist (cover) -> reject
        if _BAD_VERSION.search(it["title"]) or "live" in it["title"].lower():
            score -= 2
        if score > best_score:
            best, best_score = it, score
    return best


def _cli(argv=None):
    import argparse
    from .core import load_songs

    p = argparse.ArgumentParser(prog="songmatch.spotify")
    p.add_argument("cmd", choices=["check", "sync"])
    p.add_argument("--market", default=None)
    a = p.parse_args(argv)
    try:
        c = SpotifyClient(market=a.market)
    except SpotifyError as e:
        print("✗ " + str(e) + "\n  See README → 'Spotify setup'.")
        return 1
    try:
        if a.cmd == "check":
            hit = c.resolve("Someone Like You", "Adele")
            print("✓ Spotify OK (market %s). Test lookup: %s" % (c.market, hit["url"] if hit else "no match"))
            return 0
        songs = load_songs()
        missing = []
        for i, s in enumerate(songs, 1):
            r = c.resolve(s["title"], s["artist"])
            if not r:
                missing.append("%s - %s" % (s["title"], s["artist"]))
            print("\r  resolved %d/%d" % (i, len(songs)), end="", flush=True)
        print("\n✓ %d/%d songs linked to Spotify (cache: %s)" % (len(songs) - len(missing), len(songs), c.cache_path))
        for m in missing:
            print("  ✗ not found: " + m)
        return 0
    except SpotifyError as e:
        print("✗ " + str(e))
        return 1


if __name__ == "__main__":
    sys.exit(_cli())
