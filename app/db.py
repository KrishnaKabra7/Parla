import os
import sqlite3
from pathlib import Path

from app.langs import LANGS

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
    requeue_after INTEGER,
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
    sentence_id INTEGER REFERENCES sentences(id) ON DELETE SET NULL,
    grade INTEGER NOT NULL,
    typed_answer TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_reviews_user_time ON reviews(user_id, created_at);

CREATE TABLE IF NOT EXISTS known_words (
    id INTEGER PRIMARY KEY,
    lang TEXT NOT NULL DEFAULT 'ru',
    target TEXT NOT NULL,
    romanized TEXT NOT NULL,
    english TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (lang, target)
);

CREATE TABLE IF NOT EXISTS user_known_words (
    user_id INTEGER NOT NULL REFERENCES users(id),
    known_word_id INTEGER NOT NULL REFERENCES known_words(id) ON DELETE CASCADE,
    direction TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    ef REAL NOT NULL DEFAULT 2.5,
    interval_days REAL NOT NULL DEFAULT 0,
    due_at TEXT,
    requeue_after INTEGER,
    PRIMARY KEY (user_id, known_word_id, direction)
);
CREATE INDEX IF NOT EXISTS ix_user_known_words_due ON user_known_words(user_id, due_at);

CREATE TABLE IF NOT EXISTS user_prefs (
    user_id INTEGER PRIMARY KEY REFERENCES users(id),
    mode TEXT NOT NULL DEFAULT 'mixed'
);

CREATE TABLE IF NOT EXISTS tg_users (
    chat_id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id)
);
"""


def get_db_path() -> Path:
    return Path(os.environ.get("DB_PATH", DEFAULT_DB_PATH))


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL + busy_timeout so background prefetch writes don't collide with
    # foreground grade writes when they race on the same DB file.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _migrate_reviews_sentence_fk(conn)
    _in_transaction(conn, _migrate_known_words_columns)
    _in_transaction(conn, _create_user_langs)
    _in_transaction(conn, _add_requeue_column)


def _add_requeue_column(conn: sqlite3.Connection) -> None:
    for table in ("user_words", "user_known_words"):
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if "requeue_after" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN requeue_after INTEGER")


def _in_transaction(conn: sqlite3.Connection, step) -> None:
    # Each migration step commits as a unit so a crash mid-step can't leave
    # a half-renamed table that the "already migrated?" check then skips.
    conn.execute("BEGIN")
    try:
        step(conn)
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def _create_user_langs(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='user_langs'").fetchone():
        return
    conn.execute(
        """
        CREATE TABLE user_langs (
            user_id INTEGER NOT NULL REFERENCES users(id),
            lang TEXT NOT NULL,
            PRIMARY KEY (user_id, lang)
        )
        """
    )
    _backfill_user_langs(conn)


def _migrate_known_words_columns(conn: sqlite3.Connection) -> None:
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(known_words)")}
    if "cyrillic" not in cols:
        return
    conn.execute("ALTER TABLE known_words RENAME COLUMN cyrillic TO target")
    conn.execute("ALTER TABLE known_words RENAME COLUMN latin TO romanized")
    conn.execute("UPDATE user_known_words SET direction = 'en_to_tl' WHERE direction = 'en_to_ru'")
    conn.execute("UPDATE user_known_words SET direction = 'tl_to_en' WHERE direction = 'ru_to_en'")


def _backfill_user_langs(conn: sqlite3.Connection) -> None:
    # Users who studied before multi-language support only ever saw Russian;
    # keep them on it instead of interrupting them with the language picker.
    conn.execute(
        """
        INSERT OR IGNORE INTO user_langs(user_id, lang)
        SELECT user_id, 'ru' FROM user_words
        UNION SELECT user_id, 'ru' FROM user_known_words
        UNION SELECT user_id, 'ru' FROM reviews
        """
    )


def get_langs(conn: sqlite3.Connection, user_id: int) -> list[str]:
    rows = conn.execute("SELECT lang FROM user_langs WHERE user_id = ?", (user_id,)).fetchall()
    chosen = {r["lang"] for r in rows}
    return [code for code in LANGS if code in chosen]


def set_langs(conn: sqlite3.Connection, user_id: int, langs: list[str]) -> None:
    if not langs or any(code not in LANGS for code in langs):
        raise ValueError(f"invalid languages: {langs!r}")
    conn.execute("BEGIN")
    conn.execute("DELETE FROM user_langs WHERE user_id = ?", (user_id,))
    conn.executemany(
        "INSERT OR IGNORE INTO user_langs(user_id, lang) VALUES (?, ?)",
        [(user_id, code) for code in langs],
    )
    conn.execute("COMMIT")


def _migrate_reviews_sentence_fk(conn: sqlite3.Connection) -> None:
    # Older DBs had reviews.sentence_id as NOT NULL with a plain FK, which
    # blocked pruning orphan sentences after audio was wiped. Rebuild to
    # nullable + ON DELETE SET NULL so pruning preserves review history.
    cols = {r["name"]: r for r in conn.execute("PRAGMA table_info(reviews)")}
    if "sentence_id" not in cols or cols["sentence_id"]["notnull"] == 0:
        return
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(
        """
        BEGIN;
        CREATE TABLE reviews_new (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            sentence_id INTEGER REFERENCES sentences(id) ON DELETE SET NULL,
            grade INTEGER NOT NULL,
            typed_answer TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        INSERT INTO reviews_new (id, user_id, sentence_id, grade, typed_answer, created_at)
            SELECT id, user_id, sentence_id, grade, typed_answer, created_at FROM reviews;
        DROP TABLE reviews;
        ALTER TABLE reviews_new RENAME TO reviews;
        CREATE INDEX IF NOT EXISTS ix_reviews_user_time ON reviews(user_id, created_at);
        COMMIT;
        """
    )
    conn.execute("PRAGMA foreign_keys = ON")


def seed_known_words(conn: sqlite3.Connection, seed_file: str | Path, lang: str = "ru") -> int:
    """Load pipe-delimited target|romanized|english triples (romanized may be
    empty for Latin-script languages). INSERT OR IGNORE so
    re-runs are idempotent and file edits add new rows without touching
    existing (possibly UI-added) entries. Returns count considered (not
    necessarily inserted)."""
    path = Path(seed_file)
    if not path.exists():
        return 0
    rows: list[tuple[str, str, str, str]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("|")
            if len(parts) != 3:
                continue
            target, rom, eng = (p.strip() for p in parts)
            if not target or not eng:
                continue
            rows.append((lang, target, rom, eng))
    if not rows:
        return 0
    conn.execute("BEGIN")
    conn.executemany(
        "INSERT OR IGNORE INTO known_words(lang, target, romanized, english) VALUES (?, ?, ?, ?)",
        rows,
    )
    conn.execute("COMMIT")
    return len(rows)


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
