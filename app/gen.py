import json
from dataclasses import dataclass

from anthropic import Anthropic

from app.langs import LANGS

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 1024


@dataclass(frozen=True)
class Sentence:
    text: str
    gloss_en: str


@dataclass(frozen=True)
class GenResult:
    gloss_en: str
    sentences: list[Sentence]


def _build_prompt(lang: str, target: str, known: list[str]) -> str:
    tpl = LANGS[lang]["gen_prompt"]
    known_str = ", ".join(known) if known else "(none)"
    return tpl.replace("{target}", target).replace("{known}", known_str)


def _parse(text: str) -> GenResult:
    data = json.loads(text)
    sentences = [
        Sentence(text=s["text"], gloss_en=s["gloss_en"])
        for s in data["sentences"]
    ]
    if len(sentences) != 3:
        raise ValueError(f"expected 3 sentences, got {len(sentences)}")
    return GenResult(gloss_en=data["gloss_en"], sentences=sentences)


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
    prompt = _build_prompt(lang, target, known)
    for _ in range(2):
        msg = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        try:
            return _parse(msg.content[0].text)
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            continue
    return _fallback(target)
