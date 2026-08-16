import json
from dataclasses import dataclass

from anthropic import Anthropic

from app.langs import LANGS

MODEL = "claude-haiku-4-5"
MAX_TOKENS = 1024


@dataclass(frozen=True)
class Sentence:
    text: str
    gloss_en: str


@dataclass(frozen=True)
class GenResult:
    gloss_en: str
    sentences: list[Sentence]


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
    # Sorted so identical known-lemma sets produce byte-identical prefix bytes;
    # prompt caching is a prefix match and any reordering silently invalidates it.
    known_str = ", ".join(sorted(known)) if known else "(none)"
    system = [
        {"type": "text", "text": LANGS[lang]["system_prompt"]},
        {
            "type": "text",
            "text": f"Known lemmas: {known_str}",
            "cache_control": {"type": "ephemeral"},
        },
    ]
    user_content = f"Target word: {target}"
    for _ in range(2):
        msg = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user_content}],
        )
        try:
            return _parse(msg.content[0].text)
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            continue
    return _fallback(target)
