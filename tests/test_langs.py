import random

from app.langs import LANGS, candidates


def test_german_fold_ignores_umlauts_and_case():
    fold = LANGS["de"]["fold"]
    assert fold("Straße") == fold("strasse")
    assert fold("Müller") == fold("mueller")
    assert fold("Größe") == fold("groesse")


def test_russian_fold_transliterates_latin():
    fold = LANGS["ru"]["fold"]
    assert fold("privet") == fold("Привет")


def test_romanize_only_for_russian():
    assert LANGS["ru"]["romanize"]("привет") == "privet"
    assert LANGS["de"]["romanize"]("Hallo") is None


def test_candidates_single_kind():
    rng = random.Random(0)
    assert sorted(candidates("voice", ["ru", "de"], rng)) == [("voice", "de"), ("voice", "ru")]
    assert candidates("text", ["de"], rng) == [("text", "de")]


def test_candidates_mixed_covers_all_pairs():
    got = candidates("mixed", ["ru", "de"], random.Random(1))
    assert sorted(got) == sorted(
        [("voice", "ru"), ("voice", "de"), ("text", "ru"), ("text", "de")]
    )


def test_candidates_mixed_varies_first_pick():
    firsts = {candidates("mixed", ["ru", "de"], random.Random(s))[0] for s in range(50)}
    assert len(firsts) == 4
