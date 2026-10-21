import sqlite3

import pytest

from app import db

OLD_SCHEMA = """
CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE known_words (
    id INTEGER PRIMARY KEY,
    lang TEXT NOT NULL DEFAULT 'ru',
    cyrillic TEXT NOT NULL,
    latin TEXT NOT NULL,
    english TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (lang, cyrillic)
);
CREATE TABLE user_known_words (
    user_id INTEGER NOT NULL REFERENCES users(id),
    known_word_id INTEGER NOT NULL REFERENCES known_words(id) ON DELETE CASCADE,
    direction TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    ef REAL NOT NULL DEFAULT 2.5,
    interval_days REAL NOT NULL DEFAULT 0,
    due_at TEXT,
    PRIMARY KEY (user_id, known_word_id, direction)
);
INSERT INTO users(id, name) VALUES (1, 'old'), (2, 'fresh');
INSERT INTO known_words(id, cyrillic, latin, english) VALUES (1, 'привет', 'privet', 'hi');
INSERT INTO user_known_words(user_id, known_word_id, direction, ef, interval_days, due_at)
    VALUES (1, 1, 'en_to_ru', 2.7, 6, '2030-01-01'), (1, 1, 'ru_to_en', 2.1, 1, '2030-01-02');
"""


@pytest.fixture()
def old_db(tmp_path):
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.executescript(OLD_SCHEMA)
    raw.commit()
    raw.close()
    conn = db.connect(path)
    yield conn
    conn.close()


def test_migration_renames_columns_and_keeps_rows(old_db):
    db.init_schema(old_db)
    row = old_db.execute("SELECT target, romanized, english FROM known_words").fetchone()
    assert (row["target"], row["romanized"], row["english"]) == ("привет", "privet", "hi")


def test_migration_renames_directions_and_keeps_srs(old_db):
    db.init_schema(old_db)
    rows = old_db.execute(
        "SELECT direction, ef, interval_days, due_at FROM user_known_words ORDER BY direction"
    ).fetchall()
    assert [tuple(r) for r in rows] == [
        ("en_to_tl", 2.7, 6, "2030-01-01"),
        ("tl_to_en", 2.1, 1, "2030-01-02"),
    ]


def test_migration_backfills_russian_for_users_with_history(old_db):
    db.init_schema(old_db)
    assert db.get_langs(old_db, 1) == ["ru"]
    assert db.get_langs(old_db, 2) == []


def test_migration_is_idempotent(old_db):
    db.init_schema(old_db)
    db.set_langs(old_db, 1, ["de"])
    db.init_schema(old_db)
    assert db.get_langs(old_db, 1) == ["de"]
    assert old_db.execute("SELECT COUNT(*) FROM user_known_words").fetchone()[0] == 2


def test_set_langs_roundtrip_in_pack_order(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    conn.execute("INSERT INTO users(name) VALUES ('a')")
    db.set_langs(conn, 1, ["de", "ru"])
    assert db.get_langs(conn, 1) == ["ru", "de"]
    db.set_langs(conn, 1, ["de"])
    assert db.get_langs(conn, 1) == ["de"]


def test_set_langs_rejects_empty_and_unknown(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    conn.execute("INSERT INTO users(name) VALUES ('a')")
    with pytest.raises(ValueError):
        db.set_langs(conn, 1, [])
    with pytest.raises(ValueError):
        db.set_langs(conn, 1, ["fr"])


def test_interrupted_column_rename_is_rolled_back(old_db, monkeypatch):
    real = db._backfill_user_langs

    def boom(conn):
        raise RuntimeError("killed mid-migration")

    monkeypatch.setattr(db, "_backfill_user_langs", boom)
    with pytest.raises(RuntimeError):
        db.init_schema(old_db)
    monkeypatch.setattr(db, "_backfill_user_langs", real)
    db.init_schema(old_db)
    assert db.get_langs(old_db, 1) == ["ru"]
    row = old_db.execute("SELECT target, romanized FROM known_words").fetchone()
    assert (row["target"], row["romanized"]) == ("привет", "privet")
