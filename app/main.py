import os
import random
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path

from anthropic import Anthropic
from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeSerializer

from app import db, srs, study, text_study
from app.langs import LANGS
from app.translit import to_cyrillic, to_latin

BASE_DIR = Path(__file__).parent
AUDIO_DIR = Path("data/audio")
KNOWN_WORDS_FILE = Path("data/known_words.txt")
LANG = "ru"

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-not-secure-change-me")
COOKIE_NAME = "slux_session"
MODE_COOKIE = "slux_mode"
MODES = {"mixed", "voice", "text"}
DEFAULT_MODE = "mixed"

_serializer = URLSafeSerializer(SECRET_KEY, salt="slux-session")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@asynccontextmanager
async def lifespan(app: FastAPI):
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    conn = db.connect()
    db.init_schema(conn)
    for lang, pack in LANGS.items():
        db.seed_words(conn, lang, pack["freq_file"])
    db.seed_known_words(conn, KNOWN_WORDS_FILE, LANG)
    # Litestream restores the DB from R2 but audio files live on the ephemeral
    # disk. Drop sentence rows whose mp3 is gone so those words re-generate.
    orphans = [
        (r["id"],)
        for r in conn.execute("SELECT id, audio_path FROM sentences").fetchall()
        if not (AUDIO_DIR / r["audio_path"]).exists()
    ]
    if orphans:
        conn.executemany("DELETE FROM sentences WHERE id = ?", orphans)
    conn.close()
    yield


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.mount("/audio", StaticFiles(directory=AUDIO_DIR, check_dir=False), name="audio")


def get_anthropic() -> Anthropic:
    return Anthropic()


def current_user_id(request: Request) -> int | None:
    tok = request.cookies.get(COOKIE_NAME)
    if not tok:
        return None
    try:
        name = _serializer.loads(tok)
    except BadSignature:
        return None
    if not isinstance(name, str) or not name.strip():
        return None
    name = name.strip()
    # Cookie stores the name; resolve to user_id here, creating the row if
    # missing. Handles ephemeral-storage restarts on Render free tier gracefully
    # — a wiped DB just re-creates the user instead of throwing FK errors later.
    conn = db.connect()
    try:
        conn.execute("INSERT OR IGNORE INTO users(name) VALUES (?)", (name,))
        row = conn.execute("SELECT id FROM users WHERE name = ?", (name,)).fetchone()
        return row["id"] if row else None
    finally:
        conn.close()


def require_user(request: Request) -> int:
    uid = current_user_id(request)
    if uid is None:
        raise HTTPException(status_code=401, detail="login required")
    return uid


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def index(request: Request):
    dest = "/study" if current_user_id(request) is not None else "/login"
    return RedirectResponse(dest, status_code=303)


@app.get("/login")
def login_form(request: Request):
    if current_user_id(request) is not None:
        return RedirectResponse("/study", status_code=303)
    return templates.TemplateResponse(request, "login.html", {})


@app.post("/login")
def login_submit(name: str = Form(...)):
    name = name.strip()
    if not name:
        return RedirectResponse("/login", status_code=303)
    resp = RedirectResponse("/study", status_code=303)
    resp.set_cookie(
        COOKIE_NAME,
        _serializer.dumps(name),
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 365,
    )
    return resp


