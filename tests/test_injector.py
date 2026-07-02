from orpheus.injector import normalize_newlines, utf16_units


def test_utf16_units_ascii():
    assert utf16_units("Hi") == [0x48, 0x69]


def test_utf16_units_hebrew():
    # שלום — each Hebrew letter is a single BMP code unit
    assert utf16_units("שלום") == [0x05E9, 0x05DC, 0x05D5, 0x05DD]


def test_utf16_units_emoji_surrogate_pair():
    # U+1F600 GRINNING FACE encodes as a surrogate pair
    assert utf16_units("\U0001F600") == [0xD83D, 0xDE00]


def test_normalize_newlines():
    assert normalize_newlines("a\r\nb\nc") == "a\rb\rc"
