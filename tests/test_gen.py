from unittest.mock import MagicMock

from app.gen import _GenResultOut, _SentenceOut, generate


def _valid_parsed(gloss: str = "hello") -> _GenResultOut:
    return _GenResultOut(
        gloss_en=gloss,
        sentences=[
            _SentenceOut(text="Привет, как дела?", gloss_en="Hi, how are you?"),
            _SentenceOut(text="Привет, друг!", gloss_en="Hi, friend!"),
            _SentenceOut(text="Скажи привет.", gloss_en="Say hi."),
        ],
    )


def _mock_client(parsed_output) -> MagicMock:
    client = MagicMock()
    client.messages.parse.return_value = MagicMock(parsed_output=parsed_output)
    return client


def test_happy_path():
    client = _mock_client(_valid_parsed("hello"))
    result = generate("ru", "привет", [], client=client)
    assert result.gloss_en == "hello"
    assert len(result.sentences) == 3
    assert result.sentences[0].text == "Привет, как дела?"


def test_falls_back_on_api_error():
    client = MagicMock()
    client.messages.parse.side_effect = RuntimeError("boom")
    result = generate("ru", "привет", [], client=client)
    assert result.gloss_en == ""
    assert len(result.sentences) == 1
    assert result.sentences[0].text == "привет"


def test_prompt_includes_known_lemmas_in_system():
    client = _mock_client(_valid_parsed())
    generate("ru", "привет", ["мама", "папа"], client=client)
    kwargs = client.messages.parse.call_args.kwargs
    system_text = "\n".join(b["text"] for b in kwargs["system"])
    user_text = kwargs["messages"][0]["content"]
    assert "мама" in system_text
    assert "папа" in system_text
    assert "привет" in user_text


def test_prompt_handles_empty_known_list():
    client = _mock_client(_valid_parsed())
    generate("ru", "привет", [], client=client)
    system_text = "\n".join(
        b["text"] for b in client.messages.parse.call_args.kwargs["system"]
    )
    assert "(none)" in system_text


def test_uses_haiku_model():
    client = _mock_client(_valid_parsed())
    generate("ru", "привет", [], client=client)
    assert client.messages.parse.call_args.kwargs["model"] == "claude-haiku-4-5"


def test_uses_structured_output_format():
    client = _mock_client(_valid_parsed())
    generate("ru", "привет", [], client=client)
    assert client.messages.parse.call_args.kwargs["output_format"] is _GenResultOut


def test_cache_control_marker_on_known_lemmas_block():
    client = _mock_client(_valid_parsed())
    generate("ru", "привет", ["мама"], client=client)
    system_blocks = client.messages.parse.call_args.kwargs["system"]
    assert "cache_control" not in system_blocks[0]
    assert system_blocks[-1]["cache_control"] == {"type": "ephemeral"}


def test_known_lemmas_sorted_for_stable_prefix():
    client = _mock_client(_valid_parsed())
    generate("ru", "x", ["b", "a", "c"], client=client)
    first_system = client.messages.parse.call_args.kwargs["system"]
    client.reset_mock()
    client.messages.parse.return_value = MagicMock(parsed_output=_valid_parsed())
    generate("ru", "x", ["c", "a", "b"], client=client)
    second_system = client.messages.parse.call_args.kwargs["system"]
    assert first_system == second_system
