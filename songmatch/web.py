"""Tiny stdlib web server: python3 -m songmatch.web  -> http://localhost:8765

API keys stay server-side; the browser only receives the single picked song.
For a public deployment (e.g. Render) set:
  ALLOWED_ORIGINS=https://<user>.github.io   (CORS for the GitHub Pages front end)
  RATE_PER_MIN / RATE_PER_DAY                (per-visitor caps, default 6 / 60)
  LLM_DAILY_BUDGET                           (global AI calls per UTC day, default 200)
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .limits import DailyBudget, RateLimiter
from .llm import build_picker

INDEX = (Path(__file__).parent / "static" / "index.html").read_bytes()
PICKER, NOTES = build_picker()
ORIGINS = {o.strip().rstrip("/") for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()}
PUBLIC = bool(ORIGINS) or os.environ.get("PUBLIC") == "1"
if PUBLIC and PICKER.llm and PICKER.llm.provider == "claude-cli":
    # A personal Claude subscription must not serve the public.
    NOTES.append("claude-cli disabled on a public deployment — use an API provider (e.g. gemini)")
    PICKER.llm = None
LIMITER = RateLimiter(int(os.environ.get("RATE_PER_MIN", 6)), int(os.environ.get("RATE_PER_DAY", 60)))
BUDGET = DailyBudget(int(os.environ.get("LLM_DAILY_BUDGET", 200)))
MAX_BODY = 8000


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        origin = (self.headers.get("Origin") or "").rstrip("/")
        if origin and origin in ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Max-Age", "600")

    def _send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _err(self, code, msg):
        self._send(code, json.dumps({"error": msg}).encode())

    def _client(self):
        fwd = self.headers.get("X-Forwarded-For")
        return fwd.split(",")[0].strip() if fwd else self.client_address[0]

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send(200, INDEX, "text/html")
        elif path == "/api/status":
            self._send(200, json.dumps({"llm": PICKER.llm.describe() if PICKER.llm else None,
                                        "spotify": bool(PICKER.spotify),
                                        "ai_left_today": BUDGET.remaining() if PICKER.llm else 0}).encode())
        else:
            self._err(404, "not found")

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/api/match":
            return self._err(404, "not found")
        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return self._err(400, "bad request")
        if n > MAX_BODY:
            return self._err(413, "That's a long story — please keep it under a few paragraphs.")
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
            text = str(req.get("text", ""))[:2000]
        except (ValueError, AttributeError):
            return self._err(400, "bad request")
        if len(text.strip()) < 3:
            return self._err(400, "Tell me a bit more about what's going on (at least a few words).")
        if not LIMITER.allow(self._client()):
            return self._err(429, "The doctor needs a breather — too many visits. Try again in a minute.")
        allow_llm = bool(PICKER.llm) and BUDGET.take()
        try:
            out = PICKER.pick(text, debug=bool(req.get("debug")) and not PUBLIC,
                              no_alcohol=bool(req.get("no_alcohol")), allow_llm=allow_llm)
        except ValueError as e:
            return self._err(400, str(e))
        self._send(200, json.dumps(out, ensure_ascii=False).encode())

    def log_message(self, *a):
        pass  # never log request bodies (they contain the user's personal story)


def main():
    port = int(os.environ.get("PORT", 8765))
    print("Song matcher on http://localhost:%d | LLM: %s | Spotify: %s | CORS: %s"
          % (port, PICKER.llm.describe() if PICKER.llm else "off", "on" if PICKER.spotify else "off",
             ", ".join(sorted(ORIGINS)) or "same-origin only"))
    for n in NOTES:
        print("  [setup] " + n)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
