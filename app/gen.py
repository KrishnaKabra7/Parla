import logging
from dataclasses import dataclass

from anthropic import Anthropic
from pydantic import BaseModel

from app.langs import LANGS

MODEL = "claude-haiku-4-5"
MAX_TOKENS = 1024

log = logging.getLogger("app.gen")


# Schema for structured outputs — Anthropic enforces this shape server-side.
class _SentenceOut(BaseModel):
    text: str
    gloss_en: str


class _GenResultOut(BaseModel):
    gloss_en: str
    sentences: list[_SentenceOut]


@dataclass(frozen=True)
class Sentence:
    text: str
    gloss_en: str


@dataclass(frozen=True)
class GenResult:
    gloss_en: str
    sentences: list[Sentence]


def _fallback(target: str) -> GenResult:
    return GenResult(
        gloss_en="",
        sentences=[Sentence(text=target, gloss_en="")],
    )


def generate(
    lang: str,
    target: str,
    known: list[str],
    client: Anthropic | None = None,
) -> GenResult:
    client = client or Anthropic()
    known_str = ", ".join(sorted(known)) if known else "(none)"
    system = [
        {"type": "text", "text": LANGS[lang]["system_prompt"]},
        {
            "type": "text",
            "text": f"Known lemmas: {known_str}",
            "cache_control": {"type": "ephemeral"},
        },
    ]
    try:
        msg = client.messages.parse(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": f"Target word: {target}"}],
            output_format=_GenResultOut,
        )
        parsed = msg.parsed_output
        return GenResult(
            gloss_en=parsed.gloss_en,
            sentences=[Sentence(text=s.text, gloss_en=s.gloss_en) for s in parsed.sentences],
        )
    except Exception as e:
        log.warning("gen fallback for %r: %s: %s", target, type(e).__name__, e)
        return _fallback(target)
