import asyncio
from dataclasses import dataclass
from sqlite3 import Connection, Row

from anthropic import Anthropic

from app import gen, tts
from app.langs import LANGS
from app.translit import to_cyrillic

# Per-word locks so a background prefetch and a foreground gen for the same
# word serialize instead of double-firing Anthropic + edge-tts.
_sentence_locks: dict[int, asyncio.Lock] = {}


@dataclass
class NextItem:
    word_id: int
    lemma: str
    is_new: bool


def pick_next(conn: Connection, user_id: int, lang: str) -> NextItem | None:
    row = conn.execute(
        """
        SELECT w.id AS id, w.lemma AS lemma FROM user_words uw
        JOIN words w ON w.id = uw.word_id
        WHERE uw.user_id = ? AND w.lang = ?
          AND uw.due_at IS NOT NULL AND uw.due_at <= datetime('now')
        ORDER BY uw.due_at ASC LIMIT 1
        """,
        (user_id, lang),
    ).fetchone()
    if row:
        return NextItem(word_id=row["id"], lemma=row["lemma"], is_new=False)
    row = conn.execute(
        """
        SELECT w.id AS id, w.lemma AS lemma FROM words w
        WHERE w.lang = ?
          AND w.id NOT IN (SELECT word_id FROM user_words WHERE user_id = ?)
        ORDER BY w.freq_rank ASC LIMIT 1
        """,
        (lang, user_id),
    ).fetchone()
    if row:
        return NextItem(word_id=row["id"], lemma=row["lemma"], is_new=True)
    return None


def known_lemmas(conn: Connection, user_id: int, lang: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT w.lemma FROM user_words uw
        JOIN words w ON w.id = uw.word_id
        WHERE uw.user_id = ? AND w.lang = ?
        """,
        (user_id, lang),
    ).fetchall()
    return [r["lemma"] for r in rows]


async def ensure_sentences(
    conn: Connection,
    word_id: int,
    lemma: str,
    lang: str,
    user_id: int,
    client: Anthropic,
) -> None:
    lock = _sentence_locks.setdefault(word_id, asyncio.Lock())
    async with lock:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM sentences WHERE word_id = ?", (word_id,)
        ).fetchone()
        if row["n"] > 0:
            return
        known = known_lemmas(conn, user_id, lang)
        result = gen.generate(lang, lemma, known, client=client)
        voice = LANGS[lang]["voice"]
        for s in result.sentences:
            audio = await tts.synthesize(s.text, voice)
            conn.execute(
                "INSERT INTO sentences(word_id, text, gloss_en, audio_path) VALUES (?, ?, ?, ?)",
                (word_id, s.text, s.gloss_en, audio.name),
            )
        if result.gloss_en:
            conn.execute("UPDATE words SET gloss_en = ? WHERE id = ?", (result.gloss_en, word_id))


def ensure_user_word(conn: Connection, user_id: int, word_id: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO user_words(user_id, word_id) VALUES (?, ?)",
        (user_id, word_id),
    )


def pick_sentence(conn: Connection, word_id: int) -> Row | None:
    return conn.execute(
        "SELECT id, text, gloss_en, audio_path FROM sentences WHERE word_id = ? ORDER BY RANDOM() LIMIT 1",
        (word_id,),
    ).fetchone()


_STRIP = ".,!?;:'\"()[]«»…—-"


def _norm(tok: str) -> str:
    return tok.strip(_STRIP).lower()


@dataclass
class Token:
    text: str
    matched: bool
    is_target: bool


def diff_and_highlight(truth: str, typed: str, target_lemma: str) -> list[Token]:
    typed_set = {_norm(t) for t in to_cyrillic(typed).split() if _norm(t)}
    stem = target_lemma.lower()[:4]
    return [
        Token(text=tok, matched=_norm(tok) in typed_set, is_target=_norm(tok).startswith(stem))
        for tok in truth.split()
    ]
