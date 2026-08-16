from app.translit import has_cyrillic, to_cyrillic, to_latin


def test_has_cyrillic_true():
    assert has_cyrillic("привет")
    assert has_cyrillic("hello и world")


def test_has_cyrillic_false():
    assert not has_cyrillic("hello")
    assert not has_cyrillic("")
    assert not has_cyrillic("privet!")


def test_cyrillic_passes_through():
    assert to_cyrillic("привет") == "привет"
    assert to_cyrillic("Привет, мир!") == "Привет, мир!"


def test_simple_latin_word():
    assert to_cyrillic("privet") == "привет"


def test_multichar_shch():
    assert to_cyrillic("shchi") == "щи"


def test_multichar_zh():
    assert to_cyrillic("zhena") == "жена"


def test_multichar_ya_yu_yo():
    assert to_cyrillic("yabloko") == "яблоко"
    assert to_cyrillic("yug") == "юг"
    assert to_cyrillic("yolka") == "ёлка"


def test_punctuation_preserved():
    assert to_cyrillic("privet, mir!") == "привет, мир!"


def test_case_normalized_to_lower():
    assert to_cyrillic("PRIVET") == "привет"


def test_soft_and_hard_sign():
    assert to_cyrillic("mat'") == "мать"
    assert to_cyrillic('pod"ezd') == "подъезд"


def test_empty_string():
    assert to_cyrillic("") == ""


def test_digits_pass_through():
    assert to_cyrillic("dom 5") == "дом 5"


def test_to_latin_simple():
    assert to_latin("привет") == "privet"


def test_to_latin_multichar_letters():
    assert to_latin("щи") == "shchi"
    assert to_latin("жена") == "zhena"
    assert to_latin("яблоко") == "yabloko"


def test_to_latin_preserves_punctuation_and_digits():
    assert to_latin("привет, как дела? 5") == "privet, kak dela? 5"


def test_to_latin_capital_first_letter():
    assert to_latin("Привет") == "Privet"


def test_to_latin_passes_through_latin():
    assert to_latin("hello") == "hello"
