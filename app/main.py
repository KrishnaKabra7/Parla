import os
from contextlib import asynccontextmanager
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from anthropic import Anthropic
from fastapi import BackgroundTasks, Depends, FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeSerializer

from app import bot as tg, db, srs, study, text_study
from app.langs import LANGS, candidates

BASE_DIR = Path(__file__).parent
AUDIO_DIR = Path("data/audio")

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-not-secure-change-me")
COOKIE_NAME = "slux_session"
MODE_COOKIE = "slux_mode"
MODES = {"mixed", "voice", "text"}
DEFAULT_MODE = "mixed"

_serializer = URLSafeSerializer(SECRET_KEY, salt="slux-session")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

TG_WEBHOOK_PATH = "/tg/webhook"
_tg_bot: object | None = None
_tg_dp: object | None = None
_tg_webhook_secret: str | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _tg_bot, _tg_dp, _tg_webhook_secret
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    conn = db.connect()
    db.init_schema(conn)
    for lang, pack in LANGS.items():
        db.seed_words(conn, lang, pack["freq_file"])
        db.seed_known_words(conn, pack["known_file"], lang)
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

    _tg_bot = tg.build_bot()
    if _tg_bot is not None:
        _tg_dp = tg.build_dispatcher()
        _tg_webhook_secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET")
        public = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("TG_WEBHOOK_URL")
        if public:
            await _tg_bot.set_webhook(
                url=public.rstrip("/") + TG_WEBHOOK_PATH,
                secret_token=_tg_webhook_secret,
                drop_pending_updates=True,
            )
    yield
    if _tg_bot is not None:
        await _tg_bot.session.close()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.mount("/audio", StaticFiles(directory=AUDIO_DIR, check_dir=False), name="audio")


@lru_cache(maxsize=1)
def get_anthropic() -> Anthropic:
    # ~250ms constructor: cache so per-request Depends doesn't rebuild each time.
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


@app.post(TG_WEBHOOK_PATH)
async def tg_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    if _tg_bot is None or _tg_dp is None:
        raise HTTPException(503, "bot not configured")
    if _tg_webhook_secret and x_telegram_bot_api_secret_token != _tg_webhook_secret:
        raise HTTPException(401, "bad secret")
    from aiogram.types import Update

    payload = await request.json()
    update = Update.model_validate(payload, context={"bot": _tg_bot})
    await _tg_dp.feed_webhook_update(_tg_bot, update)
    return JSONResponse({"ok": True})


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


def _user_langs(user_id: int) -> list[str]:
    conn = db.connect()
    try:
        return db.get_langs(conn, user_id)
    finally:
        conn.close()


def _lang_ctx(lang: str) -> dict:
    return {"lang": lang, "lang_name": LANGS[lang]["name"], "lang_flag": LANGS[lang]["flag"]}


@app.get("/langs")
def langs_form(request: Request, user_id: int = Depends(require_user)):
    return templates.TemplateResponse(
        request, "langs.html", {"langs": LANGS, "chosen": _user_langs(user_id), "message": None}
    )


@app.post("/langs")
def langs_submit(
    request: Request,
    langs: list[str] = Form(default=[]),
    user_id: int = Depends(require_user),
):
    conn = db.connect()
    try:
        db.set_langs(conn, user_id, langs)
    except ValueError:
        return templates.TemplateResponse(
            request,
            "langs.html",
            {"langs": LANGS, "chosen": db.get_langs(conn, user_id), "message": "pick at least one language"},
            status_code=400,
        )
    finally:
        conn.close()
    return RedirectResponse("/study", status_code=303)


def _mode(request: Request) -> str:
    m = request.cookies.get(MODE_COOKIE, DEFAULT_MODE)
    return m if m in MODES else DEFAULT_MODE


async def _prefetch_next(user_id: int, client: Anthropic) -> None:
    """Best-effort: pre-generate sentences for the next likely word in each
    chosen language. Runs after the response is sent so it doesn't add to the
    user's wait."""
    conn = db.connect()
    try:
        for lang in db.get_langs(conn, user_id):
            item = study.pick_next(conn, user_id, lang, skip_shown=True)
            if item is not None:
                await study.ensure_sentences(conn, item.word_id, item.lemma, lang, user_id, client)
    except Exception:
        pass
    finally:
        conn.close()


