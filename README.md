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
- **Ephemeral disk.** The container's disk gets wiped on redeploy and some
  platform-side restarts. `data/slux.db` is persisted via Litestream → R2
  (see below); the mp3 cache is not — sentences whose audio is missing get
  pruned on boot and regenerated on next study.

### Persist the DB with Litestream + Cloudflare R2 (free)

The Dockerfile bundles [Litestream](https://litestream.io) as a sidecar that
streams the SQLite WAL to an S3-compatible bucket and restores it on boot.
Cloudflare R2's free tier (10 GB storage, 1M reads/mo) covers this app with
huge headroom.

One-time setup:

1. Create a Cloudflare account, then **R2** → **Create bucket** (any name,
   e.g. `slux`).
2. **R2** → **Manage R2 API Tokens** → **Create API token**. Permissions:
   *Object Read & Write*, scope to the bucket you just created. Copy the
   **Access Key ID**, **Secret Access Key**, and the **S3 API endpoint URL**
   shown at the bottom (looks like `https://<account>.r2.cloudflarestorage.com`).
3. In Render → your service → **Environment**, add four vars:
   - `R2_ENDPOINT` = the S3 API endpoint URL from step 2
   - `R2_BUCKET` = your bucket name
   - `R2_ACCESS_KEY_ID` = from step 2
   - `R2_SECRET_ACCESS_KEY` = from step 2
4. Redeploy. On first boot the bucket is empty so Litestream no-ops; every
   subsequent boot restores `data/slux.db` from R2 before uvicorn starts.

If none of the `R2_*` vars are set, the entrypoint skips Litestream and just
runs uvicorn (local `docker run` works unchanged).

### Self-hosting (fallback)

Run uvicorn on any machine you leave on, expose it with a
[Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)
(`cloudflared tunnel --url http://localhost:8000`). Free, HTTPS, no router config.
`data/slux.db` and `data/audio/` persist across restarts — back up `slux.db`
occasionally.

### Optional: persist the mp3 cache too

Currently only the DB is persisted (via Litestream + R2 above). The mp3 cache
still lives on ephemeral disk, so orphan sentences get pruned on boot and
re-cost one Anthropic call + three edge-tts renders per revisited word. To
also persist audio: upload to R2 from `app/tts.py` via `boto3` and point
template `<audio src>` at R2 URLs.

## Notes

- Type Russian *or* Latin — Latin input is transliterated to Cyrillic before
  the diff (`privet` matches `привет`).
- Frequency list is the top 3000 from
  [hermitdave/FrequencyWords](https://github.com/hermitdave/FrequencyWords)
  (OpenSubtitles 2018, MIT).
- No passwords. Toy for friends — don't type anything sensitive.
