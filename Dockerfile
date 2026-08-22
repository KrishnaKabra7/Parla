FROM python:3.12-slim

# Litestream: streams SQLite WAL to S3-compatible storage (Cloudflare R2).
# Restores data/slux.db on boot so SRS state survives Render's ephemeral disk.
COPY --from=litestream/litestream:0.3.13 /usr/local/bin/litestream /usr/local/bin/litestream

WORKDIR /app

COPY pyproject.toml ./
COPY app ./app
COPY data/ru_freq.txt ./data/ru_freq.txt
COPY litestream.yml /etc/litestream.yml
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN pip install --no-cache-dir . \
    && mkdir -p data/audio \
    && sed -i 's/\r$//' /usr/local/bin/docker-entrypoint.sh \
    && chmod +x /usr/local/bin/docker-entrypoint.sh

# Render sets $PORT; default to 8000 for local docker run
ENV PORT=8000
EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
