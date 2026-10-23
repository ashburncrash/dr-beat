"""LLM-powered picker: the LLM proposes candidate songs, we verify them, and return ONE.

Pipeline
  1. local pass   : keyword themes/mood + abuse check (safety note never depends on the LLM)
  2. one LLM call : situation analysis + ~8 candidate songs with confidence and a reason
  3. verification : each candidate must exist (Spotify match and/or LRCLIB lyrics match);
                    hallucinated songs are dropped. Lyric-theme overlap and catalogue
                    agreement add a small amount of supporting evidence.
  4. pick         : final = 0.8 * LLM confidence + 0.2 * support. Highest verified one wins.
                    If nothing verifies, confidence is too low, or the LLM fails, we fall back
                    to the built-in catalogue matcher — the app never hard-fails.

Providers (stdlib HTTP only): anthropic, openai (or any OpenAI-compatible URL), ollama (local).
Configure in .env: LLM_PROVIDER, LLM_MODEL, and ANTHROPIC_API_KEY / OPENAI_API_KEY.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from . import _net
from .cocktails import Bar, prescribe
from .core import SAFETY_NOTE, THEME_PATTERNS, mood_label
from .spotify import ENV_PATH, SpotifyError, _safe_err, load_env, norm, primary_artist

TAGS = sorted(THEME_PATTERNS)
DEFAULT_URLS = {
    "anthropic": "https://api.anthropic.com/v1/messages",
    "openai": "https://api.openai.com/v1/chat/completions",
    "ollama": "http://localhost:11434/api/chat",
    "claude-cli": None,  # Claude Code headless mode, billed to your Claude Pro/Max subscription
    # Free-tier presets (all OpenAI-compatible)
    "groq": "https://api.groq.com/openai/v1/chat/completions",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
}
OPENAI_COMPAT = {"openai", "groq", "gemini", "openrouter"}
DEFAULT_MODELS = {"claude-cli": "sonnet", "groq": "openai/gpt-oss-120b", "gemini": "gemini-3.5-flash"}
# Env vars that would make Claude Code bill an API account instead of the subscription.
_CLI_STRIP = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")
_CLI_SLOTS = threading.BoundedSemaphore(2)  # cap concurrent CLI processes from the threaded web server
KEY_VARS = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "groq": "GROQ_API_KEY",
            "gemini": "GEMINI_API_KEY", "openrouter": "OPENROUTER_API_KEY"}
BAR = Bar()

SYSTEM = """You are a music curator. Someone describes their relationship situation; choose real songs whose lyrics or sound genuinely reflect it.

Rules:
- Only real, commercially released songs. Use the exact title and the ORIGINAL artist's name as listed on Spotify. If you are not sure a song exists, leave it out.
- Read the situation carefully: perspective (were they hurt, or did they hurt someone?), stage (new, ongoing, ending, aftermath) and emotional tone. A song sung from the wrong perspective is a poor fit.
- If they write in a language other than English, include strong songs in that language too.
- Never quote lyrics. Explain the fit in your own words, in one sentence.
- confidence (0-1) = how well the song fits THIS situation. Be calibrated: 0.9+ only for a near-perfect fit.

Return ONLY a JSON object, no prose:
{"analysis": {"summary": "<one sentence, no names>", "perspective": "hurt|hurter|mutual|happy|unsure",
              "themes": [<tags from the list below>], "valence": <0-1>, "energy": <0-1>, "safety_concern": <true|false>},
 "candidates": [{"title": "...", "artist": "...", "year": <int or null>, "confidence": <0-1>, "why": "..."}],
 "rx": {"diagnosis": "<playful, kind one-line 'diagnosis' of their situation, max 15 words, no names>",
        "dosage": "<playful listening instructions, max 15 words>",
        "cocktail": {"name": "<EXACTLY one name from the cocktail list below>", "why": "<one sentence>"}}}
