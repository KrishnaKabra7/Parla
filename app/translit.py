_RULES: list[tuple[str, str]] = [
    ("shch", "щ"),
    ("sch", "щ"),
    ("zh", "ж"),
    ("sh", "ш"),
    ("ch", "ч"),
    ("kh", "х"),
    ("ts", "ц"),
    ("yo", "ё"),
    ("yu", "ю"),
    ("ya", "я"),
    ("ye", "е"),
    ("eh", "э"),
    ("a", "а"),
    ("b", "б"),
    ("v", "в"),
    ("g", "г"),
    ("d", "д"),
    ("e", "е"),
    ("z", "з"),
    ("i", "и"),
    ("j", "й"),
    ("k", "к"),
    ("l", "л"),
    ("m", "м"),
    ("n", "н"),
    ("o", "о"),
    ("p", "п"),
    ("r", "р"),
    ("s", "с"),
    ("t", "т"),
    ("u", "у"),
    ("f", "ф"),
    ("h", "х"),
    ("y", "ы"),
    ("c", "ц"),
    ("w", "в"),
    ("q", "к"),
    ("x", "кс"),
    ("'", "ь"),
    ('"', "ъ"),
]


def has_cyrillic(s: str) -> bool:
    return any("Ѐ" <= ch <= "ӿ" for ch in s)


def to_cyrillic(s: str) -> str:
    """Transliterate Latin to Cyrillic. Cyrillic input passes through unchanged."""
    if has_cyrillic(s):
        return s
    s = s.lower()
    out: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        for src, dst in _RULES:
            if s.startswith(src, i):
                out.append(dst)
                i += len(src)
                break
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


_TO_LATIN = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e",
    "ё": "yo", "ж": "zh", "з": "z", "и": "i", "й": "j", "к": "k",
    "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "shch", "ъ": '"', "ы": "y", "ь": "'",
    "э": "eh", "ю": "yu", "я": "ya",
}


def to_latin(s: str) -> str:
    """Reverse-transliterate Cyrillic to Latin. Non-Cyrillic chars pass through."""
    out: list[str] = []
    for ch in s:
        lower = ch.lower()
        mapped = _TO_LATIN.get(lower)
        if mapped is None:
            out.append(ch)
        elif ch == lower:
            out.append(mapped)
        else:
            out.append(mapped.capitalize())
    return "".join(out)
