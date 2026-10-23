"""Core matching engine: theme detection + mood estimation + text similarity.

Pure standard library (Python 3.9+). No API keys, no downloads.
"""
import json
import math
import random
import re
from collections import Counter
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "songs.json"

# ---------------------------------------------------------------- themes
# theme -> regex fragments (matched on lowercase input, word-boundary anchored)
THEME_PATTERNS = {
    "breakup": [r"broke up", r"break ?up", r"breaking up", r"dumped", r"split up", r"ended (it|things)", r"\bex\b", r"left me", r"broke (it )?off"],
    "cheating": [r"cheat\w*", r"affair", r"unfaithful", r"slept with", r"hooked up with", r"side ?(chick|piece)", r"another (girl|guy|woman|man)"],
    "betrayal": [r"betray\w*", r"lied to me", r"\blie[sd]?\b", r"behind my back", r"trust(ed)? (him|her|them)"],
    "jealousy": [r"jealous\w*", r"envious", r"new (girlfriend|boyfriend|partner|gf|bf)", r"with someone (else|new)", r"moved on (so )?(fast|quickly)", r"rebound"],
    "unrequited": [r"unrequited", r"doesn'?t (like|love|want) me( back)?", r"don'?t (like|love|want) me( back)?", r"one[- ]sided", r"friend ?zone", r"not interested in me", r"only sees me as a friend"],
    "crush": [r"crush", r"\bmet (someone|a (guy|girl))", r"butterflies", r"asked (him|her|them) out", r"first date", r"talking to (someone|a (guy|girl))", r"\bnervous\b"],
    "in_love": [r"in love", r"love (him|her|them|my (partner|husband|wife|boyfriend|girlfriend))", r"soul ?mate", r"\bthe one\b", r"so happy (with|together)", r"best (relationship|thing)", r"anniversary"],
    "commitment": [r"engaged", r"propos\w*", r"marr(y|ied|iage)", r"wedding", r"move in together", r"moving in", r"forever", r"commit\w*", r"\bring\b"],
    "long_distance": [r"long[- ]distance", r"\bldr\b", r"different (city|country|countries|time ?zones?)", r"far away", r"miles", r"time ?zone", r"visa", r"\bflight\b"],
    "missing": [r"\bmiss(ing)? (him|her|them|you|my)", r"can'?t stop thinking", r"still think about", r"still love", r"stuck on", r"get over"],
    "toxic": [r"toxic", r"manipulat\w*", r"gaslight\w*", r"red flags?", r"controlling", r"possessive", r"keep going back", r"addicted to (him|her|them)"],
    "abuse": [r"abus\w*", r"hits? me", r"\bhit me\b", r"hurts? me physically", r"violent", r"scared of (him|her|them)", r"threaten\w*"],
    "moving_on": [r"moving on", r"move on", r"moved on", r"over (him|her|them|it)", r"better off", r"free(dom)? (now|at last)", r"healing", r"\bdone with\b"],
    "empowerment": [r"stronger", r"deserve better", r"know my worth", r"\bworth\b", r"independent", r"don'?t need (him|her|them|anyone)"],
    "self_love": [r"love myself", r"self[- ]love", r"focus(ing)? on (me|myself)", r"single and"],
    "regret": [r"regret\w*", r"my fault", r"messed (it )?up", r"screwed up", r"shouldn'?t have", r"wish i (had|hadn'?t|could)", r"take it back"],
    "apology": [r"\bsorry\b", r"apologi[sz]\w*", r"forgive", r"second chance", r"one more chance"],
    "reconciliation": [r"back together", r"get (him|her|them) back", r"win (him|her|them) back", r"make it work", r"try again", r"reconcil\w*"],
    "on_off": [r"on and off", r"on[- ]again", r"off[- ]again", r"keeps? coming back", r"back and forth",
               r"keep (breaking up|getting back)", r"break(ing)? up and get(ting)? back", r"again and again"],
    "situationship": [r"situationship", r"what we are", r"what are we", r"not official", r"no label", r"mixed signals", r"friends with benefits", r"\bfwb\b", r"late[- ]night (texts?|calls?)", r"booty call", r"only (texts?|calls?) (me )?(at night|when)"],
    "uncertainty": [r"don'?t know (if|what|whether)", r"confus\w*", r"not sure", r"uncertain", r"mixed signals", r"hot and cold"],
    "miscommunication": [r"don'?t (talk|communicate)", r"never listens?", r"misunderstand\w*", r"we (keep )?fight\w*", r"argu\w*", r"silent treatment"],
    "divorce": [r"divorc\w*", r"separat(ed|ion|ing)", r"custody", r"our (kids|children)"],
    "family": [r"\bkids?\b", r"children", r"parents? (hate|don'?t like|disapprove)", r"family"],
    "forbidden": [r"parents? (hate|don'?t like|disapprove|won'?t)", r"forbidden", r"secret relationship", r"not allowed"],
    "loneliness": [r"lonely", r"alone", r"by myself", r"no one", r"nobody", r"isolated"],
    "anger": [r"angry", r"furious", r"pissed", r"\bmad\b", r"hate (him|her|them)", r"rage", r"so done"],
    "drifting_apart": [r"drift\w* apart", r"grow(ing|n)? apart", r"spark (is )?gone", r"roommates", r"boring", r"routine", r"fell out of love", r"falling out of love"],
    "growing_apart": [r"grow(ing|n)? apart", r"outgr\w*", r"want different things"],
    "trust_issues": [r"trust issues", r"can'?t trust", r"afraid (to|of) (get|getting) hurt", r"guard (is )?up", r"scared to love"],
    "insecurity": [r"insecur\w*", r"not good enough", r"too much for", r"compare myself", r"prettier", r"better than me"],
    "unequal_effort": [r"pay(s|ing)? for everything", r"one doing all", r"no effort", r"doesn'?t try", r"use[sd]? me", r"lazy", r"money"],
    "casual": [r"one[- ]night", r"fling", r"hook ?up", r"casual\w*", r"nothing serious"],
    "guilt": [r"guilt\w*", r"i cheated", r"feel terrible about"],
    "support": [r"going through (a lot|something)", r"depress\w*", r"grief", r"griev\w*", r"lost (his|her|their) (mom|dad|mother|father|job)", r"help (him|her|them)", r"be there for"],
    "obsession": [r"obsess\w*", r"stalk\w*", r"can'?t stop checking"],
    "heartbreak": [r"heart ?broken", r"heartbreak", r"devastat\w*", r"crushed", r"broke my heart", r"crying"],
}
# Some themes imply softer neighbours (used as partial credit).
THEME_NEIGHBOURS = {
    "breakup": ["heartbreak", "missing"],
    "cheating": ["betrayal", "anger"],
    "betrayal": ["cheating", "anger"],
    "unrequited": ["heartbreak", "loneliness"],
    "situationship": ["uncertainty"],
    "on_off": ["toxic", "frustration"],
    "abuse": ["toxic"],
    "divorce": ["breakup", "family"],
    "drifting_apart": ["growing_apart", "routine"],
    "moving_on": ["empowerment", "acceptance"],
    "missing": ["loneliness"],
}

