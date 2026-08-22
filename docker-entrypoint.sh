#!/bin/sh
set -e

mkdir -p /app/data

if [ -n "${R2_BUCKET:-}" ]; then
  # Restore the DB from R2 if a replica exists AND the local file is absent.
  # -if-replica-exists: no-op on first-ever boot (empty bucket).
  # -if-db-not-exists:  don't overwrite a DB that's already on disk (dev/local).
  litestream restore -if-replica-exists -if-db-not-exists -config /etc/litestream.yml /app/data/slux.db

  # replicate runs in the foreground and supervises the -exec command; when
  # uvicorn exits, litestream flushes and exits too.
  exec litestream replicate -config /etc/litestream.yml -exec "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"
else
  # No R2 configured (local docker run) — just run uvicorn.
  exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT}"
fi