def _text_card_ctx(card: text_study.TextCard) -> dict:
    prompt = card.english if card.direction == text_study.EN_TO_TL else card.target
    hint = LANGS[card.lang]["name"] if card.direction == text_study.EN_TO_TL else "English"
    return {
        **_lang_ctx(card.lang),
        "card_type": "text",
        "known_word_id": card.known_word_id,
        "direction": card.direction,
        "prompt": prompt,
        "hint_lang": hint,
    }


async def _prepare_voice_card(conn, user_id: int, client: Anthropic, lang: str) -> dict | None:
    item = study.pick_next(conn, user_id, lang)
    if item is None:
        return None
    await study.ensure_sentences(conn, item.word_id, item.lemma, lang, user_id, client)
    if item.is_new:
        study.ensure_user_word(conn, user_id, item.word_id)
    s = study.pick_sentence(conn, item.word_id)
    if s is None:
        return None
    return {**_lang_ctx(lang), "card_type": "voice", "sentence_id": s["id"], "audio": s["audio_path"]}


def _prepare_text_card(conn, user_id: int, lang: str) -> dict | None:
    card = text_study.pick_card(conn, user_id, lang)
    if card is None:
        return None
    return _text_card_ctx(card)


async def _prepare_card(conn, user_id: int, client: Anthropic, mode: str) -> dict:
    # Try every (kind, lang) pair in random order so an exhausted language or
    # kind falls through to the next instead of showing "all caught up".
    for kind, lang in candidates(mode, db.get_langs(conn, user_id)):
        if kind == "voice":
            ctx = await _prepare_voice_card(conn, user_id, client, lang)
        else:
            ctx = _prepare_text_card(conn, user_id, lang)
        if ctx is not None:
            return {**ctx, "mode": mode}
    return {"card_type": None, "mode": mode}