_COMPILED = {t: [re.compile(r"\b" + p) for p in ps] for t, ps in THEME_PATTERNS.items()}


def detect_themes(text):
    """Return {theme: weight}. Direct hits weigh 1.0, neighbours 0.4."""
    low = text.lower()
    hits = {}
    for theme, regs in _COMPILED.items():
        n = sum(1 for r in regs if r.search(low))
        if n:
            hits[theme] = 1.0 + 0.25 * (n - 1)
    # Context overrides: being married/committed is the backdrop, not the feeling,
    # when the text is really about the relationship fading or ending.
    if "commitment" in hits and ({"drifting_apart", "divorce", "cheating", "toxic", "abuse"} & set(hits)):
        hits["commitment"] = 0.2
    # A repeating break-up cycle is "on_off", not a one-time breakup or a reunion.
    if "on_off" in hits:
        for t in ("breakup", "reconciliation"):
            if t in hits:
                hits[t] = 0.3
    out = dict(hits)
    for theme in hits:
        for nb in THEME_NEIGHBOURS.get(theme, []):
            out.setdefault(nb, 0.4)
    return out


# ---------------------------------------------------------------- mood
# word -> (valence delta, energy delta); valence/energy live in [0, 1]
MOOD_LEXICON = {
    "happy": (0.8, 0.2), "happiest": (0.9, 0.3), "joy": (0.8, 0.3), "excited": (0.7, 0.6),
    "love": (0.4, 0.0), "amazing": (0.7, 0.3), "great": (0.5, 0.1), "wonderful": (0.7, 0.1),
    "grateful": (0.6, 0.0), "thankful": (0.6, 0.0), "lucky": (0.6, 0.1), "perfect": (0.6, 0.1),
    "giddy": (0.8, 0.6), "butterflies": (0.6, 0.4), "free": (0.5, 0.3), "relieved": (0.5, -0.1),
    "better": (0.4, 0.1), "stronger": (0.5, 0.4), "proud": (0.5, 0.3), "fine": (0.2, 0.0),
    "calm": (0.3, -0.5), "peaceful": (0.4, -0.5), "cozy": (0.5, -0.4), "safe": (0.4, -0.3),
    "sad": (-0.7, -0.3), "depressed": (-0.9, -0.5), "crying": (-0.7, 0.0), "cry": (-0.6, 0.0),
    "heartbroken": (-0.9, -0.2), "devastated": (-0.9, 0.1), "empty": (-0.7, -0.6), "numb": (-0.6, -0.6),
    "lonely": (-0.7, -0.4), "alone": (-0.5, -0.3), "miss": (-0.4, -0.2), "hurt": (-0.6, 0.0),
    "hurts": (-0.6, 0.0), "pain": (-0.6, 0.1), "tired": (-0.4, -0.6), "exhausted": (-0.5, -0.6),
    "lost": (-0.5, -0.2), "regret": (-0.5, -0.1), "sorry": (-0.3, -0.1), "guilty": (-0.6, 0.0),
    "angry": (-0.4, 0.8), "furious": (-0.5, 0.9), "mad": (-0.3, 0.7), "pissed": (-0.4, 0.8),
    "hate": (-0.5, 0.7), "rage": (-0.5, 0.9), "betrayed": (-0.7, 0.6), "jealous": (-0.4, 0.6),
    "anxious": (-0.4, 0.6), "nervous": (0.0, 0.5), "scared": (-0.5, 0.4), "confused": (-0.3, 0.2),
    "frustrated": (-0.4, 0.6), "annoyed": (-0.3, 0.5), "done": (0.0, 0.4), "worthless": (-0.8, -0.3),
    "stuck": (-0.4, -0.3), "bored": (-0.3, -0.6), "boring": (-0.3, -0.6),
}
NEGATORS = {"not", "no", "never", "dont", "don't", "isnt", "isn't", "wasnt", "wasn't", "cant", "can't", "anymore", "hardly"}
# Theme mood priors fill in when the text has few emotion words.
THEME_MOOD = {
    "breakup": (0.25, 0.4), "heartbreak": (0.15, 0.35), "cheating": (0.3, 0.75), "betrayal": (0.3, 0.7),
    "jealousy": (0.35, 0.7), "unrequited": (0.25, 0.4), "crush": (0.75, 0.7), "in_love": (0.7, 0.45),
    "commitment": (0.75, 0.55), "long_distance": (0.45, 0.3), "missing": (0.25, 0.3), "toxic": (0.4, 0.8),
    "abuse": (0.3, 0.75), "moving_on": (0.65, 0.75), "empowerment": (0.65, 0.85), "self_love": (0.6, 0.55),
    "regret": (0.2, 0.35), "apology": (0.3, 0.45), "reconciliation": (0.45, 0.5), "on_off": (0.55, 0.7),
    "situationship": (0.4, 0.55), "uncertainty": (0.4, 0.55), "divorce": (0.2, 0.3), "loneliness": (0.2, 0.3),
    "anger": (0.35, 0.85), "drifting_apart": (0.3, 0.45), "casual": (0.5, 0.6), "support": (0.3, 0.4),
}


