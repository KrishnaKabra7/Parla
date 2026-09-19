import random
import re
from dataclasses import dataclass
from sqlite3 import Connection

from app.translit import to_cyrillic

EN_TO_RU = "en_to_ru"
RU_TO_EN = "ru_to_en"
EN_TO_RU_WEIGHT = 0.75


@dataclass
class TextCard:
    known_word_id: int
    cyrillic: str
    latin: str
    english: str
    direction: str  # EN_TO_RU or RU_TO_EN


def pick_direction(rng: random.Random | None = None) -> str:
    r = rng or random
    return EN_TO_RU if r.random() < EN_TO_RU_WEIGHT else RU_TO_EN


def _pick_in_direction(conn: Connection, user_id: int, direction: str) -> TextCard | None:
    due = conn.execute(
        """
        SELECT kw.id, kw.cyrillic, kw.latin, kw.english FROM user_known_words ukw
        JOIN known_words kw ON kw.id = ukw.known_word_id
        WHERE ukw.user_id = ? AND ukw.direction = ?
          AND ukw.due_at IS NOT NULL AND ukw.due_at <= datetime('now')
        ORDER BY ukw.due_at ASC LIMIT 1
        """,
        (user_id, direction),
    ).fetchone()
    if due:
        return TextCard(
            known_word_id=due["id"],
            cyrillic=due["cyrillic"],
            latin=due["latin"],
            english=due["english"],
            direction=direction,
        )
    new = conn.execute(
        """
        SELECT kw.id, kw.cyrillic, kw.latin, kw.english FROM known_words kw
        WHERE kw.id NOT IN (
            SELECT known_word_id FROM user_known_words
            WHERE user_id = ? AND direction = ?
        )
        ORDER BY kw.id ASC LIMIT 1
        """,
        (user_id, direction),
    ).fetchone()
    if new:
        return TextCard(
            known_word_id=new["id"],
            cyrillic=new["cyrillic"],
            latin=new["latin"],
            english=new["english"],
            direction=direction,
        )
    return None


def pick_card(conn: Connection, user_id: int, rng: random.Random | None = None) -> TextCard | None:
    direction = pick_direction(rng)
    card = _pick_in_direction(conn, user_id, direction)
    if card is not None:
        return card
    other = RU_TO_EN if direction == EN_TO_RU else EN_TO_RU
    return _pick_in_direction(conn, user_id, other)


def ensure_user_known_word(conn: Connection, user_id: int, known_word_id: int, direction: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO user_known_words(user_id, known_word_id, direction) VALUES (?, ?, ?)",
        (user_id, known_word_id, direction),
    )


_GLOSS_STRIP = re.compile(r"\s*\([^)]*\)")


def _norm_english(s: str) -> str:
    # Drop parenthetical hints like "me (dat)" → "me", lowercase, strip punct edges.
    s = _GLOSS_STRIP.sub("", s)
    return s.strip().strip(".,!?;:'\"").lower()


def _norm_russian(s: str) -> str:
    return to_cyrillic(s).strip().strip(".,!?;:'\"").lower()


def _accepted_english(gloss_field: str) -> set[str]:
    return {_norm_english(g) for g in gloss_field.split(",") if _norm_english(g)}


def check_answer(card: TextCard, typed: str) -> bool:
    if not typed.strip():
        return False
    if card.direction == EN_TO_RU:
        return _norm_russian(typed) == card.cyrillic.lower()
    return _norm_english(typed) in _accepted_english(card.english)
