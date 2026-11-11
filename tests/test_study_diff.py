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


def test_german_diff_strips_german_quotes():
    toks = diff_and_highlight("Er sagt: „Hallo“.", "er sagt hallo", "hallo", "de")
    assert all(t.matched for t in toks)


def test_spanish_diff_strips_inverted_marks_and_accents():
    toks = diff_and_highlight("¿Dónde está mi niño?", "donde esta mi nino", "niño", "es")
    assert all(t.matched for t in toks)
    assert [t.is_target for t in toks] == [False, False, False, True]


def test_french_diff_handles_elision_and_accents():
    toks = diff_and_highlight("J’ai vu l'école hier.", "jai vu ecole hier", "école", "fr")
    assert [t.matched for t in toks] == [False, True, True, True]
    assert [t.is_target for t in toks] == [False, False, True, False]
    toks = diff_and_highlight("J'ai faim aujourd'hui.", "j'ai faim aujourd'hui", "ai", "fr")
    assert all(t.matched for t in toks)
    assert toks[0].is_target
