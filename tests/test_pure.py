"""Seam 2: pure functions, no fixtures at all.

All three fail silently in production. A `normalize_title()` regression quietly
lowers the lyrics hit rate in slice 4 with no error anywhere; a stale
player-state write corrupts the position with no visible failure at all.
"""

import pytest

from muse.library import FULL_LISTEN, PARTIAL, SKIP, UNKNOWN
from muse.library import accept_state_write, classify_play
from muse.titles import normalize_title


# --- normalize_title -------------------------------------------------------
# Tested against the real polluted titles measured in the library.

@pytest.mark.parametrize(
    "title,expected",
    [
        ("Talking to the Moon - Acoustic Piano", "talking to the moon"),
        ("Stoned - Bonus Track", "stoned"),
        (
            'Just for a Moment - From "High School Musical: The Musical: The Series"',
            "just for a moment",
        ),
        ("&burn (with Vince Staples)", "&burn"),
        ("Uptown Funk (feat. Bruno Mars)", "uptown funk"),
        ("Levitating (feat. DaBaby)", "levitating"),
        ("Bohemian Rhapsody - Remastered 2011", "bohemian rhapsody"),
    ],
)
def test_noise_is_stripped(title, expected):
    assert normalize_title(title) == expected


# The load-bearing assertions. Over-eager normalization eats real titles, and
# the damage shows up as a lyrics lookup that quietly matches the wrong song.
@pytest.mark.parametrize(
    "title,expected",
    [
        ("Sgt. Pepper's Lonely Hearts Club Band (Reprise)",
         "sgt. pepper's lonely hearts club band (reprise)"),
        ("Marquee Moon", "marquee moon"),
        ("Hurt - Nine Inch Nails Cover", "hurt - nine inch nails cover"),
        ("Everything In Its Right Place", "everything in its right place"),
        ("R U Mine?", "r u mine?"),
        ("D-Day", "d-day"),
        ("9 to 5", "9 to 5"),
    ],
)
def test_legitimate_titles_are_left_alone(title, expected):
    assert normalize_title(title) == expected


def test_empty_title_is_empty():
    assert normalize_title("") == ""


def test_normalization_is_stable():
    """Applying it twice changes nothing, so a re-index cannot drift."""
    once = normalize_title("Uptown Funk (feat. Bruno Mars)")
    assert normalize_title(once) == once


# --- classify_play ---------------------------------------------------------

def test_track_end_is_a_full_listen():
    assert classify_play(180_000, 180_000, "track_end") == FULL_LISTEN


def test_track_end_is_a_full_listen_even_when_position_lags():
    """The client's last heartbeat is up to 10 seconds behind the end of the
    track. `track_end` is the fact; the position is not."""
    assert classify_play(172_000, 180_000, "track_end") == FULL_LISTEN


def test_just_under_the_boundary_is_a_skip():
    assert classify_play(26_999, 180_000, "user_next") == SKIP


def test_exactly_at_the_boundary_is_not_a_skip():
    assert classify_play(27_000, 180_000, "user_next") == PARTIAL


def test_most_of_the_way_through_is_a_full_listen():
    assert classify_play(160_000, 180_000, "user_next") == FULL_LISTEN


def test_lost_is_neither_skip_nor_full_listen():
    """The phone died. There is no user intent behind it, and slice 2 must be
    able to exclude it from training pairs."""
    assert classify_play(5_000, 180_000, "lost") == UNKNOWN
    assert classify_play(179_000, 180_000, "lost") == UNKNOWN


def test_an_open_span_is_unknown():
    assert classify_play(5_000, 180_000, None) == UNKNOWN


@pytest.mark.parametrize("duration", [0, None])
def test_unknown_duration_does_not_divide_by_zero(duration):
    assert classify_play(5_000, duration, "user_next") == UNKNOWN


def test_null_position_is_a_skip():
    assert classify_play(None, 180_000, "user_next") == SKIP


# --- accept_state_write ----------------------------------------------------

def test_a_playing_client_with_a_newer_timestamp_is_accepted():
    assert accept_state_write(2000, 1000, "playing") is True


def test_a_stale_write_is_rejected():
    assert accept_state_write(500, 1000, "playing") is False


def test_an_equal_timestamp_is_rejected():
    """Two writes carrying the same timestamp carry no evidence of which came
    second."""
    assert accept_state_write(1000, 1000, "playing") is False


@pytest.mark.parametrize("client_state", ["paused", "idle", "stopped", ""])
def test_a_client_that_is_not_playing_never_writes(client_state):
    """The whole point: an idle background tab heartbeating every 10 seconds
    must not overwrite the position of the device actually playing."""
    assert accept_state_write(999_999, 1000, client_state) is False
