"""`create_app`'s art wiring: the chain behind `run_download` and the one
behind a retried backfill both lead with Cover Art Archive then iTunes, and
share one `ItunesArt` (one rate limiter). Lifespan driven, never run."""

import asyncio

from muse.api import create_app
from muse.config import Config


class NoAnswerMb:
    def get(self, path, params):
        return None


def _config(tmp_path):
    return Config(
        root=tmp_path, library_db=tmp_path / "library.sqlite3",
        manifest=tmp_path / "manifest.json", catalog=tmp_path / "catalog.sqlite3",
        audio_features=tmp_path / "features.sqlite3",
        music_root=tmp_path / "MUSIC", art_root=tmp_path / "art",
        web_dist=tmp_path / "web", backup_dir=tmp_path / "backups",
        backup_target=None, port=8080, host="127.0.0.1",
        art_backfill=True, embed_batch=False, content_embed=False,
        catalog_freeze="2025-11-13",
    )


def test_both_chains_lead_with_caa_then_itunes_and_share_one_itunes(
    tmp_path, monkeypatch
):
    import muse.api as api_module
    from muse.catalog import EmptyCatalog as RealEmptyCatalog

    chains: list[list] = []
    real_chain = api_module.ChainedArtProvider

    def recording_chain(providers):
        chains.append(list(providers))
        return real_chain(providers)

    monkeypatch.setattr(api_module, "ChainedArtProvider", recording_chain)
    monkeypatch.setattr("muse.api._open_catalog", lambda config: RealEmptyCatalog())

    async def no_backfill(*args, **kwargs):
        return None

    # no worker thread may outlive the test's connection
    monkeypatch.setattr(api_module, "run_art_backfill", no_backfill)

    app = create_app(_config(tmp_path), mb=NoAnswerMb())

    async def drive():
        async with app.router.lifespan_context(app):
            return None

    asyncio.run(drive())

    names = [[type(p).__name__ for p in chain] for chain in chains]
    assert sorted(names, key=len) == [
        ["CoverArtArchive", "ItunesArt"],
        ["CoverArtArchive", "ItunesArt", "YtdlpThumbnailArt"],
    ]
    first, second = sorted(chains, key=len)
    assert first[1] is second[1]
