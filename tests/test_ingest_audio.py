from pathlib import Path

import pytest

from muse.ingest import AudioResult, YtDlpAudioSource, safe_component
from muse.remux import NO_M4A


@pytest.fixture
def fakes(monkeypatch):
    import muse.ingest as ingest_mod
    import muse.ytdlp as ytdlp_mod

    seen = {"queries": [], "remuxed": []}

    def fake_download(binary, query, out_dir, base_name, **kwargs):
        seen["queries"].append(query)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{base_name}.m4a").write_bytes(b"fake audio")
        return {"id": "yt_abc123", "duration": 200.0}

    monkeypatch.setattr(ytdlp_mod, "download", fake_download)
    monkeypatch.setattr(ingest_mod, "remux_m4a", seen["remuxed"].append)
    return seen


def test_fetch_writes_file_and_returns_source_site_id(tmp_path, fakes):
    source = YtDlpAudioSource(Path("yt-dlp"))
    result = source.fetch(
        query="ytsearch1:Radiohead - Airbag",
        out_dir=tmp_path,
        base_name="01 - Airbag",
    )
    assert isinstance(result, AudioResult)
    assert result.file_path.suffix == ".m4a"
    assert result.source_site_id == "yt_abc123"
    assert result.duration_s == 200.0
    assert fakes["remuxed"] == [result.file_path]
    assert fakes["queries"] == ["ytsearch1:Radiohead - Airbag"]


def test_fetch_uses_explicit_url_when_given(tmp_path, fakes):
    source = YtDlpAudioSource(Path("yt-dlp"))
    source.fetch(
        query="https://youtube.com/watch?v=xyz",
        out_dir=tmp_path,
        base_name="track",
    )
    assert fakes["queries"] == ["https://youtube.com/watch?v=xyz"]


def test_fetch_raises_no_m4a_when_nothing_is_written(tmp_path, monkeypatch):
    import muse.ytdlp as ytdlp_mod

    monkeypatch.setattr(ytdlp_mod, "download", lambda *a, **k: {"id": "x"})
    source = YtDlpAudioSource(Path("yt-dlp"))
    with pytest.raises(RuntimeError) as exc:
        source.fetch(query="q", out_dir=tmp_path, base_name="track")
    assert str(exc.value) == NO_M4A


def test_safe_component_strips_invalid_chars():
    # Each invalid char becomes its own "_" (matches music_downloader.py's
    # safe_component exactly): ":" -> "_", each '"' -> "_", "?" -> "_".
    assert safe_component('Song: "Title"?') == "Song_ _Title__"


def test_safe_component_collapses_whitespace_and_trims_trailing_dot():
    assert safe_component("  A   B  . ") == "A B"
