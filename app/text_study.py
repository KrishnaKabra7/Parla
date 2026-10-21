import random
import re
from dataclasses import dataclass
from sqlite3 import Connection

from app.langs import LANGS

EN_TO_TL = "en_to_tl"
TL_TO_EN = "tl_to_en"
DIRECTIONS = (EN_TO_TL, TL_TO_EN)
EN_TO_TL_WEIGHT = 0.75


@dataclass
class TextCard:
    known_word_id: int
    lang: str
    target: str
    romanized: str
    english: str
    direction: str  # EN_TO_TL or TL_TO_EN


def pick_direction(rng: random.Random | None = None) -> str:
    r = rng or random
    return EN_TO_TL if r.random() < EN_TO_TL_WEIGHT else TL_TO_EN


def _card(row, direction: str) -> TextCard:
    return TextCard(
        known_word_id=row["id"],
        lang=row["lang"],
        target=row["target"],
        romanized=row["romanized"],
        english=row["english"],
        direction=direction,
    )


def _pick_in_direction(conn: Connection, user_id: int, lang: str, direction: str) -> TextCard | None:
    due = conn.execute(
        """
        SELECT kw.id, kw.lang, kw.target, kw.romanized, kw.english FROM user_known_words ukw
        JOIN known_words kw ON kw.id = ukw.known_word_id
        WHERE ukw.user_id = ? AND ukw.direction = ? AND kw.lang = ?
          AND ukw.due_at IS NOT NULL AND ukw.due_at <= datetime('now')
        ORDER BY ukw.due_at ASC LIMIT 1
        """,
        (user_id, direction, lang),
    ).fetchone()
    if due:
        return _card(due, direction)
    new = conn.execute(
        """
        SELECT kw.id, kw.lang, kw.target, kw.romanized, kw.english FROM known_words kw
        WHERE kw.lang = ? AND kw.id NOT IN (
            SELECT known_word_id FROM user_known_words
            WHERE user_id = ? AND direction = ?
        )
        ORDER BY kw.created_at DESC, kw.id DESC LIMIT 1
        """,
        (lang, user_id, direction),
    ).fetchone()
    if new:
        return _card(new, direction)
    return None


def pick_card(conn: Connection, user_id: int, lang: str, rng: random.Random | None = None) -> TextCard | None:
    direction = pick_direction(rng)
    card = _pick_in_direction(conn, user_id, lang, direction)
    if card is not None:
        return card
    other = TL_TO_EN if direction == EN_TO_TL else EN_TO_TL
    return _pick_in_direction(conn, user_id, lang, other)


def load_card(conn: Connection, known_word_id: int, direction: str) -> TextCard | None:
    row = conn.execute(
        "SELECT id, lang, target, romanized, english FROM known_words WHERE id = ?",
        (known_word_id,),
    ).fetchone()
    return _card(row, direction) if row else None


def ensure_user_known_word(conn: Connection, user_id: int, known_word_id: int, direction: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO user_known_words(user_id, known_word_id, direction) VALUES (?, ?, ?)",
        (user_id, known_word_id, direction),
    )


_GLOSS_STRIP = re.compile(r"\s*\([^)]*\)")
_PUNCT = ".,!?;:'\""


def _norm_english(s: str) -> str:
    # Drop parenthetical hints like "me (dat)" → "me", lowercase, strip punct edges.
    s = _GLOSS_STRIP.sub("", s)
    return s.strip().strip(_PUNCT).lower()


def _norm_target(lang: str, s: str) -> str:
    return LANGS[lang]["fold"](s.strip().strip(_PUNCT))


def _accepted_english(gloss_field: str) -> set[str]:
    return {_norm_english(g) for g in gloss_field.split(",") if _norm_english(g)}


def check_answer(card: TextCard, typed: str) -> bool:
    if not typed.strip():
        return False
    if card.direction == EN_TO_TL:
        return _norm_target(card.lang, typed) == _norm_target(card.lang, card.target)
    return _norm_english(typed) in _accepted_english(card.english)
