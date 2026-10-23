# 🩺 Dr. Beat — your relationship, prescribed as a song (and a cocktail)

**Live:** https://ashburncrash.github.io/dr-beat/

Tell Dr. Beat what's going on in your love life. The 8-bit clinic writes you a prescription: **one song** that fits your situation, a playful diagnosis and dosage, and a **paired cocktail** (every drink has an alcohol-free version).

Pure Python standard library (3.9+), no installs. Runs fully offline from a built-in catalogue; picks songs with an LLM when a key is configured.

## How a song is picked

1. **Local pass** — keyword themes, mood (valence/energy) and an abuse check. The support note never depends on the LLM.
2. **One LLM call** — reads the situation, including *perspective* (were you hurt, or did you hurt someone?), and proposes ~8 real songs with a confidence and a one-line reason. The same call writes the diagnosis and dosage and chooses a cocktail from the fixed list.
3. **Verification** — each candidate must be found on Spotify (title + original artist) and/or have lyrics on LRCLIB. Songs that can't be found are dropped, so invented songs never reach you.
4. **Pick one** — `final = 0.8 × LLM confidence + 0.2 × support` (lyric-theme overlap or agreement with the curated catalogue). The highest verified song wins.
5. **Fallback** — if the LLM isn't configured, fails, is rate-limited, nothing verifies, or the best pick is under 45%, the built-in matcher (71 hand-picked songs; theme + TF-IDF + mood) answers instead. The prescription is stamped **AI PICK** or **CATALOGUE**.

**Cocktails** come only from `data/cocktails.json` (26 classics, each with a 0% version), so the LLM can't invent a recipe; unknown names fall back to a local theme/mood match. The **0% ABV** toggle (`--no-alcohol`) always serves the alcohol-free version. If someone mentions abuse or threats, the result becomes a plain **care plan**: alcohol-free drink, no jokes, and a support note.

## Run locally

```bash
cp .env.example .env      # then fill in keys (never commit .env)
python3 -m songmatch.web  # http://localhost:8765  (?demo=1 / ?demo=serious show a sample without calling the AI)
python3 -m songmatch "I cheated on her and the guilt is eating me alive"
python3 -m songmatch --json --debug "..."   # every LLM candidate + its score
python3 -m songmatch --no-llm "..."         # catalogue only
python3 -m songmatch --offline "..."        # no network at all
python3 -m songmatch.llm check              # one real call to test the LLM key
python3 -m unittest -v                      # 58 offline tests
```

## Configuration (`.env` locally, Environment tab on Render)

**LLM** — set the key; the provider is detected from it (or set `LLM_PROVIDER` explicitly).

| Provider | Key | Default model | Notes |
|---|---|---|---|
| `gemini` (used live) | `GEMINI_API_KEY` from aistudio.google.com/apikey | `gemini-3.5-flash` | Free tier; Google may use and review free-tier text |
| `groq` | `GROQ_API_KEY` from console.groq.com/keys | `openai/gpt-oss-120b` | Free tier; doesn't train on your data |
| `openrouter` | `OPENROUTER_API_KEY` | set `LLM_MODEL` to a `:free` model | Low daily cap |
| `claude-cli` | none — log in with `claude` once | `sonnet` | Your Claude subscription via Claude Code; **local/personal use only**, refused on a public deployment |
| `ollama` | none | set `LLM_MODEL` | Fully local |
| `anthropic` / `openai` | `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | set `LLM_MODEL` | Paid |

`LLM_MODEL` overrides the default. Google retires Gemini models often: if picks start falling back to the catalogue, run `python3 -m songmatch.llm check` and switch to a newer Flash model.

**Spotify** (optional) — create an app at developer.spotify.com/dashboard (tick Web API; the form needs a redirect URI, use `http://127.0.0.1:8888/callback`) and set `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET`. Used only to verify songs exist and to show links, album art and the player. Spotify's 2026 rules for new apps require the app owner to have **Premium**. `python3 -m songmatch.spotify check` tests it.

**Public-server limits** (defaults shown):

| Variable | Default | Purpose |
|---|---|---|
| `ALLOWED_ORIGINS` | — | Sites allowed to call the API, e.g. `https://ashburncrash.github.io` (no path) |
| `RATE_PER_MIN` / `RATE_PER_DAY` | `6` / `60` | Requests per visitor |
| `LLM_DAILY_BUDGET` | `200` | AI picks per day across all visitors; after that, catalogue picks |

## Deployment

```
GitHub Pages (docs/)  ──fetch──▶  Render web service (python3 -m songmatch.web)  ──▶  Gemini, Spotify, LRCLIB
static page, no keys               holds the keys, rate-limits, picks the song
```

- **Front end:** GitHub Pages serves `main` → `/docs` at https://ashburncrash.github.io/dr-beat/. `docs/index.html` is generated from `songmatch/static/index.html`. Rebuild it after editing the page or changing the backend URL:
  ```bash
  python3 scripts/build_pages.py https://song-matcher-2c6a.onrender.com
  git add docs && git commit -m "Rebuild Pages" && git push
  ```
- **Backend:** Render → **New → Web Service** → connect this repo.
  - Start command: `python3 -m songmatch.web`
  - Environment: `GEMINI_API_KEY`, `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `ALLOWED_ORIGINS=https://ashburncrash.github.io`
  - Live at https://song-matcher-2c6a.onrender.com (it also serves the page itself). Pushing to `main` redeploys it.
  - `render.yaml` lists the same settings, for accounts where **New → Blueprint** is available.
- The free Render instance sleeps after 15 idle minutes; the first visit then takes about a minute (the page shows "WAKING THE DOCTOR…"). Its disk is wiped on restart, so lookup caches start empty.

## Privacy

- What you type goes to the server and to the LLM provider. On the free Gemini tier, Google may review it; the page asks visitors to leave out real names. Use `ollama` to keep everything on your machine.
- LLM answers are cached in memory only (keyed by a hash of the input). Nothing you type is written to disk or logged by the server.
- API keys stay on the server; the browser only receives the picked song and prescription.
- Lyrics are fetched only to derive theme tags; raw lyrics are never stored or shown.

## Files

| File | Role |
|---|---|
| `songmatch/llm.py` | LLM clients, prompt, verification, single-song picker |
| `songmatch/core.py` | Offline catalogue matcher (fallback + local analysis) |
| `songmatch/cocktails.py` | Diagnosis, dosage and cocktail pairing |
| `songmatch/web.py` | HTTP server: page + `/api/match`, CORS, rate limits |
| `songmatch/limits.py` | Per-visitor rate limiter and daily AI budget |
| `songmatch/spotify.py` | Spotify client (client credentials), track lookup + cache |
| `songmatch/lyrics.py` | LRCLIB lyrics → theme/mood tags |
| `songmatch/static/index.html` | The 8-bit clinic UI and prescription form |
| `docs/` | Generated GitHub Pages copy of the UI |
| `scripts/build_pages.py` | Builds `docs/` with the backend URL |
| `data/songs.json` | 71 curated songs; summaries in my own words, mood numbers hand-estimated |
| `data/cocktails.json` | 26 cocktails, each with a 0% version |
| `render.yaml` | Render settings |

Dr. Beat is a jukebox, not a doctor. Not medical advice. Drink responsibly and only if you're of legal age.
