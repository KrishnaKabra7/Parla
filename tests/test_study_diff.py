from app.study import diff_and_highlight


def test_german_diff_ignores_case_and_umlauts():
    toks = diff_and_highlight("Ich gehe über die Straße.", "ich gehe ueber die strasse", "straße", "de")
    assert all(t.matched for t in toks)
    assert [t.is_target for t in toks] == [False, False, False, False, True]


def test_german_diff_marks_missing_word():
    toks = diff_and_highlight("Ich gehe nach Hause.", "ich gehe hause", "haus", "de")
    assert [t.matched for t in toks] == [True, True, False, True]


def test_german_diff_does_not_transliterate_latin_input():
    toks = diff_and_highlight("Ja, sehr gut.", "ja sehr gut", "ja", "de")
    assert all(t.matched for t in toks)


def test_russian_diff_still_accepts_latin_input():
    toks = diff_and_highlight("Привет, друг!", "privet drug", "привет", "ru")
    assert all(t.matched for t in toks)


def test_german_flags_known_word_stems():
    toks = diff_and_highlight("Das Haus ist groß.", "", "haus", "de", [(7, "groß")])
    assert toks[3].flag_word_id == 7
