"""Build the GitHub Pages copy of the front end into docs/.

    python3 scripts/build_pages.py https://dr-beat-clinic.onrender.com

The page is identical to songmatch/static/index.html except that API calls go to the
given backend URL instead of the same origin.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main(argv):
    if len(argv) != 1 or not re.match(r"^(https://[\w.-]+|http://(localhost|127\.0\.0\.1)(:\d+)?)/?$", argv[0]):
        print("usage: python3 scripts/build_pages.py https://<your-service>.onrender.com")
        return 2
    api = argv[0].rstrip("/")
    src = (ROOT / "songmatch" / "static" / "index.html").read_text(encoding="utf-8")
    tag = '<meta name="api-base" content="">'
    if tag not in src:
        print("api-base meta tag not found in index.html")
        return 1
    out = ROOT / "docs"
    out.mkdir(exist_ok=True)
    (out / "index.html").write_text(src.replace(tag, '<meta name="api-base" content="%s">' % api), encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    print("✓ docs/index.html -> API at " + api)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
