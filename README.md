# 🩺 Dr. Beat — your relationship, prescribed as a song (and a cocktail)

Describe your relationship issue or status; it returns **one** song that fits.

Pure Python standard library (3.9+). No installs. Works fully offline; gets smarter with an LLM and Spotify configured in `.env`.

## Run

```bash
cd ~/workspace/song-matcher
python3 -m songmatch "I cheated on her and the guilt is eating me alive"
python3 -m songmatch                  # interactive
python3 -m songmatch --json --debug "..."   # see every LLM candidate + its score
python3 -m songmatch --no-llm "..."   # catalogue only
python3 -m songmatch --offline "..."  # no network at all
python3 -m songmatch.web              # http://localhost:8765 (iPad: http://<mac-ip>:8765)
python3 -m unittest -v                # 37 offline tests
```

## How a song is picked

1. **Local pass** — keyword themes, mood (valence/energy), and an abuse check. The support note never depends on the LLM.
2. **One LLM call** — reads the situation (including *perspective*: were you hurt, or did you hurt someone?) and proposes ~8 real songs, each with a confidence and a one-line reason.
3. **Verification** — each candidate must be found on Spotify (title + original artist) and/or have lyrics on LRCLIB. Songs that can't be found are dropped, so invented songs never reach you.
4. **Pick one** — `final = 0.8 × LLM confidence + 0.2 × support` (lyric-theme overlap or agreement with the curated catalogue). The highest verified song is returned.
5. **Fallback** — if the LLM isn't configured, errors, nothing verifies, or the best pick is under 45%, the built-in catalogue matcher (70 hand-picked songs; theme + TF-IDF + mood) answers instead. The result says which (`source: llm | catalogue`).

## Setup (`cp .env.example .env`, then fill it in — never commit it or paste keys in chat)

**LLM via your Claude subscription (no API key)** — set `LLM_PROVIDER=claude-cli` (optionally `LLM_MODEL=sonnet`). Run `claude` once in a terminal and log in with your Claude account (choose the subscription login, not Console/API). The app then runs Claude Code headless (`claude -p`, no tools, empty temp dir, text sent via stdin). Usage counts against your plan's normal limits; when you hit them, the app falls back to the catalogue. `ANTHROPIC_API_KEY` is removed from the subprocess environment so it can't accidentally bill an API account. Personal use only — don't expose this as a public service on your subscription.

**Free LLM (recommended: Groq)** — get a key at https://console.groq.com/keys, then in `.env`: `LLM_PROVIDER=groq` and `GROQ_API_KEY=...`. Also supported: `gemini` (key from https://aistudio.google.com/apikey; free-tier prompts may be used by Google) and `openrouter` (set `LLM_MODEL` to a `:free` model). Verify with `python3 -m songmatch.llm check`.

**Prescription** — the same LLM call also returns a playful diagnosis, dosage, and a cocktail chosen from `data/cocktails.json` (26 classics, each with a 0% version). Unknown names fall back to a local theme/mood match. `--no-alcohol` (or the 0% ABV toggle) serves the mocktail; safety cases always get the mocktail and no jokes.

**LLM via API** — `LLM_PROVIDER` = `anthropic` | `openai` | `ollama`, plus `LLM_MODEL` (a model ID from your provider) and the matching API key. `LLM_BASE_URL` points the `openai` provider at any OpenAI-compatible service (e.g. OpenRouter).

**Spotify** — create an app at https://developer.spotify.com/dashboard (tick Web API; the form needs a redirect URI, use `http://127.0.0.1:8888/callback`). Add the Client ID/secret. Then:
```bash
python3 -m songmatch.spotify check   # one real call
python3 -m songmatch.spotify sync    # link the 70 catalogue songs
```
Spotify's Feb-2026 rules for new apps: the app owner needs **Premium**; no audio features, recommendations, popularity, or other users' playlists; search max 10 results. Here Spotify is used only to verify songs exist and to provide links, album art and the embedded player.

## Deploy (GitHub Pages front end + Render backend)

1. **Backend:** Render → New → Blueprint → pick this repo. `render.yaml` creates `dr-beat-clinic` (free plan) and asks for `GEMINI_API_KEY` and the Spotify ID/secret. Keys live only in Render.
2. **Front end:** `python3 scripts/build_pages.py https://song-matcher-2c6a.onrender.com`, commit `docs/`, push. Pages serves `main` → `/docs`.
3. `ALLOWED_ORIGINS` (CORS), `RATE_PER_MIN`/`RATE_PER_DAY` (per visitor) and `LLM_DAILY_BUDGET` (AI calls per day; after that, catalogue picks) are set in `render.yaml`.

The free Render instance sleeps after 15 idle minutes; the first visit takes about a minute to wake it. `claude-cli` is refused on a public deployment.

## Privacy

- With `anthropic`/`openai`, **your text is sent to that provider**. Use `ollama` to keep everything on your Mac.
- LLM answers are cached in memory only (keyed by a hash of the input); nothing you type is written to disk or logged by the web server.
- Lyrics are fetched only to derive theme tags; raw lyrics are never stored or shown.

## Files

| File | Role |
|---|---|
| `songmatch/llm.py` | LLM clients, prompt, verification, single-song picker |
| `songmatch/core.py` | Offline catalogue matcher (fallback + local analysis) |
| `songmatch/spotify.py` | Spotify client (client-credentials), track resolve + cache |
| `songmatch/lyrics.py` | LRCLIB lyrics → theme/mood tags |
| `songmatch/discover.py` | Older Spotify-search discovery (library only, not used by CLI/web) |
| `data/songs.json` | 70 curated songs; summaries in my own words, mood numbers hand-estimated |
