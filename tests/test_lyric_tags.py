"""muse/lyric_tags.py: pure taxonomy, segmentation, asking and derived scores
(docs/specs/2026-10-12-lyric-tag-store.md D7)."""

import ast
from pathlib import Path

from muse import lyric_tags as L


def test_module_is_pure():
    tree = ast.parse(Path(L.__file__).read_text())
    imported = {a.name.split(".")[0] for n in ast.walk(tree)
                if isinstance(n, ast.Import) for a in n.names}
    imported |= {(n.module or "").split(".")[0] for n in ast.walk(tree)
                 if isinstance(n, ast.ImportFrom)}
    assert not imported & {"sqlite3", "requests", "urllib", "http", "socket"}


def test_every_group_has_an_escape_label():
    for name, (_, labels) in {**L.GROUPS, **L.SONG_GROUPS}.items():
        assert ("none" in labels) or ("absent" in labels), name
    for groups in L.DETAIL.values():
        for name, (_, labels) in groups.items():
            assert "none" in labels, name


def test_chants_ad_libs_and_headers_are_dropped():
    lines = L.lines_from_text("[Chorus]\nOh-oh-oh-oh\n(yeah)\n♪\nI walk the line tonight")
    assert [t for _, t in lines] == ["I walk the line tonight"]


def test_lrc_lines_keep_their_times():
    lines = L.lines_from_lrc("[00:01.00] first line here\n[00:02.50] second line here")
    assert lines == [(1000, "first line here"), (2500, "second line here")]


REPEATS = ("we sing it loud tonight", "hands up to the sky now",
           "nothing gonna stop us", "this is where we belong")


def _song(n_body, repeats=REPEATS):
    body = [f"distinct verse line number {i} goes" for i in range(n_body)]
    return L.lines_from_text("\n".join(list(repeats) + body + list(repeats)))


def test_hook_is_repeated_lines_and_body_splits_in_thirds():
    segs = L.segments(_song(15))
    assert len(segs["hook"]) == 4
    assert {"start", "middle", "end"} <= set(segs)


def test_short_body_is_one_segment():
    segs = L.segments(_song(5))
    assert "body" in segs and "start" not in segs


def test_short_hook_merges_into_body():
    segs = L.segments(_song(5, repeats=("one repeated line here",)))
    assert "hook" not in segs


def test_text_hash_is_stable_and_order_sensitive():
    a = [(None, "x y z"), (None, "a b c")]
    assert L.text_hash(a) == L.text_hash(list(a))
    assert L.text_hash(a) != L.text_hash(a[::-1])


def _first_label(state, groups):
    """Answers every question with its first label at probability 1."""
    out = {}
    for g, (_, labels) in groups.items():
        first = next(iter(labels))
        out[g] = {k: (1.0 if k == first else 0.0) for k in labels}
    return out


def test_tag_song_asks_song_level_and_every_segment():
    tags = L.tag_song(_first_label, "A — B", _song(15))
    assert set(tags["song"]) == set(L.SONG_GROUPS)
    assert {"hook", "start", "middle", "end"} <= set(tags)
    assert set(L.GROUPS) <= set(tags["start"])
    # theme_broad's first label is `love`, so the love detail is asked
    assert "detail_love" in tags["start"]


def test_tag_song_with_middle_missing_does_not_crash():
    # time thirds drop an empty `middle` (a long instrumental break)
    lines = [(i * 1000, f"early line number {i} here") for i in range(8)]
    lines += [(90_000 + i * 1000, f"late line number {i} here") for i in range(8)]
    tags = L.tag_song(_first_label, "A — B", lines)
    assert "middle" not in tags and {"start", "end"} <= set(tags)


def _one(label, labels):
    return {k: (1.0 if k == label else 0.0) for k in labels}


def _tags(outlook, flags=None):
    seg = {"outlook": _one(outlook, L.GROUPS["outlook"][1])}
    song = {g: _one("absent", L.SEVERITY_LEVELS) for g in L.SEVERITY}
    for g, level in (flags or {}).items():
        song[g] = _one(level, L.SEVERITY_LEVELS)
    return {"song": song, "start": seg, "end": seg}


def test_heartbreak_is_dark_mood_but_not_heavy():
    s = L.scores(_tags("despair"))
    assert (s["mood_darkness"], s["heavy_content"], s["glorifying"]) == (1.0, 0.0, 0.0)


def test_glorifying_drug_song_is_flagged_but_not_dark_mood():
    # pilot v3c: Texas Tea -- all its outlook mass on harm_as_fun
    s = L.scores(_tags("harm_as_fun", {"sev_drugs_alcohol": "theme"}))
    assert s["glorifying"] == 1.0
    assert abs(s["heavy_content"] - 2 / 3) < 1e-9
    assert s["mood_darkness"] == 0.0


def test_worship_is_light():
    assert L.scores(_tags("untroubled")) == {
        "mood_darkness": 0.0, "end_mood_darkness": 0.0, "heavy_content": 0.0,
        "severity": {g: 0.0 for g in L.SEVERITY}, "glorifying": 0.0}


def test_no_lines_set_has_no_scores():
    assert L.scores({}) is None


def test_tag_vector_weights_hook_like_the_body_together():
    tags = {"song": {"mode": {"story": 1.0}},
            "hook": {"emotion": {"joy": 1.0}},
            "start": {"emotion": {"sadness": 1.0}},
            "end": {"emotion": {"sadness": 1.0}}}
    v = L.tag_vector(tags)
    assert v["emotion:joy"] == 0.5 and v["emotion:sadness"] == 0.5
    assert v["mode:story"] == 1.0


# Korean and Japanese lyrics (laya_tune/docs/corpus-spec.md): the
# segmenter was ASCII-only and dropped every Hangul and kana line as empty.

KO_VERSE = [f"너를 처음 만난 그날 밤 {w}" for w in
            ("하늘", "바다", "구름", "바람", "노을", "별빛", "골목", "기차", "편지", "우산",
             "계절", "창문", "거리", "새벽")]
JA_CHORUS = ["沈むように溶けてゆくように", "二人だけの空が広がる夜に",
             "さよならだけだった", "その一言で全てが分かった"]


def test_korean_and_japanese_lines_survive_cleaning():
    assert [t for _, t in L.lines_from_text("\n".join(KO_VERSE))] == KO_VERSE
    assert [t for _, t in L.lines_from_text("\n".join(JA_CHORUS))] == JA_CHORUS


def test_a_repeated_japanese_chorus_is_the_hook():
    verse = [f"知らない街の{c}を歩いた日々の記憶" for c in "春夏秋冬朝昼夕夜雨雪風空月星"]
    lines = L.lines_from_text("\n".join(JA_CHORUS + verse + JA_CHORUS))
    segs = L.segments(lines)
    assert [t for _, t in segs["hook"]] == JA_CHORUS
    assert set(segs) == {"hook", "start", "middle", "end"}


def test_a_short_repeated_kana_chant_is_not_a_hook_line():
    lines = L.lines_from_text("\n".join(["ラララ"] * 4 + JA_CHORUS * 2 + KO_VERSE))
    assert "ラララ" not in [t for _, t in L.segments(lines)["hook"]]


def test_english_keys_are_unchanged():
    assert L.key("My album (my album)!") == "my album"
    assert L.is_chant("Oh-oh-oh (by the bonfire)") and not L.is_chant("And we all sing, oh-oh")
