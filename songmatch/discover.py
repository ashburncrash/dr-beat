"""Discover songs beyond the curated catalogue: Spotify search -> lyric verification -> score.

Spotify search alone is noisy (a track called "Toxic" isn't necessarily about a toxic
relationship), so every candidate must have lyrics on LRCLIB whose detected themes
overlap the user's. Candidates without verifiable lyrics are dropped, never guessed.
"""
import math
from concurrent.futures import ThreadPoolExecutor

from .core import mood_label
from .spotify import SpotifyError, _BAD_VERSION, norm

THEME_QUERIES = {
    "cheating": ["cheating", "cheated on me"],
    "betrayal": ["betrayal", "you lied"],
    "jealousy": ["jealous", "with someone new"],
    "situationship": ["situationship", "what are we"],
    "uncertainty": ["mixed signals"],
    "long_distance": ["long distance", "miles away"],
    "breakup": ["breakup", "break up"],
    "heartbreak": ["heartbreak", "broken heart"],
    "missing": ["miss you", "still love you"],
    "moving_on": ["moving on", "over you"],
    "empowerment": ["better without you"],
    "unrequited": ["unrequited love", "love me back"],
    "crush": ["crush", "butterflies"],
    "in_love": ["in love", "love song"],
    "commitment": ["marry me", "forever"],
    "toxic": ["toxic love", "red flags"],
    "on_off": ["on and off", "back together"],
    "regret": ["regret", "my mistake"],
    "apology": ["sorry", "forgive me"],
    "reconciliation": ["come back to me", "second chance"],
    "divorce": ["divorce"],
    "drifting_apart": ["drifting apart", "falling out of love"],
    "loneliness": ["lonely"],
    "anger": ["hate you"],
    "casual": ["one night"],
}


def discover(analysis, spotify, lyrics, exclude=(), k=3, max_candidates=24):
    """Return up to k verified discovered tracks for a matcher analysis dict."""
    themes = {t: w for t, w in analysis["themes"].items() if w >= 1.0}
    if not themes:
        return []
    top = sorted(themes, key=lambda t: -themes[t])[:3]
    queries = []
    for t in top:
        queries += THEME_QUERIES.get(t, [t.replace("_", " ")])[:2]
    queries = queries[:6]

    excl = {(norm(t), norm(a.split(",")[0])) for t, a in exclude}
    cands, hits, errors = {}, {}, 0
    for q in queries:
        try:
            items = spotify.search_tracks(q, limit=10)
        except SpotifyError:
            errors += 1
            if errors == len(queries):
                raise
            continue
        for it in items:
            sig = (norm(it["title"]), norm(it["artist"].split(",")[0]))
            if sig in excl or _BAD_VERSION.search(it["title"]):
                continue
            if sig not in cands:
                cands[sig] = dict(it, query=q)
            hits[sig] = hits.get(sig, 0) + 1
    pool = sorted(cands, key=lambda s: -hits[s])[:max_candidates]

    with ThreadPoolExecutor(max_workers=6) as ex:
        signals = list(ex.map(lambda s: lyrics.analyze(cands[s]["title"], cands[s]["artist"].split(",")[0],
                                                       cands[s]["duration_s"]), pool))
    total = sum(themes.values())
    out = []
    for sig, lx in zip(pool, signals):
        if not lx:
            continue
        shared = [t for t in themes if t in lx["themes"]]
        theme_score = sum(themes[t] for t in shared) / total
        if theme_score < 0.3:
            continue
        mood = 1 - math.hypot(lx["valence"] - analysis["valence"], lx["energy"] - analysis["energy"]) / math.sqrt(2)
        score = 0.6 * theme_score + 0.3 * mood + 0.1 * min(1.0, hits[sig] / 2)
        c = cands[sig]
        out.append(dict(c, score=round(score, 3), reasons=[
            "Lyrics touch on: " + ", ".join(t.replace("_", " ") for t in shared),
            "Lyric mood reads %s (yours: %s)" % (mood_label(lx["valence"], lx["energy"]), analysis["mood"]),
            'Found via Spotify search "%s"; themes verified from lyrics (LRCLIB)' % c["query"],
        ]))
    out.sort(key=lambda r: -r["score"])
    return out[:k]
