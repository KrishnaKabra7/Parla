import os
import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = "data/slux.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS words (
    id INTEGER PRIMARY KEY,
    lang TEXT NOT NULL,
    lemma TEXT NOT NULL,
    freq_rank INTEGER NOT NULL,
    gloss_en TEXT,
    UNIQUE (lang, lemma)
);
CREATE INDEX IF NOT EXISTS ix_words_lang_rank ON words(lang, freq_rank);

CREATE TABLE IF NOT EXISTS user_words (
    user_id INTEGER NOT NULL REFERENCES users(id),
    word_id INTEGER NOT NULL REFERENCES words(id),
    status TEXT NOT NULL DEFAULT 'new',
    ef REAL NOT NULL DEFAULT 2.5,
    interval_days REAL NOT NULL DEFAULT 0,
    due_at TEXT,
    PRIMARY KEY (user_id, word_id)
);
CREATE INDEX IF NOT EXISTS ix_user_words_due ON user_words(user_id, due_at);

CREATE TABLE IF NOT EXISTS sentences (
    id INTEGER PRIMARY KEY,
    word_id INTEGER NOT NULL REFERENCES words(id),
    text TEXT NOT NULL,
    gloss_en TEXT NOT NULL,
    audio_path TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_sentences_word ON sentences(word_id);

CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    sentence_id INTEGER NOT NULL REFERENCES sentences(id),
    grade INTEGER NOT NULL,
    typed_answer TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_reviews_user_time ON reviews(user_id, created_at);
"""


def get_db_path() -> Path:
    return Path(os.environ.get("DB_PATH", DEFAULT_DB_PATH))


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def seed_words(conn: sqlite3.Connection, lang: str, freq_file: str | Path) -> int:
    """Insert lemmas from freq_file with freq_rank = line number.
    Skips entirely if the lang is already seeded. Returns count inserted."""
    if conn.execute("SELECT 1 FROM words WHERE lang = ? LIMIT 1", (lang,)).fetchone():
        return 0
    path = Path(freq_file)
    if not path.exists():
        return 0
    with path.open(encoding="utf-8") as f:
        rows = [
            (lang, lemma, rank)
            for rank, line in enumerate(f, start=1)
            if (lemma := line.strip())
        ]
    if not rows:
        return 0
    conn.execute("BEGIN")
    conn.executemany(
        "INSERT OR IGNORE INTO words(lang, lemma, freq_rank) VALUES (?, ?, ?)",
        rows,
    )
    conn.execute("COMMIT")
    return len(rows)
