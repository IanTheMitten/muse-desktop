from muse import settings


def test_missing_file_gives_defaults(tmp_path):
    assert settings.load(tmp_path / "settings.json") == settings.DEFAULTS


def test_corrupt_json_gives_defaults(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text("{nope")
    assert settings.load(p) == settings.DEFAULTS


def test_round_trip_leaves_no_tmp_and_keeps_unknown_keys(tmp_path):
    p = tmp_path / "sub" / "settings.json"
    settings.save(p, {"music_folders": ["/m"], "extra": 1})
    got = settings.load(p)
    assert got["music_folders"] == ["/m"] and got["extra"] == 1
    assert got["downloader_unlocked"] is False
    assert not p.with_suffix(".json.tmp").exists()
    assert settings.music_folders(p) == [tmp_path.parent.joinpath("/m")]
