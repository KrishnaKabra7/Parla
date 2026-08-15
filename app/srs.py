from datetime import datetime, timedelta, timezone
from enum import IntEnum


class Grade(IntEnum):
    AGAIN = 0
    HARD = 3
    GOOD = 4
    EASY = 5


DEFAULT_EF = 2.5
MIN_EF = 1.3
KNOWN_THRESHOLD_DAYS = 21.0


def update_ef(ef: float, grade: int) -> float:
    ef_new = ef + 0.1 - (5 - grade) * (0.08 + (5 - grade) * 0.02)
    return max(MIN_EF, ef_new)


def next_interval(interval_days: float, ef_new: float, grade: int) -> float:
    if grade < 3:
        return 1.0
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
