from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app import study, text_study
from app.translit import to_latin

_MDV2_SPECIALS = r"_*[]()~`>#+-=|{}.!\\"


def esc(s: str) -> str:
    return "".join("\\" + c if c in _MDV2_SPECIALS else c for c in s)


def voice_prompt() -> str:
    return esc("Type what you heard.")


def text_prompt(card: text_study.TextCard) -> str:
    if card.direction == text_study.EN_TO_RU:
        prompt, hint = card.english, "Russian"
    else:
        prompt, hint = card.cyrillic, "English"
    return f"*{esc(prompt)}*\n_Answer in {esc(hint)}_"


def voice_reveal(tokens: list[study.Token], gloss: str) -> str:
    parts = []
    for tok in tokens:
        text = esc(tok.text)
        if tok.is_target:
            parts.append(f"__{text}__")
        elif tok.matched:
            parts.append(f"*{text}*")
        else:
            parts.append(f"~{text}~")
    truth = " ".join(parts)
    cyrillic_line = "".join(t.text + " " for t in tokens).strip()
    latin = esc(to_latin(cyrillic_line))
    out = f"{truth}\n_{latin}_"
    if gloss:
        out += f"\n{esc(gloss)}"
    return out


def text_reveal(card: text_study.TextCard, correct: bool, typed: str) -> str:
    mark = "\\✓" if correct else "\\✗"
    lines = [
        f"{mark} *{esc(card.cyrillic)}*",
        f"_{esc(card.latin)}_",
        esc(card.english),
    ]
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


def mode_keyboard() -> InlineKeyboardMarkup:
    row = [
        InlineKeyboardButton(text="mixed", callback_data="mode:mixed"),
        InlineKeyboardButton(text="voice", callback_data="mode:voice"),
        InlineKeyboardButton(text="text", callback_data="mode:text"),
    ]
    return InlineKeyboardMarkup(inline_keyboard=[row])
