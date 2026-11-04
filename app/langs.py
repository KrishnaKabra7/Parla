import random
import unicodedata

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

SYSTEM_PROMPT_ES = """You generate short Spanish sentences for a vocabulary-trainer app.

Given a target Spanish word and a list of Spanish words the learner already knows, output STRICT JSON in this exact shape (and nothing else — no prose, no code fences):

{"gloss_en": "<English translation of the target word>", "sentences": [{"text": "<Spanish sentence>", "gloss_en": "<English translation>"}, {"text": "...", "gloss_en": "..."}, {"text": "...", "gloss_en": "..."}]}

Rules:
- Exactly 3 sentences.
- Each sentence is 4-9 words.
- Natural spoken register (things a native speaker would actually say), in neutral Spanish.
- Every content word other than the target MUST come from the known-words list. Function words (articles, prepositions, pronouns, common conjunctions, forms of ser/estar/haber/tener) are always allowed.
- Use correct accents and inverted punctuation (¿ ¡).
- The target word should appear in a common inflected form appropriate to the sentence, not only the citation form.
- If the known list is empty or too small to build 3 natural sentences, generate the most natural short (4-9 word) sentences you can that use the target word, keeping them minimal and beginner-friendly."""

TEXT_SYSTEM_PROMPT = """You generate {name} sentences for a translation-practice app. The learner sees the sentence in English or {name} and translates it into the other language.

Given a target {name} word (with its English meaning in parentheses) and a list of {name} words the learner already knows, output STRICT JSON in this exact shape (and nothing else — no prose, no code fences):

{{"gloss_en": "<English translation of the target word>", "sentences": [{{"text": "<{name} sentence>", "gloss_en": "<natural English translation>"}}, {{"text": "...", "gloss_en": "..."}}, {{"text": "...", "gloss_en": "..."}}]}}

Rules:
- Exactly 3 sentences, each 3-8 words.
- Each sentence must be something a person would actually say or need in everyday life: greetings, ordering food, shopping, asking directions, transport, plans with friends, home, work, feelings, small talk. No literary, abstract or textbook-only sentences.
- Use the target word in the sense given by its English meaning.
- Prefer words from the known list for everything else; common function words are always allowed. Keep any other vocabulary basic and high-frequency.
- Use correct spelling, capitalization, accents and punctuation for {name}.
- The English translation should be natural English, not word-for-word."""

_DE_UMLAUTS = str.maketrans({"ä": "a", "ö": "o", "ü": "u", "ß": "ss"})


def _fold_ru(s: str) -> str:
    return to_cyrillic(s).lower()


def _fold_de(s: str) -> str:
    # Collapse "über", "ueber" and "uber" to one form so learners without a
    # German keyboard aren't marked wrong. Applied to both sides of a compare.
    s = s.lower().replace("ae", "a").replace("oe", "o").replace("ue", "u")
    return s.translate(_DE_UMLAUTS)


def _fold_es(s: str) -> str:
    # Accents and ñ are optional when typing; applied to both sides of a compare.
    s = unicodedata.normalize("NFD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c))


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
    "es": {
        "name": "Spanish",
        "flag": "🇪🇸",
        "freq_file": "data/es_freq.txt",
        "known_file": "data/known_words_es.txt",
        "voice": "es-ES-AlvaroNeural",
        "system_prompt": SYSTEM_PROMPT_ES,
        "fold": _fold_es,
        "romanize": lambda s: None,
    },
}

KINDS = ("voice", "text")


def candidates(
    mode: str,
    langs: list[str],
    due: dict[tuple[str, str], int] | None = None,
    rng: random.Random | None = None,
) -> list[tuple[str, str]]:
    """(kind, lang) pairs to try in order: most due cards first, ties random,
    so mixed mode clears backlogs before introducing new cards."""
    r = rng or random
    kinds = [mode] if mode in KINDS else list(KINDS)
    pairs = [(k, lang) for k in kinds for lang in langs]
    r.shuffle(pairs)
    due = due or {}
    pairs.sort(key=lambda p: -due.get(p, 0))
    return pairs