def tokenize(text):
    return re.findall(r"[a-z']+", text.lower())


def estimate_mood(text, themes=None):
    """Estimate (valence, energy) in [0,1] from text, with simple negation handling."""
    toks = tokenize(text)
    v_sum, e_sum, hits = 0.0, 0.0, 0
    for i, tok in enumerate(toks):
        if tok not in MOOD_LEXICON:
            continue
        dv, de = MOOD_LEXICON[tok]
        window = toks[max(0, i - 3):i] + toks[i + 1:i + 2]  # "not happy", "happy anymore"
        if any(w in NEGATORS for w in window):
            dv = -dv * 0.7
        v_sum += dv
        e_sum += de
        hits += 1
    lex_v = 0.5 + 0.5 * math.tanh(v_sum / max(hits, 1)) if hits else None
    lex_e = 0.5 + 0.5 * math.tanh(e_sum / max(hits, 1)) if hits else None

    # Intensity cues: !!, ALL CAPS words.
    bangs = text.count("!")
    caps = sum(1 for w in re.findall(r"\b[A-Z]{3,}\b", text))
    energy_boost = min(0.25, 0.06 * bangs + 0.06 * caps)

    prior = None
    if themes:
        pri = [(THEME_MOOD[t], w) for t, w in themes.items() if t in THEME_MOOD]
        if pri:
            tw = sum(w for _, w in pri)
            prior = (sum(m[0] * w for m, w in pri) / tw, sum(m[1] * w for m, w in pri) / tw)

    if lex_v is not None and prior is not None:
        conf = min(1.0, hits / 3.0)  # more emotion words -> trust lexicon more
        v = conf * lex_v + (1 - conf) * prior[0]
        e = conf * lex_e + (1 - conf) * prior[1]
    elif lex_v is not None:
        v, e = lex_v, lex_e
    elif prior is not None:
        v, e = prior
    else:
        v, e = 0.5, 0.5
    return round(v, 3), round(min(1.0, e + energy_boost), 3)


