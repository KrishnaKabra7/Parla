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


def test_startup_seeds_both_languages(client):
    from app import db

    conn = db.connect()
    words = {r["lang"]: r["n"] for r in conn.execute("SELECT lang, COUNT(*) AS n FROM words GROUP BY lang")}
    de_known = {r["target"] for r in conn.execute("SELECT target FROM known_words WHERE lang = 'de'")}
    conn.close()
    assert words["ru"] == 3000
    assert words["de"] == 3000
    assert len(de_known) == 500
    assert {"ja", "nein", "ich", "Haus"} <= de_known


def test_german_fold_accepts_bare_vowels():
    fold = LANGS["de"]["fold"]
    assert fold("über") == fold("uber") == fold("ueber")
    assert fold("schön") == fold("schon")
    assert fold("Mädchen") == fold("madchen")


def test_spanish_fold_ignores_accents_tilde_and_case():
    fold = LANGS["es"]["fold"]
    assert fold("Canción") == fold("cancion")
    assert fold("Niño") == fold("nino")
    assert fold("pingüino") == fold("pinguino")
    assert fold("ÁRBOL") == fold("arbol")


def test_no_romanize_for_spanish():
    assert LANGS["es"]["romanize"]("hola") is None


def test_startup_seeds_spanish(client):
    from app import db

    conn = db.connect()
    n = conn.execute("SELECT COUNT(*) FROM words WHERE lang = 'es'").fetchone()[0]
    known = {r["target"] for r in conn.execute("SELECT target FROM known_words WHERE lang = 'es'")}
    conn.close()
    assert n == 3000
    assert len(known) == 500
    assert {"sí", "no", "yo", "casa"} <= known