@app.get("/study")
async def study_page(
    request: Request,
    background_tasks: BackgroundTasks,
    user_id: int = Depends(require_user),
    client: Anthropic = Depends(get_anthropic),
):
    mode = _mode(request)
    langs = _user_langs(user_id)
    if not langs:
        return RedirectResponse("/langs", status_code=303)
    conn = db.connect()
    try:
        ctx = await _prepare_card(conn, user_id, client, mode)
    finally:
        conn.close()
    if mode != "text":
        background_tasks.add_task(_prefetch_next, user_id, client)
    ctx["chosen"] = [LANGS[code] for code in langs]
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
            SELECT s.id, s.text, s.gloss_en, s.audio_path, w.lemma, w.lang FROM sentences s
            JOIN words w ON w.id = s.word_id
            WHERE s.id = ?
            """,
            (sentence_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404)
        user_lemmas = study.user_word_lemmas(conn, user_id, row["lang"])
    finally:
        conn.close()
    lang = row["lang"]
    tokens = study.diff_and_highlight(row["text"], typed, row["lemma"], lang, user_lemmas)
    return templates.TemplateResponse(
        request,
        "_reveal.html",
        {
            **_lang_ctx(lang),
            "sentence_id": sentence_id,
            "tokens": tokens,
            "gloss": row["gloss_en"],
            "latin": LANGS[lang]["romanize"](row["text"]),
            "audio": row["audio_path"],
            "typed": typed,
        },
    )


@app.get("/words")
def words_page(
    request: Request,
    q: str = "",
    kind: str = "voice",
    user_id: int = Depends(require_user),
):
    if kind not in ("voice", "text"):
        kind = "voice"
    like = f"%{q.strip().lower()}%"
    conn = db.connect()
    try:
        chosen = db.get_langs(conn, user_id)
        marks = ",".join("?" * len(chosen))
        if kind == "voice":
            rows = conn.execute(
                f"""
                SELECT w.id, w.lang, w.lemma, w.gloss_en, uw.status, uw.ef, uw.due_at
                FROM user_words uw JOIN words w ON w.id = uw.word_id
                WHERE uw.user_id = ? AND w.lang IN ({marks}) AND LOWER(w.lemma) LIKE ?
                ORDER BY uw.due_at IS NULL, uw.due_at ASC
                """,
                (user_id, *chosen, like),
            ).fetchall()
        else:
            rows = conn.execute(
                f"""
                SELECT kw.lang, kw.target, kw.romanized, kw.english,
                       CASE ukw.direction WHEN 'en_to_tl' THEN 'en→' || kw.lang ELSE kw.lang || '→en' END AS direction,
                       ukw.status, ukw.ef, ukw.due_at
                FROM user_known_words ukw JOIN known_words kw ON kw.id = ukw.known_word_id
                WHERE ukw.user_id = ? AND kw.lang IN ({marks})
                  AND (LOWER(kw.target) LIKE ? OR LOWER(kw.english) LIKE ?)
                ORDER BY ukw.due_at IS NULL, ukw.due_at ASC
                """,
                (user_id, *chosen, like, like),
            ).fetchall()
    finally:
        conn.close()
    tpl = "_word_rows.html" if request.headers.get("HX-Request") else "words.html"
    return templates.TemplateResponse(
        request, tpl, {"rows": rows, "q": q, "kind": kind, "langs": LANGS}
    )


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
        known_rows = conn.execute(
            """
            SELECT w.lang, COUNT(*) AS n FROM user_words uw JOIN words w ON w.id = uw.word_id
            WHERE uw.user_id = ? AND uw.status = 'known' GROUP BY w.lang
            """,
            (user_id,),
        ).fetchall()
        chosen = db.get_langs(conn, user_id)
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

    known_by_lang = {r["lang"]: r["n"] for r in known_rows}
    known = [(LANGS[code], known_by_lang.get(code, 0)) for code in chosen]
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
    flagged_word_ids: list[int] = Form(default=[]),
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
        # Flags first, so their reviews don't eat into the main word's Again window.
        for fid in set(flagged_word_ids) - {word_id}:
            seen = conn.execute("SELECT 1 FROM user_words WHERE user_id=? AND word_id=?", (user_id, fid)).fetchone()
            if seen:
                srs.record_grade(conn, user_id, srs.Grade.AGAIN, "user_words", {"word_id": fid}, sentence_id, typed)
        srs.record_grade(conn, user_id, grade, "user_words", {"word_id": word_id}, sentence_id, typed)
        ctx = await _prepare_card(conn, user_id, client, mode)
    finally:
        conn.close()
    if mode != "text":
        background_tasks.add_task(_prefetch_next, user_id, client)
    return templates.TemplateResponse(request, "_card.html", ctx)


def _load_text_card(conn, known_word_id: int, direction: str) -> text_study.TextCard:
    if direction not in text_study.DIRECTIONS:
        raise HTTPException(400, "invalid direction")
    card = text_study.load_card(conn, known_word_id, direction)
    if card is None:
        raise HTTPException(404)
    return card


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
            **_lang_ctx(card.lang),
            "target": card.target,
            "romanized": card.romanized,
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
    if direction not in text_study.DIRECTIONS:
        raise HTTPException(400, "invalid direction")
    mode = _mode(request)
    conn = db.connect()
    try:
        exists = conn.execute(
            "SELECT 1 FROM known_words WHERE id = ?", (known_word_id,)
        ).fetchone()
        if exists is None:
            raise HTTPException(404)
        key = {"known_word_id": known_word_id, "direction": direction}
        srs.record_grade(conn, user_id, grade, "user_known_words", key, typed=typed)
        ctx = await _prepare_card(conn, user_id, client, mode)
    finally:
        conn.close()
    if mode != "text":
        background_tasks.add_task(_prefetch_next, user_id, client)
    return templates.TemplateResponse(request, "_card.html", ctx)


def _known_add_ctx(user_id: int, message: str | None) -> dict:
    codes = _user_langs(user_id) or list(LANGS)
    return {"message": message, "langs": [(code, LANGS[code]) for code in codes]}


@app.get("/known/add")
def known_add_form(request: Request, user_id: int = Depends(require_user)):
    return templates.TemplateResponse(request, "known_add.html", _known_add_ctx(user_id, None))


@app.post("/known/add")
def known_add_submit(
    request: Request,
    english: str = Form(...),
    lang: str = Form("ru"),
    target: str = Form(""),
    romanized: str = Form(""),
    user_id: int = Depends(require_user),
):
    if lang not in LANGS:
        raise HTTPException(400, "invalid language")
    english = english.strip()
    target, romanized = text_study.normalize_known_word(lang, target, romanized)
    if not target or not english:
        return templates.TemplateResponse(
            request, "known_add.html", _known_add_ctx(user_id, "word and english required")
        )
    conn = db.connect()
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO known_words(lang, target, romanized, english) VALUES (?, ?, ?, ?)",
            (lang, target, romanized, english),
        )
        msg = f"added {target}" if cur.rowcount else f"already exists: {target}"
    finally:
        conn.close()
    return templates.TemplateResponse(request, "known_add.html", _known_add_ctx(user_id, msg))
