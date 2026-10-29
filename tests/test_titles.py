from muse.titles import qualifiers, same_title


def test_qualifiers_paren_live():
    assert qualifiers("Dynamite (Live)") == {"live"}


def test_qualifiers_dash_live():
    assert qualifiers("Dynamite - Live") == {"live"}


def test_qualifiers_remix():
    assert qualifiers("Up (Remix)") == {"remix"}


def test_qualifiers_remix_and_instrumental():
    assert qualifiers("Attention (250 Remix / Instrumental)") == {"remix", "instrumental"}


def test_qualifiers_inst_abbreviation():
    assert qualifiers("Speed of Summer (Inst.)") == {"instrumental"}


def test_qualifiers_live_as_title_word():
    assert qualifiers("Live Your Life") == {"live"}


def test_qualifiers_no_substring_false_positives():
    assert qualifiers("Delivery") == frozenset()
    assert qualifiers("Instant Crush") == frozenset()
    assert qualifiers("밤편지") == frozenset()


def test_same_title_identical():
    assert same_title("Dynamite", "Dynamite")


def test_same_title_qualifier_differs():
    assert not same_title("Dynamite", "Dynamite (Live)")
    assert not same_title("Dynamite", "Dynamite - Live")
    assert not same_title("Up", "Up (Remix)")


def test_same_title_feat_ignored():
    assert same_title("Uptown Funk (feat. Bruno Mars)", "Uptown Funk")


def test_same_title_hangul():
    assert same_title("밤편지", "밤편지")


def test_same_title_empty_is_false():
    assert not same_title("", "")
