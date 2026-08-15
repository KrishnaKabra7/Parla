from datetime import datetime, timedelta, timezone

from app.srs import (
    DEFAULT_EF,
    KNOWN_THRESHOLD_DAYS,
    MIN_EF,
    Grade,
    next_interval,
    review,
    status_for,
    update_ef,
)


def test_grade_values():
    assert Grade.AGAIN == 0
    assert Grade.HARD == 3
    assert Grade.GOOD == 4
    assert Grade.EASY == 5


def test_update_ef_easy_raises_ef():
    assert update_ef(DEFAULT_EF, Grade.EASY) > DEFAULT_EF


def test_update_ef_good_keeps_ef_stable():
    assert update_ef(DEFAULT_EF, Grade.GOOD) == DEFAULT_EF


def test_update_ef_hard_lowers_ef():
    assert update_ef(DEFAULT_EF, Grade.HARD) < DEFAULT_EF


def test_update_ef_again_lowers_ef_the_most():
    ef_hard = update_ef(DEFAULT_EF, Grade.HARD)
    ef_again = update_ef(DEFAULT_EF, Grade.AGAIN)
    assert ef_again < ef_hard


def test_update_ef_floor():
    ef = DEFAULT_EF
    for _ in range(50):
        ef = update_ef(ef, Grade.AGAIN)
    assert ef == MIN_EF


def test_update_ef_monotonic_in_grade():
    grades = [Grade.AGAIN, Grade.HARD, Grade.GOOD, Grade.EASY]
    efs = [update_ef(DEFAULT_EF, g) for g in grades]
    assert efs == sorted(efs)


def test_next_interval_new_word_pass():
    assert next_interval(0, DEFAULT_EF, Grade.GOOD) == 1.0


def test_next_interval_first_review_pass():
    assert next_interval(1.0, DEFAULT_EF, Grade.GOOD) == 3.0


def test_next_interval_subsequent_review_multiplies_by_ef():
    assert next_interval(3.0, DEFAULT_EF, Grade.GOOD) == 3.0 * DEFAULT_EF


def test_next_interval_fail_resets_to_one_day():
    assert next_interval(30.0, DEFAULT_EF, Grade.AGAIN) == 1.0


def test_next_interval_hard_still_progresses():
    # Grade.HARD (3) is a pass in SM-2, so interval should progress
    assert next_interval(3.0, DEFAULT_EF, Grade.HARD) == 3.0 * DEFAULT_EF


def test_status_for_below_threshold_is_learning():
    assert status_for(KNOWN_THRESHOLD_DAYS - 0.1) == "learning"


def test_status_for_at_or_above_threshold_is_known():
    assert status_for(KNOWN_THRESHOLD_DAYS) == "known"
    assert status_for(KNOWN_THRESHOLD_DAYS + 100) == "known"


def test_review_returns_all_fields():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ef_new, interval_new, due_at, status = review(DEFAULT_EF, 0, Grade.GOOD, now=now)
    assert ef_new == DEFAULT_EF
    assert interval_new == 1.0
    assert due_at == now + timedelta(days=1)
    assert status == "learning"


def test_review_fail_schedules_tomorrow():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _, interval_new, due_at, status = review(DEFAULT_EF, 30.0, Grade.AGAIN, now=now)
    assert interval_new == 1.0
    assert due_at == now + timedelta(days=1)
    assert status == "learning"


def test_review_promotes_to_known_at_threshold():
    # Simulate many successful reviews until interval crosses 21d
    ef, interval = DEFAULT_EF, 0.0
    status = "new"
    for _ in range(10):
        ef, interval, _, status = review(ef, interval, Grade.GOOD)
        if status == "known":
            break
    assert status == "known"
    assert interval >= KNOWN_THRESHOLD_DAYS


def test_review_uses_utcnow_when_now_omitted():
    before = datetime.now(timezone.utc)
    _, _, due_at, _ = review(DEFAULT_EF, 0, Grade.GOOD)
    after = datetime.now(timezone.utc)
    # due_at should be ~1 day from "now"
    assert before + timedelta(days=1) - timedelta(seconds=5) <= due_at
    assert due_at <= after + timedelta(days=1) + timedelta(seconds=5)
