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


def test_prompt_includes_known_lemmas_in_system():
    client = MagicMock()
    client.messages.create.return_value = _resp(_valid_payload())
    generate("ru", "привет", ["мама", "папа"], client=client)
    kwargs = client.messages.create.call_args.kwargs
    system_text = "\n".join(b["text"] for b in kwargs["system"])
    user_text = kwargs["messages"][0]["content"]
    assert "мама" in system_text
    assert "папа" in system_text
    assert "привет" in user_text  # target lives in the user message, not system


def test_prompt_handles_empty_known_list():
    client = MagicMock()
    client.messages.create.return_value = _resp(_valid_payload())
    generate("ru", "привет", [], client=client)
    system_text = "\n".join(b["text"] for b in client.messages.create.call_args.kwargs["system"])
    assert "(none)" in system_text


def test_uses_haiku_model():
    client = MagicMock()
    client.messages.create.return_value = _resp(_valid_payload())
    generate("ru", "привет", [], client=client)
    assert client.messages.create.call_args.kwargs["model"] == "claude-haiku-4-5"


def test_cache_control_marker_on_known_lemmas_block():
    client = MagicMock()
    client.messages.create.return_value = _resp(_valid_payload())
    generate("ru", "привет", ["мама"], client=client)
    system_blocks = client.messages.create.call_args.kwargs["system"]
    # First block: instructions, no cache marker. Last block: known lemmas, with marker.
    assert "cache_control" not in system_blocks[0]
    assert system_blocks[-1]["cache_control"] == {"type": "ephemeral"}
    assert "Known lemmas:" in system_blocks[-1]["text"]


def test_known_lemmas_sorted_for_stable_prefix():
    client = MagicMock()
    client.messages.create.return_value = _resp(_valid_payload())
    # Same set, two different orderings — must produce identical system bytes
    generate("ru", "x", ["b", "a", "c"], client=client)
    first_system = client.messages.create.call_args.kwargs["system"]
    client.reset_mock()
    client.messages.create.return_value = _resp(_valid_payload())
    generate("ru", "x", ["c", "a", "b"], client=client)
    second_system = client.messages.create.call_args.kwargs["system"]
    assert first_system == second_system
