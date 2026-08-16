from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("SECRET_KEY", "test-secret")

    import importlib
    from app import gen, main, tts
    importlib.reload(main)

    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    monkeypatch.setattr(tts, "AUDIO_DIR", audio_dir)

    async def fake_synth(text: str, voice: str) -> Path:
        p = audio_dir / f"{abs(hash(text + voice))}.mp3"
        p.write_bytes(b"\xff\xfb\x90\x00")
        return p

    monkeypatch.setattr("app.study.tts.synthesize", fake_synth)

    parsed = gen._GenResultOut(
        gloss_en="and",
        sentences=[
            gen._SentenceOut(text="Мама и папа.", gloss_en="Mom and dad."),
            gen._SentenceOut(text="Я и ты.", gloss_en="You and I."),
            gen._SentenceOut(text="Он и она.", gloss_en="He and she."),
        ],
    )
    fake_client = MagicMock()
    fake_client.messages.parse.return_value = MagicMock(parsed_output=parsed)
    main.app.dependency_overrides[main.get_anthropic] = lambda: fake_client

    with TestClient(main.app) as c:
        c.fake_client = fake_client  # type: ignore[attr-defined]
        yield c
