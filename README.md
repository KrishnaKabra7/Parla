# slux

Audio-first vocabulary trainer. You hear a short Russian sentence, type what
you heard, then see the text and gloss. SM-2 schedules reviews.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
```

Set two env vars in the terminal you'll run the server from:

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."   # from https://console.anthropic.com
$env:SECRET_KEY = "any-long-random-string"
```

## Run

```powershell
.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Then open http://127.0.0.1:8000, type a name, and start dictating.

The first card for each new word triggers one Anthropic call + three
edge-tts renders (~5s pause). Every review after that hits disk only.

## Test

```powershell
.venv\Scripts\pytest
```

## Stack

FastAPI · Jinja · htmx · stdlib sqlite3 · edge-tts · Anthropic SDK · SM-2.
No JS framework, no build step, one process, one SQLite file at `data/slux.db`.

## Deploying

### Render (free tier, Docker)

1. Push this repo to GitHub.
2. On [render.com](https://render.com) → **New +** → **Web Service** → connect the repo.
3. Runtime: **Docker** (Render picks up the `Dockerfile` automatically).
   Instance type: **Free**. Region: pick nearest to you.
4. Under **Environment**, add:
   - `ANTHROPIC_API_KEY` = your `sk-ant-...` key
   - `SECRET_KEY` = any long random string
5. Click **Create Web Service**. First build takes ~3-4 min; you'll get a
   `https://<name>.onrender.com` URL.

**Known trade-offs on Render's free tier:**
- **Cold start ~30-60s** after 15 min of inactivity. First click after a pause
  is slow; after that it's snappy until the next 15-min gap.
- **No persistent disk.** `data/slux.db` and the mp3 cache live inside the
  container, so they get wiped on every redeploy (and on some platform-side
  restarts). Your SRS state won't survive long-term — fine for testing the
  deploy path from your phone, but plan to move storage off-container before
  relying on the app day-to-day. See "Planned migration" below.

### Self-hosting (fallback)

Run uvicorn on any machine you leave on, expose it with a
[Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)
(`cloudflared tunnel --url http://localhost:8000`). Free, HTTPS, no router config.
`data/slux.db` and `data/audio/` persist across restarts — back up `slux.db`
occasionally.

### Planned migration (persistent storage on Render)

To keep data across redeploys on the free tier, storage moves off-container:

- **DB → [Turso](https://turso.tech)** via the `libsql` package. `libsql`
  returns plain tuples (not sqlite3.Row), so `app/db.py` needs a dict-cursor
  wrapper, plus explicit `.commit()` calls throughout (no autocommit).
- **mp3 cache → Cloudflare R2** (S3-compatible, free 10GB / 1M reads/mo) via
  `boto3`. `app/tts.py` uploads instead of writing locally; template audio
  `src` points at R2 URLs.

Not tiny — closer to a few hundred lines including test fixture updates.

## Notes

- Type Russian *or* Latin — Latin input is transliterated to Cyrillic before
  the diff (`privet` matches `привет`).
- Frequency list is the top 3000 from
  [hermitdave/FrequencyWords](https://github.com/hermitdave/FrequencyWords)
  (OpenSubtitles 2018, MIT).
- No passwords. Toy for friends — don't type anything sensitive.
