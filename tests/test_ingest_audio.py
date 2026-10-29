from pathlib import Path

from muse.ingest import AudioResult, YtDlpAudioSource, safe_component, tag_m4a


class FakeYdl:
    """Stands in for yt_dlp.YoutubeDL — records the query it was given and
    writes a tiny placeholder file where yt-dlp's postprocessor would."""

    last_query = None

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, query, download=True):
        FakeYdl.last_query = query
        out_path = Path(self.opts["outtmpl"].replace("%(ext)s", "m4a"))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"fake audio")
        return {"id": "yt_abc123"}


def test_fetch_writes_file_and_returns_source_site_id(tmp_path, monkeypatch):
    import muse.ingest as ingest_mod

    monkeypatch.setattr(ingest_mod, "YoutubeDL", FakeYdl)
    source = YtDlpAudioSource()
    result = source.fetch(
        query="ytsearch1:Radiohead - Airbag",
        out_dir=tmp_path,
        base_name="01 - Airbag",
    )
    assert isinstance(result, AudioResult)
    assert result.file_path.exists()
    assert result.file_path.suffix == ".m4a"
    assert result.source_site_id == "yt_abc123"
    assert FakeYdl.last_query == "ytsearch1:Radiohead - Airbag"


def test_fetch_uses_explicit_url_when_given(tmp_path, monkeypatch):
    import muse.ingest as ingest_mod

    monkeypatch.setattr(ingest_mod, "YoutubeDL", FakeYdl)
    source = YtDlpAudioSource()
    source.fetch(
        query="https://youtube.com/watch?v=xyz",
        out_dir=tmp_path,
        base_name="track",
    )
    assert FakeYdl.last_query == "https://youtube.com/watch?v=xyz"


def test_safe_component_strips_invalid_chars():
    # Each invalid char becomes its own "_" (matches music_downloader.py's
    # safe_component exactly): ":" -> "_", each '"' -> "_", "?" -> "_".
    assert safe_component('Song: "Title"?') == "Song_ _Title__"


def test_safe_component_collapses_whitespace_and_trims_trailing_dot():
    assert safe_component("  A   B  . ") == "A B"
