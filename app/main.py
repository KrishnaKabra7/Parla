import os
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path

from anthropic import Anthropic
from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeSerializer

from app import db, srs, study
from app.langs import LANGS
from app.translit import to_latin

BASE_DIR = Path(__file__).parent
AUDIO_DIR = Path("data/audio")
LANG = "ru"

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-not-secure-change-me")
COOKIE_NAME = "slux_session"

_serializer = URLSafeSerializer(SECRET_KEY, salt="slux-session")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@asynccontextmanager
async def lifespan(app: FastAPI):
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    conn = db.connect()
    db.init_schema(conn)
    for lang, pack in LANGS.items():
        db.seed_words(conn, lang, pack["freq_file"])
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
        return int(_serializer.loads(tok))
    except (BadSignature, ValueError, TypeError):
        return None


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
    conn = db.connect()
    try:
        conn.execute("INSERT OR IGNORE INTO users(name) VALUES (?)", (name,))
        row = conn.execute("SELECT id FROM users WHERE name = ?", (name,)).fetchone()
    finally:
        conn.close()
    resp = RedirectResponse("/study", status_code=303)
    resp.set_cookie(
        COOKIE_NAME,
        _serializer.dumps(row["id"]),
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


async def _prepare_card(conn, user_id: int, client: Anthropic) -> dict:
    item = study.pick_next(conn, user_id, LANG)
    if item is None:
        return {"sentence_id": None}
    await study.ensure_sentences(conn, item.word_id, item.lemma, LANG, user_id, client)
    if item.is_new:
        study.ensure_user_word(conn, user_id, item.word_id)
    s = study.pick_sentence(conn, item.word_id)
    if s is None:
        return {"sentence_id": None}
    return {"sentence_id": s["id"], "audio": s["audio_path"]}


@app.get("/study")
async def study_page(
    request: Request,
    background_tasks: BackgroundTasks,
    user_id: int = Depends(require_user),
    client: Anthropic = Depends(get_anthropic),
):
    conn = db.connect()
    try:
        ctx = await _prepare_card(conn, user_id, client)
    finally:
        conn.close()
    background_tasks.add_task(_prefetch_next, user_id, client)
    return templates.TemplateResponse(request, "study.html", ctx)


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
            SELECT s.id, s.text, s.gloss_en, w.lemma FROM sentences s
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
        ctx = await _prepare_card(conn, user_id, client)
    finally:
        conn.close()
    background_tasks.add_task(_prefetch_next, user_id, client)
    return templates.TemplateResponse(request, "_card.html", ctx)