def mood_label(v, e):
    if v >= 0.55:
        return "euphoric / upbeat" if e >= 0.55 else "warm / tender"
    if v <= 0.4:
        return "angry / defiant" if e >= 0.6 else "melancholy / aching"
    return "restless / conflicted" if e >= 0.55 else "wistful / bittersweet"


# ---------------------------------------------------------------- text similarity (TF-IDF)
STOP = set("a an the and or but if to of in on at for with is was are were be been i me my we our you your he him his she her they them their it its that this so just really very have has had do does did not no".split())


def _stem(tok):
    for suf in ("ing", "ed", "ly", "es", "s"):
        if len(tok) > 4 and tok.endswith(suf):
            return tok[: -len(suf)]
    return tok


def _terms(text):
    return [_stem(t) for t in tokenize(text) if t not in STOP and len(t) > 2]


class TfidfIndex:
    def __init__(self, docs):
        self.docs = [Counter(_terms(d)) for d in docs]
        n = len(docs)
        df = Counter(t for d in self.docs for t in d)
        self.idf = {t: math.log((1 + n) / (1 + c)) + 1 for t, c in df.items()}
        self.vecs = [self._vec(d) for d in self.docs]

    def _vec(self, counts):
        v = {t: (1 + math.log(c)) * self.idf.get(t, 0) for t, c in counts.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {t: x / norm for t, x in v.items()}

    def query(self, text):
        q = self._vec(Counter(_terms(text)))
        return [sum(q.get(t, 0) * x for t, x in d.items()) for d in self.vecs]


# ---------------------------------------------------------------- matcher
SAFETY_NOTE = (
    "If you're being hurt, threatened, or feel unsafe, you deserve support. "
    "Please reach out to someone you trust or a local domestic-violence hotline "
    "(e.g. US 1-800-799-7233, UK 0808 2000 247, Korea 1366, or your local emergency number)."
)


def load_songs(path=DATA_PATH):
    with open(path, encoding="utf-8") as f:
        songs = json.load(f)
    for s in songs:
        for k in ("title", "artist", "themes", "summary", "valence", "energy"):
            if k not in s:
                raise ValueError("song missing %r: %r" % (k, s))
    return songs


class SongMatcher:
    W_THEME, W_TEXT, W_MOOD = 0.5, 0.25, 0.25

    def __init__(self, songs=None):
        self.songs = songs if songs is not None else load_songs()
        docs = ["%s %s %s" % (s["summary"], " ".join(s["themes"]).replace("_", " "), s["title"]) for s in self.songs]
        self.index = TfidfIndex(docs)

    def analyze(self, text):
        themes = detect_themes(text)
        v, e = estimate_mood(text, themes)
        return {"themes": themes, "valence": v, "energy": e, "mood": mood_label(v, e)}

    def match(self, text, top_k=3, surprise=False, seed=None):
        text = (text or "").strip()
        if len(text) < 3:
            raise ValueError("Tell me a bit more about what's going on (at least a few words).")
        a = self.analyze(text)
        themes = a["themes"]
        text_sims = self.index.query(text)
        max_sim = max(text_sims) or 1.0
        total_theme = sum(themes.values()) or 1.0

        scored = []
        for s, tsim in zip(self.songs, text_sims):
            # Theme score: weighted fraction of user themes the song covers,
            # with song's first-listed (primary) theme counting a bit more.
            cover = 0.0
            for i, t in enumerate(s["themes"]):
                if t in themes:
                    cover += themes[t] * (1.15 if i == 0 else 1.0)
            theme_score = min(1.0, cover / total_theme)
            dist = math.hypot(s["valence"] - a["valence"], s["energy"] - a["energy"]) / math.sqrt(2)
            mood_score = 1 - dist
            text_score = tsim / max_sim
            if themes:
                score = self.W_THEME * theme_score + self.W_TEXT * text_score + self.W_MOOD * mood_score
            else:  # no explicit theme words: lean on text + mood
                score = 0.55 * text_score + 0.45 * mood_score
            scored.append((score, s, theme_score, text_score, mood_score))

        scored.sort(key=lambda x: (-x[0], x[1]["title"]))
        pool = scored[: max(top_k, 6)] if surprise else scored
        if surprise:
            rng = random.Random(seed)
            weights = [max(p[0], 0.01) ** 3 for p in pool]
            first = rng.choices(pool, weights=weights, k=1)[0]
            pool = [first] + [p for p in scored if p is not first]
        results = [self._explain(p, a) for p in pool[:top_k]]
        return {
            "analysis": a,
            "results": results,
            "safety_note": SAFETY_NOTE if "abuse" in themes and themes["abuse"] >= 1.0 else None,
        }

    @staticmethod
    def _explain(p, a):
        score, s, th, tx, md = p
        shared = [t for t in s["themes"] if t in a["themes"]]
        reasons = []
        if shared:
            reasons.append("Lyrical theme overlap: " + ", ".join(t.replace("_", " ") for t in shared))
        reasons.append("Song's story: " + s["summary"])
        reasons.append(
            "Sound/mood: song feels %s (valence %.2f, energy %.2f, ~%s BPM) vs. your %s"
            % (mood_label(s["valence"], s["energy"]), s["valence"], s["energy"], s.get("tempo", "?"), a["mood"])
        )
        q = (s["title"] + " " + s["artist"]).replace(" ", "+")
        return {
            "title": s["title"], "artist": s["artist"], "year": s.get("year"),
            "score": round(score, 3),
            "breakdown": {"theme": round(th, 3), "text": round(tx, 3), "mood": round(md, 3)},
            "reasons": reasons,
            "links": {
                "youtube": "https://www.youtube.com/results?search_query=" + q,
                "spotify": "https://open.spotify.com/search/" + (s["title"] + " " + s["artist"]).replace(" ", "%20"),
            },
        }