Give 8 candidates, best first. safety_concern is true only if they describe abuse, threats, or being unsafe.
The rx is a light-hearted 'music doctor' prescription: warm, never mocking, never encourage drinking to cope.
Theme tags: """ + ", ".join(TAGS) + "\nCocktail list: " + ", ".join(BAR.names())

USER = "My situation:\n<<<\n%s\n>>>"


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, provider=None, model=None, api_key=None, base_url=None, http=None,
                 env_path=ENV_PATH, timeout=60, sleep=time.sleep, run=subprocess.run, cli_path=None):
        load_env(env_path)
        self.provider = (provider or os.environ.get("LLM_PROVIDER") or "").strip().lower()
        if not self.provider:
            # No provider named: use the first free/paid key that is present (gemini first).
            for prov in ("gemini", "groq", "openrouter", "anthropic", "openai"):
                if os.environ.get(KEY_VARS[prov]) and (prov != "openrouter" or os.environ.get("LLM_MODEL")):
                    self.provider = prov
                    break
        if not self.provider:
            raise LLMError("LLM not configured (set LLM_PROVIDER and LLM_MODEL in .env)")
        if self.provider not in DEFAULT_URLS:
            raise LLMError("LLM_PROVIDER must be one of: " + ", ".join(sorted(DEFAULT_URLS)))
        self.model = model or os.environ.get("LLM_MODEL") or DEFAULT_MODELS.get(self.provider)
        if not self.model:
            raise LLMError("LLM_MODEL not set in .env")
        self.run = run
        self.cli_path = cli_path or os.environ.get("CLAUDE_BIN")
        self.cli_timeout = max(timeout, 120)
        var = KEY_VARS.get(self.provider)
        self._key = api_key or (os.environ.get(var) if var else None)
        if var and not self._key:
            raise LLMError("%s not set in .env" % var)
        self.url = base_url or os.environ.get("LLM_BASE_URL") or DEFAULT_URLS[self.provider]
        self.http = http or (lambda m, u, h, d: _net.request(m, u, h, d, timeout=timeout))
        self.sleep = sleep

    @classmethod
    def from_env(cls, **kw):
        """Return (client, None) or (None, reason)."""
        try:
            return cls(**kw), None
        except LLMError as e:
            return None, str(e)

    def describe(self):
        suffix = " (subscription)" if self.provider == "claude-cli" else ""
        return "%s:%s%s" % (self.provider, self.model, suffix)

    def _complete_cli(self, system, user):
        """Claude Code headless: `claude -p`, text on stdin (not argv, so it never shows in `ps`)."""
        exe = self.cli_path or shutil.which("claude")
        if not exe:
            raise LLMError("claude CLI not found — install Claude Code, then run `claude` once to log in")
        cmd = [exe, "-p", "--output-format", "json", "--model", self.model, "--system-prompt", system,
               "--tools", "", "--strict-mcp-config", "--no-session-persistence", "--disable-slash-commands"]
        env = {k: v for k, v in os.environ.items() if k not in _CLI_STRIP}
        with _CLI_SLOTS, tempfile.TemporaryDirectory() as cwd:  # empty cwd: no project CLAUDE.md/hooks
            try:
                p = self.run(cmd, input=user, capture_output=True, text=True, timeout=self.cli_timeout,
                             env=env, cwd=cwd)
            except subprocess.TimeoutExpired:
                raise LLMError("claude CLI timed out after %ss" % self.cli_timeout)
            except OSError as e:
                raise LLMError("could not start claude CLI (%s)" % e.__class__.__name__)
        d = None
        try:
            d = json.loads(p.stdout or "")
        except ValueError:
            pass
        msg = (d or {}).get("result") if isinstance(d, dict) else None
        if p.returncode != 0 or not isinstance(d, dict) or d.get("is_error"):
            detail = str(msg or p.stderr or p.stdout or "no output").strip().splitlines()
            detail = detail[0][:200] if detail else "no output"
            if re.search(r"log ?in|logged in|authenticat|/login", detail, re.I):
                detail = "not logged in — run `claude` once and sign in with your Claude account"
            raise LLMError("claude CLI failed: " + detail)
        if not str(msg or "").strip():
            raise LLMError("claude CLI returned an empty answer")
        return str(msg)

    def _request(self, system, user, max_tokens):
        msgs = [{"role": "user", "content": user}]
        if self.provider == "anthropic":
            h = {"x-api-key": self._key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
            body = {"model": self.model, "max_tokens": max_tokens, "temperature": 0.3, "system": system, "messages": msgs}
        elif self.provider in OPENAI_COMPAT:
            h = {"Authorization": "Bearer " + self._key, "Content-Type": "application/json"}
            body = {"model": self.model, "temperature": 0.3, "response_format": {"type": "json_object"},
                    "messages": [{"role": "system", "content": system}] + msgs}
        else:
            h = {"Content-Type": "application/json"}
            body = {"model": self.model, "stream": False, "format": "json", "options": {"temperature": 0.3},
                    "messages": [{"role": "system", "content": system}] + msgs}
        return h, body

    def complete(self, system, user, max_tokens=1500):
        if self.provider == "claude-cli":
            return self._complete_cli(system, user)
        h, body = self._request(system, user, max_tokens)
        status, raw = 0, b""
        for attempt in range(4):
            try:
                status, _, raw = self.http("POST", self.url, h, json.dumps(body).encode())
            except OSError as e:
                raise LLMError("could not reach %s (%s)" % (self.provider, e.__class__.__name__))
            if status == 400 and "temperature" in body and b"temperature" in raw:
                body.pop("temperature")  # some models only allow the default temperature
                continue
            if status == 400 and "response_format" in body and re.search(rb"response_format|json_object|json mode", raw, re.I):
                body.pop("response_format")  # some free models don't support JSON mode; parse_json copes
                continue
            if (status == 429 or status >= 500) and attempt < 3:
                self.sleep(1.5 * (attempt + 1))
                continue
            break
        if status == 429:
            raise LLMError("%s rate limit reached (free tiers have per-minute/per-day caps)" % self.provider)
        if status != 200:
            raise LLMError("%s returned %s: %s" % (self.provider, status, _safe_err(raw)))
        try:
            d = json.loads(raw)
            if self.provider == "anthropic":
                text = "".join(b.get("text", "") for b in d.get("content") or [] if b.get("type") == "text")
            elif self.provider in OPENAI_COMPAT:
                text = d["choices"][0]["message"]["content"] or ""
            else:
                text = d["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise LLMError("unexpected response shape from %s" % self.provider)
        if not text.strip():
            raise LLMError("%s returned an empty answer" % self.provider)
        return text


def parse_json(text):
    """Tolerant JSON extraction: handles ```json fences and prose around the object."""
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    try:
        return json.loads(t)
    except ValueError:
        pass
    s, e = t.find("{"), t.rfind("}")
    if 0 <= s < e:
        try:
            return json.loads(t[s:e + 1])
        except ValueError:
            pass
    raise LLMError("LLM returned non-JSON output")


def _num(x, default):
    try:
        return round(max(0.0, min(1.0, float(x))), 3)
    except (TypeError, ValueError):
        return default


def clean_candidates(raw, limit=10):
    out, seen = [], set()
    for c in raw if isinstance(raw, list) else []:
        if not isinstance(c, dict):
            continue
        title, artist = str(c.get("title") or "").strip(), str(c.get("artist") or "").strip()
        if not title or not artist:
            continue
        k = (norm(title), norm(primary_artist(artist)))
        if not k[0] or k in seen:
            continue
        seen.add(k)
        try:
            conf = float(c.get("confidence", 0))
        except (TypeError, ValueError):
            conf = 0.0
        if conf > 1:  # model answered in percent
            conf /= 100.0
        out.append({"title": title, "artist": artist,
                    "year": c.get("year") if isinstance(c.get("year"), int) else None,
                    "confidence": max(0.0, min(1.0, conf)),
                    "why": str(c.get("why") or "").strip()[:300]})
    return out[:limit]


def links(title, artist, sp=None):
    q = title + " " + artist
    return {"youtube": "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(q),
            "spotify": sp["url"] if sp else "https://open.spotify.com/search/" + urllib.parse.quote(q)}


class SongPicker:
    """Returns exactly one song per input. Works with or without an LLM / Spotify."""
    W_LLM, W_SUPPORT = 0.8, 0.2

    def __init__(self, matcher, llm=None, spotify=None, lyrics=None, min_confidence=0.45, bar=None):
        self.matcher, self.llm, self.spotify, self.lyrics = matcher, llm, spotify, lyrics
        self.min_confidence = min_confidence
        self.bar = bar or BAR
        self._cache = {}  # in-memory only: sha256(input) -> parsed LLM answer; nothing written to disk

    def _ask(self, text):
        k = hashlib.sha256(" ".join(text.lower().split()).encode()).hexdigest()
        if k not in self._cache:
            data = parse_json(self.llm.complete(SYSTEM, USER % text[:2000]))
            if not isinstance(data, dict):
                raise LLMError("LLM returned JSON that is not an object")
            self._cache[k] = data
        return self._cache[k]

    def _verify(self, c):
        out = {"exists": False, "spotify": None, "lyrics": None, "via": []}
        if self.spotify:
            try:
                sp = self.spotify.resolve(c["title"], c["artist"])
            except SpotifyError:
                sp = None
            if sp:
                out.update(exists=True, spotify=sp)
                out["via"].append("Spotify")
        if self.lyrics:
            lx = self.lyrics.analyze(c["title"], primary_artist(c["artist"]), (out["spotify"] or {}).get("duration_s"))
            if lx:
                out.update(exists=True, lyrics=lx)
                out["via"].append("LRCLIB lyrics")
        return out

    @staticmethod
    def _merge_analysis(local, an):
        themes = dict(local["themes"])
        for t in an.get("themes") or []:
            if t in THEME_PATTERNS:
                themes[t] = max(themes.get(t, 0.0), 1.0)
        v, e = _num(an.get("valence"), local["valence"]), _num(an.get("energy"), local["energy"])
        persp = an.get("perspective")
        return {"themes": themes, "valence": v, "energy": e, "mood": mood_label(v, e),
                "summary": (str(an.get("summary") or "")[:300] or None),
                "perspective": persp if isinstance(persp, str) else None}

    def pick(self, text, debug=False, no_alcohol=False, allow_llm=True):
        """One song + a prescription (diagnosis, dosage, paired drink). Always returns a result."""
        out = self._pick(text, debug, allow_llm)
        raw = None
        if out["song"]["source"] == "llm":
            data = self._cache.get(hashlib.sha256(" ".join(text.lower().split()).encode()).hexdigest()) or {}
            raw = data.get("rx")
        out["rx"] = prescribe(self.bar, out["analysis"], raw, serious=bool(out["safety_note"]), no_alcohol=no_alcohol)
        return out

    def _pick(self, text, debug=False, allow_llm=True):
        base = self.matcher.match(text, top_k=len(self.matcher.songs))  # raises ValueError on empty input
        if not self.llm:
            return self._fallback(base, "LLM not configured — picked from the built-in catalogue.", [], debug)
        if not allow_llm:
            return self._fallback(base, "Today's free AI quota is used up — picked from the built-in catalogue.", [], debug)
        try:
            data = self._ask(text)
            cands = clean_candidates(data.get("candidates"))
            if not cands:
                raise LLMError("no usable candidates")
        except LLMError as e:
            return self._fallback(base, "LLM unavailable (%s) — picked from the built-in catalogue." % e, [], debug)

        an = data.get("analysis") if isinstance(data.get("analysis"), dict) else {}
        analysis = self._merge_analysis(base["analysis"], an)
        safety = SAFETY_NOTE if (base["safety_note"] or an.get("safety_concern") is True) else None
        user_themes = {t for t, w in analysis["themes"].items() if w >= 1}
        catalogue = {(norm(r["title"]), norm(primary_artist(r["artist"]))): r["score"] for r in base["results"]}

        with ThreadPoolExecutor(max_workers=6) as ex:
            checks = list(ex.map(self._verify, cands))
        rows = []
        for c, chk in zip(cands, checks):
            lx = chk["lyrics"]
            overlap = len(user_themes & set(lx["themes"])) / len(user_themes) if (lx and user_themes) else 0.0
            cat = catalogue.get((norm(c["title"]), norm(primary_artist(c["artist"]))), 0.0)
            final = self.W_LLM * c["confidence"] + self.W_SUPPORT * max(overlap, cat) if chk["exists"] else 0.0
            rows.append(dict(c, verified=chk["exists"], via=chk["via"], spotify=chk["spotify"],
                             lyric_overlap=round(overlap, 2), catalogue=round(cat, 2), final=round(final, 3)))
        rows.sort(key=lambda r: (-r["final"], -r["confidence"]))
        best = rows[0]
        if not best["verified"]:
            return self._fallback(base, "None of the LLM's suggestions could be verified as real songs — "
                                        "picked from the built-in catalogue.", rows, debug, analysis, safety)
        if best["final"] < self.min_confidence:
            return self._fallback(base, "LLM's best pick was low-confidence (%.0f%%) — picked from the built-in "
                                        "catalogue." % (best["final"] * 100), rows, debug, analysis, safety)

        sp = best["spotify"]
        title, artist = (sp["title"], sp["artist"]) if sp else (best["title"], best["artist"])
        reasons = [best["why"]] if best["why"] else []
        reasons.append("Verified as a real track via " + " + ".join(best["via"]))
        if best["lyric_overlap"]:
            reasons.append("Its lyrics also touch on your themes (%.0f%% overlap)" % (best["lyric_overlap"] * 100))
        song = {"title": title, "artist": artist, "year": (sp or {}).get("year") or best["year"],
                "confidence": best["final"], "llm_confidence": round(best["confidence"], 3), "source": "llm",
                "model": self.llm.describe(), "reasons": reasons, "links": links(title, artist, sp), "spotify": sp}
        out = {"analysis": analysis, "song": song, "safety_note": safety, "note": None}
        if debug:
            out["candidates"] = rows
        return out

    def _fallback(self, base, note, rows, debug, analysis=None, safety=None):
        r = dict(base["results"][0])
        r["confidence"] = r.pop("score")
        r.pop("breakdown", None)
        r["source"] = "catalogue"
        r["links"] = dict(r["links"])
        if self.spotify:
            try:
                self.spotify.enrich([r])
            except SpotifyError:
                pass
        out = {"analysis": analysis or base["analysis"], "song": r, "safety_note": safety or base["safety_note"], "note": note}
        if debug:
            out["candidates"] = rows
        return out


def build_picker(use_llm=True, offline=False, market=None):
    """Wire up whatever is configured in .env. Returns (picker, [status notes])."""
    from .core import SongMatcher
    from .lyrics import LyricsAnalyzer
    from .spotify import SpotifyClient

    notes, llm, sp, lyr = [], None, None, None
    if not offline:
        sp = SpotifyClient.from_env(market=market)
        lyr = LyricsAnalyzer()
        if use_llm:
            llm, why = LLMClient.from_env()
            if why:
                notes.append(why)
    return SongPicker(SongMatcher(), llm=llm, spotify=sp, lyrics=lyr), notes


def _cli(argv=None):
    """python3 -m songmatch.llm check  — one real call to prove the configured LLM works."""
    import sys
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] != ["check"]:
        print("usage: python3 -m songmatch.llm check")
        return 2
    llm, why = LLMClient.from_env()
    if not llm:
        print("✗ " + why)
        return 1
    t0 = time.time()
    try:
        reply = parse_json(llm.complete('Reply with exactly this JSON: {"ok": true}', "ping", max_tokens=200))
    except LLMError as e:
        print("✗ %s: %s" % (llm.describe(), e))
        return 1
    print("✓ %s works (%.1fs) — reply: %s" % (llm.describe(), time.time() - t0, json.dumps(reply)[:60]))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
