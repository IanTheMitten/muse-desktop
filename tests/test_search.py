"""Search.

Finding a song is a loud failure — you either see it or you do not — so this
file is deliberately short. What it does cover is the part that fails silently
or rudely: a listener's text being treated as query syntax. Unquoted, a stray
`"` is a 500 and a bare `NOT` quietly changes what the search means.
"""

from conftest import insert_track

from muse.library.queries import search

LIBRARY = [
    (1, "Talking to the Moon - Acoustic Piano", "Bruno Mars", "Doo-Wops & Hooligans"),
    (2, "Talking to the Moon", "Bruno Mars", "Doo-Wops & Hooligans"),
    (3, "Uptown Funk (feat. Bruno Mars)", "Mark Ronson", "Uptown Special"),
    (4, "everything i wanted", "Billie Eilish", "everything i wanted"),
    (5, "&burn (with Vince Staples)", "Billie Eilish", "dont smile at me"),
]


def load(conn):
    for rowid, name, artist, album in LIBRARY:
        insert_track(conn, rowid, track_name=name, artist_name=artist,
                     album_name=album)
    return conn


def names(results):
    return [track["track_name"] for track in results]


def test_a_plain_title_finds_its_version_suffixed_sibling(conn):
    """The whole reason the normalized column exists."""
    load(conn)
    found = names(search(conn, "talking to the moon"))
    assert "Talking to the Moon" in found
    assert "Talking to the Moon - Acoustic Piano" in found


def test_a_fragment_from_the_middle_of_a_word_matches(conn):
    """Trigram tokenization, which is why substring search costs no second
    dependency."""
    load(conn)
    assert "everything i wanted" in names(search(conn, "verything"))


def test_searching_by_artist_finds_their_tracks(conn):
    load(conn)
    assert len(search(conn, "Billie Eilish")) == 2


def test_one_misspelt_word_does_not_lose_the_result(conn):
    """Each word is matched on its own, so the correctly typed ones still hit."""
    load(conn)
    assert "Talking to the Moon" in names(search(conn, "talking to the mooon"))


def test_a_single_misspelt_word_still_finds_the_track(conn):
    """No other word to fall back on. `mooon` shares no four-character run with
    `moon`, so substring matching fails outright and the trigram stage is what
    saves it."""
    load(conn)
    assert "Talking to the Moon" in names(search(conn, "mooon"))


def test_a_common_word_does_not_drag_in_the_whole_library(conn):
    """Matching the phrase and every word at once would return everything
    containing `the` — correctly ranked, and still useless."""
    load(conn)
    found = names(search(conn, "talking to the moon"))
    assert set(found) == {
        "Talking to the Moon",
        "Talking to the Moon - Acoustic Piano",
    }


def test_nonsense_finds_nothing(conn):
    """Trigram matching alone answers "shares some letters", which is far too
    generous — `zzzzznothing` shares `ing` with half the library. Without a
    similarity floor this returned sixty rows of noise."""
    load(conn)
    assert search(conn, "zzzzznothing") == []


def test_a_misspelling_returns_the_track_and_not_the_library(conn):
    load(conn)
    found = names(search(conn, "mooon"))
    assert "Talking to the Moon" in found
    assert len(found) <= 3


def test_the_fuzzy_stage_only_runs_when_nothing_else_matched(conn):
    """On its own the trigram stage is far too generous; an exact hit must
    never be diluted by it."""
    load(conn)
    assert names(search(conn, "everything i wanted")) == ["everything i wanted"]


def test_a_quote_does_not_break_the_search(conn):
    """Unescaped, a stray quote is a syntax error and a 500. Escaped, it is
    just a character the listener typed, and the rest of the words still
    search."""
    load(conn)
    assert "Talking to the Moon" in names(search(conn, 'talking " to'))


def test_fts_operators_are_treated_as_text(conn):
    """`NOT` must not act as an operator. If it did, this query would exclude
    every Bruno Mars track — a silently wrong result rather than a visible
    error."""
    load(conn)
    found = names(search(conn, "moon NOT bruno"))
    assert "Talking to the Moon" in found


def test_wildcards_are_not_syntax_either(conn):
    load(conn)
    assert search(conn, "* OR *") == []


def test_an_empty_query_returns_nothing(conn):
    load(conn)
    assert search(conn, "") == []
    assert search(conn, "   ") == []


def test_a_two_character_query_still_works(conn):
    """Trigram indexes cannot match anything shorter than three characters, so
    returning nothing for a short query would be a silent dead end."""
    load(conn)
    assert "&burn (with Vince Staples)" in names(search(conn, "bu"))


def test_results_carry_playability(conn):
    load(conn)
    assert all("playable" in track for track in search(conn, "moon"))


def test_display_titles_are_never_the_normalized_form(conn):
    load(conn)
    assert "Uptown Funk (feat. Bruno Mars)" in names(search(conn, "uptown funk"))