@app.post("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE_NAME)
    return resp


def _mode(request: Request) -> str:
    m = request.cookies.get(MODE_COOKIE, DEFAULT_MODE)
    return m if m in MODES else DEFAULT_MODE


async def _prefetch_next(user_id: int, client: Anthropic) -> None:
    """Best-effort: pre-generate sentences for the next likely word.
    Runs after the response is sent so it doesn't add to the user's wait."""
    conn = db.connect()
    try:
        item = study.pick_next(conn, user_id, LANG)
        if item is None:
            return
        await study.ensure_sentences(conn, item.word_id, item.lemma, LANG, user_id, client)
    except Exception:
        pass
    finally:
        conn.close()


def _text_card_ctx(card: text_study.TextCard) -> dict:
    prompt = card.english if card.direction == text_study.EN_TO_RU else card.cyrillic
    hint = "Russian" if card.direction == text_study.EN_TO_RU else "English"
    return {
        "card_type": "text",
        "known_word_id": card.known_word_id,
        "direction": card.direction,
        "prompt": prompt,
        "hint_lang": hint,
    }


async def _prepare_voice_card(conn, user_id: int, client: Anthropic) -> dict | None:
    item = study.pick_next(conn, user_id, LANG)
    if item is None:
        return None
    await study.ensure_sentences(conn, item.word_id, item.lemma, LANG, user_id, client)
    if item.is_new:
        study.ensure_user_word(conn, user_id, item.word_id)
    s = study.pick_sentence(conn, item.word_id)
    if s is None:
        return None
    return {"card_type": "voice", "sentence_id": s["id"], "audio": s["audio_path"]}


def _prepare_text_card(conn, user_id: int) -> dict | None:
    card = text_study.pick_card(conn, user_id)
    if card is None:
        return None
    return _text_card_ctx(card)


async def _prepare_card(conn, user_id: int, client: Anthropic, mode: str) -> dict:
    empty = {"card_type": None, "mode": mode}
    if mode == "voice":
        ctx = await _prepare_voice_card(conn, user_id, client)
        return {**(ctx or {}), **{"mode": mode, "card_type": ctx["card_type"] if ctx else None}}
    if mode == "text":
        ctx = _prepare_text_card(conn, user_id)
        return {**(ctx or {}), **{"mode": mode, "card_type": ctx["card_type"] if ctx else None}}
    # mixed: 50/50 coinflip; fall through to the other if empty.
    first, second = ("voice", "text") if random.random() < 0.5 else ("text", "voice")
    for kind in (first, second):
        ctx = (await _prepare_voice_card(conn, user_id, client)) if kind == "voice" else _prepare_text_card(conn, user_id)
        if ctx is not None:
            return {**ctx, "mode": mode}
    return empty


@app.get("/study")
async def study_page(
    request: Request,
    background_tasks: BackgroundTasks,
    user_id: int = Depends(require_user),
    client: Anthropic = Depends(get_anthropic),
):
    mode = _mode(request)
    conn = db.connect()
    try:
        ctx = await _prepare_card(conn, user_id, client, mode)
    finally:
        conn.close()
    if mode != "text":
        background_tasks.add_task(_prefetch_next, user_id, client)
    return templates.TemplateResponse(request, "study.html", ctx)


@app.post("/mode/{name}")
def set_mode(name: str, request: Request):
    if name not in MODES:
        raise HTTPException(400, "invalid mode")
    resp = RedirectResponse("/study", status_code=303)
    resp.set_cookie(
        MODE_COOKIE, name, httponly=True, samesite="lax",
        max_age=60 * 60 * 24 * 365,
    )
    return resp


@app.post("/review")
def review(
    request: Request,
    sentence_id: int = Form(...),
    typed: str = Form(""),
    user_id: int = Depends(require_user),
):
    conn = db.connect()
    try:
        row = conn.execute(
            """
            SELECT s.id, s.text, s.gloss_en, s.audio_path, w.lemma FROM sentences s
            JOIN words w ON w.id = s.word_id
            WHERE s.id = ?
            """,
            (sentence_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(404)
    tokens = study.diff_and_highlight(row["text"], typed, row["lemma"])
    return templates.TemplateResponse(
        request,
        "_reveal.html",
        {
            "sentence_id": sentence_id,
            "tokens": tokens,
            "gloss": row["gloss_en"],
            "latin": to_latin(row["text"]),
            "audio": row["audio_path"],
            "typed": typed,
        },
    )


@app.get("/words")
def words_page(
    request: Request,
    q: str = "",
    user_id: int = Depends(require_user),
):
    like = f"%{q.strip().lower()}%"
    conn = db.connect()
    try:
        rows = conn.execute(
            """
            SELECT w.id, w.lemma, w.gloss_en, uw.status, uw.ef, uw.due_at
            FROM user_words uw JOIN words w ON w.id = uw.word_id
            WHERE uw.user_id = ? AND w.lang = ? AND LOWER(w.lemma) LIKE ?
            ORDER BY uw.due_at IS NULL, uw.due_at ASC
            """,
            (user_id, LANG, like),
        ).fetchall()
    finally:
        conn.close()
    tpl = "_word_rows.html" if request.headers.get("HX-Request") else "words.html"
    return templates.TemplateResponse(request, tpl, {"rows": rows, "q": q})


@app.get("/words/{word_id}")
def word_detail(
    request: Request,
    word_id: int,
    user_id: int = Depends(require_user),
):
    conn = db.connect()
    try:
        word = conn.execute(
            "SELECT id, lemma, gloss_en FROM words WHERE id = ?", (word_id,)
        ).fetchone()
        sentences = conn.execute(
            "SELECT text, gloss_en, audio_path FROM sentences WHERE word_id = ? ORDER BY id",
            (word_id,),
        ).fetchall()
    finally:
        conn.close()
    if word is None:
        raise HTTPException(404)
    return templates.TemplateResponse(
        request, "word_detail.html", {"word": word, "sentences": sentences}
    )


@app.get("/stats")
def stats_page(
    request: Request,
    user_id: int = Depends(require_user),
):
    conn = db.connect()
    try:
        # Reviews per day, last 30 days (including today)
        rows = conn.execute(
            """
            SELECT DATE(created_at) AS d, COUNT(*) AS n
            FROM reviews
            WHERE user_id = ? AND created_at >= DATE('now', '-29 days')
            GROUP BY DATE(created_at)
            """,
            (user_id,),
        ).fetchall()
        per_day = {r["d"]: r["n"] for r in rows}
        known = conn.execute(
            "SELECT COUNT(*) AS n FROM user_words WHERE user_id = ? AND status = 'known'",
            (user_id,),
        ).fetchone()["n"]
        streak_rows = conn.execute(
            "SELECT DISTINCT DATE(created_at) AS d FROM reviews WHERE user_id = ? ORDER BY d DESC",
            (user_id,),
        ).fetchall()
    finally:
        conn.close()

    today = date.today()
    days = [(today - timedelta(days=29 - i)) for i in range(30)]
    counts = [per_day.get(d.isoformat(), 0) for d in days]

    streak_dates = {r["d"] for r in streak_rows}
    streak = 0
    d = today
    if d.isoformat() not in streak_dates:
        d = d - timedelta(days=1)
    while d.isoformat() in streak_dates:
        streak += 1
        d = d - timedelta(days=1)

    return templates.TemplateResponse(
        request,
        "stats.html",
        {"days": days, "counts": counts, "known": known, "streak": streak},
    )


@app.post("/grade")
async def grade(
    request: Request,
    background_tasks: BackgroundTasks,
    sentence_id: int = Form(...),
    grade: int = Form(...),
    typed: str = Form(""),
    user_id: int = Depends(require_user),
    client: Anthropic = Depends(get_anthropic),
):
    mode = _mode(request)
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT word_id FROM sentences WHERE id = ?", (sentence_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(404)
        word_id = row["word_id"]
        uw = conn.execute(
            "SELECT ef, interval_days FROM user_words WHERE user_id=? AND word_id=?",
            (user_id, word_id),
        ).fetchone()
        ef = uw["ef"] if uw else srs.DEFAULT_EF
        interval = uw["interval_days"] if uw else 0.0
        ef_new, interval_new, due_at, status_new = srs.review(ef, interval, grade)
        conn.execute(
            """
            INSERT INTO user_words(user_id, word_id, status, ef, interval_days, due_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, word_id) DO UPDATE SET
                status = excluded.status,
                ef = excluded.ef,
                interval_days = excluded.interval_days,
                due_at = excluded.due_at
            """,
            (user_id, word_id, status_new, ef_new, interval_new, due_at.isoformat()),
        )
        conn.execute(
            "INSERT INTO reviews(user_id, sentence_id, grade, typed_answer) VALUES (?, ?, ?, ?)",
            (user_id, sentence_id, grade, typed),
        )
        ctx = await _prepare_card(conn, user_id, client, mode)
    finally:
        conn.close()
    if mode != "text":
        background_tasks.add_task(_prefetch_next, user_id, client)
    return templates.TemplateResponse(request, "_card.html", ctx)


def _load_text_card(conn, known_word_id: int, direction: str) -> text_study.TextCard:
    if direction not in (text_study.EN_TO_RU, text_study.RU_TO_EN):
        raise HTTPException(400, "invalid direction")
    row = conn.execute(
        "SELECT id, cyrillic, latin, english FROM known_words WHERE id = ?",
        (known_word_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(404)
    return text_study.TextCard(
        known_word_id=row["id"],
        cyrillic=row["cyrillic"],
        latin=row["latin"],
        english=row["english"],
        direction=direction,
    )


@app.post("/review/text")
def review_text(
    request: Request,
    known_word_id: int = Form(...),
    direction: str = Form(...),
    typed: str = Form(""),
    user_id: int = Depends(require_user),
):
    conn = db.connect()
    try:
        card = _load_text_card(conn, known_word_id, direction)
    finally:
        conn.close()
    correct = text_study.check_answer(card, typed)
    return templates.TemplateResponse(
        request,
        "_text_reveal.html",
        {
            "known_word_id": known_word_id,
            "direction": direction,
            "typed": typed,
            "correct": correct,
            "cyrillic": card.cyrillic,
            "latin": card.latin,
            "english": card.english,
        },
    )


@app.post("/grade/text")
async def grade_text(
    request: Request,
    background_tasks: BackgroundTasks,
    known_word_id: int = Form(...),
    direction: str = Form(...),
    grade: int = Form(...),
    typed: str = Form(""),
    user_id: int = Depends(require_user),
    client: Anthropic = Depends(get_anthropic),
):
    if direction not in (text_study.EN_TO_RU, text_study.RU_TO_EN):
        raise HTTPException(400, "invalid direction")
    mode = _mode(request)
    conn = db.connect()
    try:
        exists = conn.execute(
            "SELECT 1 FROM known_words WHERE id = ?", (known_word_id,)
        ).fetchone()
        if exists is None:
            raise HTTPException(404)
        ukw = conn.execute(
            "SELECT ef, interval_days FROM user_known_words WHERE user_id=? AND known_word_id=? AND direction=?",
            (user_id, known_word_id, direction),
        ).fetchone()
        ef = ukw["ef"] if ukw else srs.DEFAULT_EF
        interval = ukw["interval_days"] if ukw else 0.0
        ef_new, interval_new, due_at, status_new = srs.review(ef, interval, grade)
        conn.execute(
            """
            INSERT INTO user_known_words(user_id, known_word_id, direction, status, ef, interval_days, due_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, known_word_id, direction) DO UPDATE SET
                status = excluded.status,
                ef = excluded.ef,
                interval_days = excluded.interval_days,
                due_at = excluded.due_at
            """,
            (user_id, known_word_id, direction, status_new, ef_new, interval_new, due_at.isoformat()),
        )
        conn.execute(
            "INSERT INTO reviews(user_id, sentence_id, grade, typed_answer) VALUES (?, NULL, ?, ?)",
            (user_id, grade, typed),
        )
        ctx = await _prepare_card(conn, user_id, client, mode)
    finally:
        conn.close()
    if mode != "text":
        background_tasks.add_task(_prefetch_next, user_id, client)
    return templates.TemplateResponse(request, "_card.html", ctx)


@app.get("/known/add")
def known_add_form(request: Request, user_id: int = Depends(require_user)):
    return templates.TemplateResponse(request, "known_add.html", {"message": None})


@app.post("/known/add")
def known_add_submit(
    request: Request,
    latin: str = Form(...),
    english: str = Form(...),
    cyrillic: str = Form(""),
    user_id: int = Depends(require_user),
):
    latin = latin.strip()
    english = english.strip()
    cyrillic = cyrillic.strip() or to_cyrillic(latin)
    if not latin or not english or not cyrillic:
        return templates.TemplateResponse(
            request, "known_add.html", {"message": "latin and english required"}
        )
    conn = db.connect()
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO known_words(lang, cyrillic, latin, english) VALUES (?, ?, ?, ?)",
            (LANG, cyrillic, latin, english),
        )
        msg = f"added {cyrillic}" if cur.rowcount else f"already exists: {cyrillic}"
    finally:
        conn.close()
    return templates.TemplateResponse(request, "known_add.html", {"message": msg})
