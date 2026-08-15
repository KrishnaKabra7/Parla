import hashlib
from pathlib import Path

import edge_tts

AUDIO_DIR = Path("data/audio")


def cache_path(text: str, voice: str) -> Path:
    key = hashlib.sha1(f"{voice}|{text}".encode("utf-8")).hexdigest()
    return AUDIO_DIR / f"{key}.mp3"


async def synthesize(text: str, voice: str) -> Path:
    """Render `text` with `voice` to an mp3 in AUDIO_DIR. Cached by sha1(voice|text)."""
    path = cache_path(text, voice)
    if path.exists():
        return path
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".mp3.tmp")
    comm = edge_tts.Communicate(text, voice)
    await comm.save(str(tmp))
    tmp.replace(path)
    return path
