import random
from datetime import datetime, timedelta, timezone
from enum import IntEnum

from app import db


class Grade(IntEnum):
    AGAIN = 0
    HARD = 3
    GOOD = 4
    EASY = 5


DEFAULT_EF = 2.5
MIN_EF = 1.3
KNOWN_THRESHOLD_DAYS = 3.0
HARD_EF_PENALTY = 0.15
REQUEUE_MIN, REQUEUE_MAX = 5, 10


def update_ef(ef: float, grade: int) -> float:
    if grade == Grade.HARD:
        return max(MIN_EF, ef - HARD_EF_PENALTY)
    ef_new = ef + 0.5 - (5 - grade) * (0.08 + (5 - grade) * 0.02)
    return max(MIN_EF, ef_new)


def next_interval(interval_days: float, ef_new: float, grade: int) -> float:
    if grade < 3:
        return 0.0
    if interval_days < 1:
        return 1.0
    if interval_days < 3:
        return 3.0
    return interval_days * ef_new


def status_for(interval_days: float) -> str:
    if interval_days >= KNOWN_THRESHOLD_DAYS:
        return "known"
    return "learning"


def review(
    ef: float,
    interval_days: float,
    grade: int,
    now: datetime | None = None,
) -> tuple[float, float, datetime, str]:
    """Apply an SM-2 review. Returns (new_ef, new_interval_days, new_due_at, new_status)."""
    now = now or datetime.now(timezone.utc)
    ef_new = update_ef(ef, grade)
    interval_new = next_interval(interval_days, ef_new, grade)
    due_at = now + timedelta(days=interval_new)
    return ef_new, interval_new, due_at, status_for(interval_new)


def review_count(conn, user_id: int) -> int:
    return conn.execute("SELECT COUNT(*) FROM reviews WHERE user_id = ?", (user_id,)).fetchone()[0]


def record_grade(conn, user_id: int, grade: int, table: str, key: dict, sentence_id=None, typed=None, rng=None) -> None:
    """Grade one card in `table` (user_words or user_known_words, identified by
    `key`) and log the review. "Again" holds the card back for 5-10 more
    graded cards, so it returns in the same sitting."""
    cols = ", ".join(key)
    where = " AND ".join(f"{k} = ?" for k in key)
    with db.transaction(conn):
        row = conn.execute(
            f"SELECT ef, interval_days FROM {table} WHERE user_id = ? AND {where}", (user_id, *key.values())
        ).fetchone()
        ef, interval = (row["ef"], row["interval_days"]) if row else (DEFAULT_EF, 0.0)
        ef, interval, due_at, status = review(ef, interval, grade)
        conn.execute(
            "INSERT INTO reviews(user_id, sentence_id, grade, typed_answer) VALUES (?, ?, ?, ?)",
            (user_id, sentence_id, grade, typed),
        )
        requeue_after = None
        if grade < Grade.HARD:
            requeue_after = review_count(conn, user_id) + (rng or random).randint(REQUEUE_MIN, REQUEUE_MAX)
        conn.execute(
            f"""
            INSERT INTO {table}(user_id, {cols}, status, ef, interval_days, due_at, requeue_after)
            VALUES (?, {", ".join("?" * len(key))}, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, {cols}) DO UPDATE SET
                status = excluded.status,
                ef = excluded.ef,
                interval_days = excluded.interval_days,
                due_at = excluded.due_at,
                requeue_after = excluded.requeue_after
            """,
            (user_id, *key.values(), status, ef, interval, due_at.isoformat(), requeue_after),
        )
