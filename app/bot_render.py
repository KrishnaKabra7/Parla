from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app import db, study, text_study
from app.langs import LANGS

_MDV2_SPECIALS = r"_*[]()~`>#+-=|{}.!\\"


def esc(s: str) -> str:
    return "".join("\\" + c if c in _MDV2_SPECIALS else c for c in s)


def lang_label(lang: str) -> str:
    return esc(f"{LANGS[lang]['flag']} {LANGS[lang]['name']}")


def voice_prompt(lang: str) -> str:
    return f"{lang_label(lang)}\n" + esc("Type what you heard.")


def text_prompt(card: text_study.TextCard, sentence=None) -> str:
    to_tl = card.direction == text_study.EN_TO_TL
    if sentence is not None:
        prompt = sentence["gloss_en"] if to_tl else sentence["text"]
    else:
        prompt = card.english if to_tl else card.target
    hint = LANGS[card.lang]["name"] if to_tl else "English"
    return f"{lang_label(card.lang)}\n*{esc(prompt)}*\n_Answer in {esc(hint)}_"


def voice_reveal(tokens: list[study.Token], gloss: str, lang: str) -> str:
    parts = []
    for tok in tokens:
        text = esc(tok.text)
        if tok.is_target:
            parts.append(f"__{text}__")
        elif tok.matched:
            parts.append(f"*{text}*")
        else:
            parts.append(f"~{text}~")
    out = " ".join(parts)
    romanized = LANGS[lang]["romanize"](" ".join(t.text for t in tokens))
    if romanized:
        out += f"\n_{esc(romanized)}_"
    if gloss:
        out += f"\n{esc(gloss)}"
    return out


def text_reveal(card: text_study.TextCard, correct: bool, typed: str) -> str:
    mark = "\✓" if correct else "\✗"
    lines = [f"{mark} *{esc(card.target)}*"]
    if card.romanized:
        lines.append(f"_{esc(card.romanized)}_")
    lines.append(esc(card.english))
    if typed:
        lines.append(f"_you typed:_ {esc(typed)}")
    return "\n".join(lines)


def text_sentence_reveal(card: text_study.TextCard, sentence, typed: str) -> str:
    lines = [f"*{esc(sentence['text'])}*"]
    if romanized := LANGS[card.lang]["romanize"](sentence["text"]):
        lines.append(f"_{esc(romanized)}_")
    lines.append(esc(sentence["gloss_en"]))
    if typed:
        lines.append(f"_you typed:_ {esc(typed)}")
    return "\n".join(lines)


def grade_keyboard(kind: str, *ids: object) -> InlineKeyboardMarkup:
    payload = ":".join(str(x) for x in ids)
    row = [
        InlineKeyboardButton(text="Again", callback_data=f"g:0:{kind}:{payload}"),
        InlineKeyboardButton(text="Hard", callback_data=f"g:3:{kind}:{payload}"),
        InlineKeyboardButton(text="Good", callback_data=f"g:4:{kind}:{payload}"),
        InlineKeyboardButton(text="Easy", callback_data=f"g:5:{kind}:{payload}"),
    ]
    return InlineKeyboardMarkup(inline_keyboard=[row])


def mode_keyboard(prefs: dict[str, str]) -> InlineKeyboardMarkup:
    def row(prefix: str, key: str, values: tuple[str, ...]) -> list[InlineKeyboardButton]:
        return [
            InlineKeyboardButton(
                text=f"{'✅ ' if prefs[key] == v else ''}{'mix' if v == 'mixed' else v}",
                callback_data=f"{prefix}:{v}",
            )
            for v in values
        ]
    return InlineKeyboardMarkup(inline_keyboard=[row("mode", "mode", db.MODES), row("len", "length", db.LENGTHS)])


def lang_keyboard(chosen: list[str]) -> InlineKeyboardMarkup:
    row = [
        InlineKeyboardButton(
            text=f"{'✅' if code in chosen else '⬜'} {pack['flag']} {pack['name']}",
            callback_data=f"lang:{code}",
        )
        for code, pack in LANGS.items()
    ]
    done = [InlineKeyboardButton(text="start ▶", callback_data="lang:done")]
    return InlineKeyboardMarkup(inline_keyboard=[row, done])


def add_lang_keyboard(langs: list[str]) -> InlineKeyboardMarkup:
    row = [
        InlineKeyboardButton(text=f"{LANGS[c]['flag']} {LANGS[c]['name']}", callback_data=f"addlang:{c}")
        for c in langs
    ]
    return InlineKeyboardMarkup(inline_keyboard=[row])
