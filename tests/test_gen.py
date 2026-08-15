import json
from unittest.mock import MagicMock

from app.gen import generate


def _resp(text: str) -> MagicMock:
    return MagicMock(content=[MagicMock(text=text)])


def _valid_payload(gloss: str = "hello") -> str:
    return json.dumps(
        {
            "gloss_en": gloss,
            "sentences": [
                {"text": "Привет, как дела?", "gloss_en": "Hi, how are you?"},
                {"text": "Привет, друг!", "gloss_en": "Hi, friend!"},
                {"text": "Скажи привет.", "gloss_en": "Say hi."},
            ],
        }
    )


def test_happy_path():
    client = MagicMock()
    client.messages.create.return_value = _resp(_valid_payload("hello"))
    result = generate("ru", "привет", [], client=client)
    assert result.gloss_en == "hello"
    assert len(result.sentences) == 3
    assert result.sentences[0].text == "Привет, как дела?"
    assert client.messages.create.call_count == 1


def test_retries_once_on_bad_json_then_succeeds():
    client = MagicMock()
    client.messages.create.side_effect = [
        _resp("not json at all {"),
        _resp(_valid_payload()),
    ]
    result = generate("ru", "привет", [], client=client)
    assert len(result.sentences) == 3
    assert client.messages.create.call_count == 2


def test_falls_back_after_two_failures():
    client = MagicMock()
    client.messages.create.side_effect = [
        _resp("not json"),
        _resp("still not json"),
    ]
    result = generate("ru", "привет", [], client=client)
    assert result.gloss_en == ""
    assert len(result.sentences) == 1
    assert result.sentences[0].text == "привет"
    assert client.messages.create.call_count == 2


def test_falls_back_on_wrong_shape():
    bad = json.dumps({"sentences": []})
    client = MagicMock()
    client.messages.create.side_effect = [_resp(bad), _resp(bad)]
    result = generate("ru", "x", [], client=client)
    assert result.sentences[0].text == "x"


def test_prompt_includes_known_lemmas():
    client = MagicMock()
    client.messages.create.return_value = _resp(_valid_payload())
    generate("ru", "привет", ["мама", "папа"], client=client)
    sent_prompt = client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "привет" in sent_prompt
    assert "мама" in sent_prompt
    assert "папа" in sent_prompt


def test_prompt_handles_empty_known_list():
    client = MagicMock()
    client.messages.create.return_value = _resp(_valid_payload())
    generate("ru", "привет", [], client=client)
    sent_prompt = client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "(none)" in sent_prompt
