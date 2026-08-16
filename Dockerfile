FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY app ./app
COPY data/ru_freq.txt ./data/ru_freq.txt

RUN pip install --no-cache-dir . \
    && mkdir -p data/audio

# Render sets $PORT; default to 8000 for local docker run
ENV PORT=8000
EXPOSE 8000

CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
