"""Cocktail pairing + the "prescription" around the picked song.

Drinks come only from data/cocktails.json (well-known classics, each with a 0% version).
The LLM may choose one by name in the same call that picks the song; unknown names are
ignored and a local theme/mood match is used instead, so the result is always a real recipe.
Serious situations (safety note) always get the alcohol-free version and a gentle tone.
"""
import json
from pathlib import Path

from .spotify import norm

PATH = Path(__file__).resolve().parent.parent / "data" / "cocktails.json"

NICK = {
    "breakup": "post-breakup blues", "heartbreak": "acute heartache", "cheating": "trust fracture",
    "betrayal": "trust fracture", "jealousy": "green-eye syndrome", "unrequited": "one-sided crush fever",
    "crush": "butterfly overload", "in_love": "terminal sweetness", "commitment": "ring-finger tingle",
    "long_distance": "time-zone longing", "missing": "chronic missing-you", "toxic": "sweet-poison dependency",
    "moving_on": "recovery glow", "empowerment": "main-character energy", "self_love": "self-love surplus",
    "regret": "what-if-itis", "apology": "sorry-itis", "reconciliation": "second-chance flutter",
    "on_off": "on-again-off-again syndrome", "situationship": "undefined-relationship syndrome",
    "uncertainty": "mixed-signal static", "drifting_apart": "fading-spark syndrome", "divorce": "big-change ache",
    "loneliness": "lonely-night ache", "anger": "boiling-point fever", "trust_issues": "guarded-heart syndrome",
    "casual": "fling fever", "guilt": "guilty conscience", "insecurity": "not-enough-itis",
}
DOSAGE = {
    "euphoric": "1 listen, loud. Repeat as needed.",
    "warm": "1 listen, slowly. Slow-dancing optional.",
    "angry": "1 listen at full volume. Scream-singing permitted.",
    "melancholy": "1 listen, lights off, tissues on standby.",
    "restless": "1 listen on a late-night walk. Do NOT text them during.",
    "wistful": "1 listen while staring out of a window.",
}
SERIOUS_DOSAGE = "Listen whenever you need it. Please be gentle with yourself, and reach out to someone you trust."


def _mood_key(analysis):
    return (analysis.get("mood") or "").split(" ")[0]


def diagnose(analysis):
    top = sorted((t for t, w in analysis.get("themes", {}).items() if w >= 1),
                 key=lambda t: -analysis["themes"][t])
    names = [NICK.get(t, t.replace("_", " ")) for t in top[:2]]
    if not names:
        return "A mild case of the feels."
    d = "Acute " + names[0]
    if len(names) > 1 and names[1] != names[0]:
        d += ", with complications of " + names[1]
    return d + "."


class Bar:
    def __init__(self, path=PATH):
        self.drinks = json.loads(Path(path).read_text(encoding="utf-8"))
        self._by = {norm(d["name"]): d for d in self.drinks}

    def names(self):
        return [d["name"] for d in self.drinks]

    def find(self, name):
        return self._by.get(norm(name or "")) if name else None

    def best(self, analysis):
        themes = {t: w for t, w in analysis.get("themes", {}).items() if w >= 1}
        mood = _mood_key(analysis)

        def score(i_d):
            i, d = i_d
            return (sum(themes.get(t, 0) for t in d["themes"]) + (0.75 if mood in d["moods"] else 0), -i)

        return max(enumerate(self.drinks), key=score)[1]


def serve(d, why=None, mocktail_only=False):
    out = {"name": d["name"], "glass": d["glass"], "color": d["color"], "ingredients": d["ingredients"],
           "why": why or d["note"], "alcohol_free": False, "mocktail": d["mocktail"]}
    if mocktail_only:
        out.update(name=d["mocktail"]["name"], ingredients=d["mocktail"]["ingredients"],
                   alcohol_free=True, mocktail=None)
    return out


def _clip(s, n):
    s = str(s or "").strip()
    return s[:n] if s else None


def prescribe(bar, analysis, rx=None, serious=False, no_alcohol=False):
    """Build {diagnosis, dosage, drink}. rx is the LLM's raw 'rx' object (may be junk or None)."""
    rx = rx if isinstance(rx, dict) else {}
    c = rx.get("cocktail")
    name = c.get("name") if isinstance(c, dict) else (c if isinstance(c, str) else None)
    why = c.get("why") if isinstance(c, dict) else None
    d = bar.find(name)
    source = "llm" if d else "bar"
    if d is None:
        d, why = bar.best(analysis), None
    drink = serve(d, _clip(why, 240), mocktail_only=serious or no_alcohol)
    drink["source"] = source
    if serious:
        return {"diagnosis": None, "dosage": SERIOUS_DOSAGE, "drink": drink, "serious": True}
    return {"diagnosis": _clip(rx.get("diagnosis"), 160) or diagnose(analysis),
            "dosage": _clip(rx.get("dosage"), 160) or DOSAGE.get(_mood_key(analysis), DOSAGE["wistful"]),
            "drink": drink, "serious": False}
