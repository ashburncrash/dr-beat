"""CLI: python3 -m songmatch "my boyfriend cheated on me"   (or no args for interactive).

Returns ONE song. Uses the LLM + Spotify + LRCLIB if configured in .env, otherwise the
built-in catalogue matcher.
  --no-llm    skip the LLM (catalogue pick, still Spotify-linked if configured)
  --offline   no network at all
  --debug     with --json, include every LLM candidate and its verification score
"""
import argparse
import json
import sys

from .llm import build_picker


def render(out):
    a, s = out["analysis"], out["song"]
    themes = ", ".join(t.replace("_", " ") for t, w in sorted(a["themes"].items(), key=lambda x: -x[1]) if w >= 1) or "(none detected)"
    if a.get("summary"):
        print("\n  " + a["summary"])
    print("  themes: %s | mood: %s" % (themes, a["mood"]))
    print("\n  🎵 %s — %s (%s)   [confidence %.0f%%, %s]" % (s["title"], s["artist"], s.get("year") or "?",
                                                            s["confidence"] * 100, s["source"]))
    for r in s["reasons"]:
        print("    - " + r)
    sp = s.get("spotify")
    print("    > " + (sp["url"] if sp else s["links"]["youtube"]))
    rx = out.get("rx")
    if rx:
        d = rx["drink"]
        print("\n  ℞ " + ("CARE PLAN" if rx["serious"] else "DR. BEAT'S PRESCRIPTION"))
        if rx["diagnosis"]:
            print("    Diagnosis: " + rx["diagnosis"])
        print("    Dosage:    " + rx["dosage"])
        print("    Pair with: %s%s — %s" % (d["name"], " (0% ABV)" if d["alcohol_free"] else "", ", ".join(d["ingredients"])))
        print("               " + d["why"])
        if d.get("mocktail"):
            print("    0%% option: %s — %s" % (d["mocktail"]["name"], ", ".join(d["mocktail"]["ingredients"])))
    if out.get("note"):
        print("\n  (" + out["note"] + ")")
    if out["safety_note"]:
        print("\n  💛 " + out["safety_note"])
    print()


def main(argv=None):
    p = argparse.ArgumentParser(prog="songmatch", description="Find the one song that matches your relationship situation.")
    p.add_argument("text", nargs="*", help="describe your relationship issue or status")
    p.add_argument("--json", action="store_true")
    p.add_argument("--debug", action="store_true", help="include all scored candidates in --json output")
    p.add_argument("--no-llm", action="store_true")
    p.add_argument("--offline", action="store_true")
    p.add_argument("--market", default=None, help="Spotify market, e.g. US, KR")
    p.add_argument("--no-alcohol", action="store_true", help="always pair the alcohol-free version")
    args = p.parse_args(argv)
    picker, notes = build_picker(use_llm=not args.no_llm, offline=args.offline, market=args.market)
    for n in notes:
        print("  [setup] " + n, file=sys.stderr)

    def run(text):
        try:
            out = picker.pick(text, debug=args.debug, no_alcohol=args.no_alcohol)
        except ValueError as e:
            print("  " + str(e))
            return
        print(json.dumps(out, indent=2, ensure_ascii=False)) if args.json else render(out)

    if args.text:
        run(" ".join(args.text))
        return
    print("Tell me what's going on in your love life (empty line to quit).")
    while True:
        try:
            line = input("> ").strip()
        except EOFError:
            break
        if not line:
            break
        run(line)


if __name__ == "__main__":
    sys.exit(main())
