import random

from app.translit import to_cyrillic, to_latin

SYSTEM_PROMPT_RU = """You generate short Russian sentences for a vocabulary-trainer app.

Given a target Russian word and a list of Russian lemmas the learner already knows, output STRICT JSON in this exact shape (and nothing else — no prose, no code fences):

{"gloss_en": "<English translation of the target word>", "sentences": [{"text": "<Russian sentence>", "gloss_en": "<English translation>"}, {"text": "...", "gloss_en": "..."}, {"text": "...", "gloss_en": "..."}]}

Rules:
- Exactly 3 sentences.
- Each sentence is 4-9 words.
- Natural spoken register (things a native speaker would actually say).
- Every content word other than the target MUST come from the known-lemmas list. Function words (prepositions, pronouns, particles, common conjunctions) are always allowed.
- The target word should appear in a common inflected form appropriate to the sentence, not only the citation form.
- If the known list is empty or too small to build 3 natural sentences, generate the most natural short (4-9 word) sentences you can that use the target word, keeping them minimal."""

SYSTEM_PROMPT_DE = """You generate short German sentences for a vocabulary-trainer app.

Given a target German word and a list of German words the learner already knows, output STRICT JSON in this exact shape (and nothing else — no prose, no code fences):

{"gloss_en": "<English translation of the target word>", "sentences": [{"text": "<German sentence>", "gloss_en": "<English translation>"}, {"text": "...", "gloss_en": "..."}, {"text": "...", "gloss_en": "..."}]}

Rules:
- Exactly 3 sentences.
- Each sentence is 4-9 words.
- Natural spoken register (things a native speaker would actually say).
- Every content word other than the target MUST come from the known-words list. Function words (articles, prepositions, pronouns, particles, common conjunctions, forms of sein/haben) are always allowed.
- The target word may be given in lowercase; use correct German capitalization in the sentences (nouns capitalized).
- The target word should appear in a common inflected form appropriate to the sentence, not only the citation form.
- If the known list is empty or too small to build 3 natural sentences, generate the most natural short (4-9 word) sentences you can that use the target word, keeping them minimal and beginner-friendly."""

_DE_FOLD = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def _fold_ru(s: str) -> str:
    return to_cyrillic(s).lower()


def _fold_de(s: str) -> str:
    return s.lower().translate(_DE_FOLD)


LANGS = {
    "ru": {
        "name": "Russian",
        "flag": "🇷🇺",
        "freq_file": "data/ru_freq.txt",
        "known_file": "data/known_words.txt",
        "voice": "ru-RU-DmitryNeural",
        "system_prompt": SYSTEM_PROMPT_RU,
        "fold": _fold_ru,
        "romanize": to_latin,
    },
    "de": {
        "name": "German",
        "flag": "🇩🇪",
        "freq_file": "data/de_freq.txt",
        "known_file": "data/known_words_de.txt",
        "voice": "de-DE-ConradNeural",
        "system_prompt": SYSTEM_PROMPT_DE,
        "fold": _fold_de,
        "romanize": lambda s: None,
    },
}

KINDS = ("voice", "text")


def candidates(mode: str, langs: list[str], rng: random.Random | None = None) -> list[tuple[str, str]]:
    r = rng or random
    kinds = [mode] if mode in KINDS else list(KINDS)
    pairs = [(k, lang) for k in kinds for lang in langs]
    r.shuffle(pairs)
    return pairs
