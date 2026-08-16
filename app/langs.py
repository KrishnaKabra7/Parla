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

LANGS = {
    "ru": {
        "name": "Russian",
        "freq_file": "data/ru_freq.txt",
        "voice": "ru-RU-DmitryNeural",
        "system_prompt": SYSTEM_PROMPT_RU,
    },
}
