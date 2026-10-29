"""The Cadence contract (docs/contracts/cadence.md, ADR 0007).

A failure here means a Cadence-facing change: see the doc's "Changing the
surface".
"""

import copy
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import muse.contract as contract
from muse.api import ENDED_REASONS, create_app
from muse.config import Config

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def app(tmp_path):
    config = Config(
        root=tmp_path, library_db=tmp_path / "library.sqlite3",
        manifest=tmp_path / "manifest.json", catalog=tmp_path / "catalog.sqlite3",
        audio_features=tmp_path / "features.sqlite3",
        music_root=tmp_path / "MUSIC", art_root=tmp_path / "art",
        web_dist=tmp_path / "web", backup_dir=tmp_path / "backups",
        backup_target=None, port=8080, host="127.0.0.1",
        art_backfill=False, embed_batch=False, content_embed=False,
        catalog_freeze="2025-11-13",
    )
    return create_app(config)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def test_contract_route_returns_the_current_version(client):
    response = client.get("/api/contract")
    assert response.status_code == 200
    assert response.json()["version"] == contract.CONTRACT_VERSION
    assert re.fullmatch(r"\d+\.\d+", contract.CONTRACT_VERSION)


def test_contract_route_is_on_the_surface():
    assert ("get", "/api/contract") in contract.CADENCE_SURFACE


def test_every_surface_operation_exists(app):
    paths = app.openapi()["paths"]
    for method, path in contract.CADENCE_SURFACE:
        assert method in paths[path]


def test_live_surface_matches_the_committed_snapshot(app):
    committed = json.loads(contract.SNAPSHOT_PATH.read_text())
    assert committed == contract.surface_snapshot(app.openapi()), (
        "Cadence surface changed. Bump CONTRACT_VERSION in muse/contract.py, "
        "run: uv run python -m muse.contract --write, "
        "update docs/contracts/cadence.md"
    )


def test_snapshot_records_the_current_version():
    committed = json.loads(contract.SNAPSHOT_PATH.read_text())
    assert committed["version"] == contract.CONTRACT_VERSION


def test_snapshot_has_no_refs_or_prose(app):
    text = json.dumps(contract.surface_snapshot(app.openapi()))
    for forbidden in ("$ref", "operationId", "summary"):
        assert forbidden not in text


def test_prose_does_not_change_the_snapshot(app):
    original = app.openapi()
    changed = copy.deepcopy(original)
    op = changed["paths"]["/api/library/search/vibe"]["get"]
    op["description"] = "changed"
    op["summary"] = "changed"
    op["operationId"] = "changed"
    assert contract.surface_snapshot(changed) == contract.surface_snapshot(original)


def test_a_parameter_default_change_does_change_the_snapshot(app):
    original = app.openapi()
    changed = copy.deepcopy(original)
    params = changed["paths"]["/api/library/search"]["get"]["parameters"]
    limit = next(p for p in params if p["name"] == "limit")
    limit["schema"]["default"] = 61
    assert contract.surface_snapshot(changed) != contract.surface_snapshot(original)


def test_write_refuses_a_surface_change_without_a_bump(app, tmp_path):
    path = tmp_path / "snap.json"
    path.write_text(json.dumps({"version": contract.CONTRACT_VERSION, "paths": {}}))
    before = path.read_text()
    assert contract.write_snapshot(app.openapi(), path) is False
    assert path.read_text() == before


def test_write_accepts_a_surface_change_with_a_bump(app, tmp_path):
    path = tmp_path / "snap.json"
    path.write_text(json.dumps({"version": "0.9", "paths": {}}))
    assert contract.write_snapshot(app.openapi(), path) is True
    assert json.loads(path.read_text()) == contract.surface_snapshot(app.openapi())


def test_ended_reasons_are_pinned():
    assert ENDED_REASONS == frozenset(
        {"track_end", "user_next", "user_prev", "new_selection", "pause_timeout", "lost"}
    ), (
        "Adding or renaming an ended_reason is a MAJOR Cadence contract change "
        "(docs/contracts/cadence.md, ADR 0001)"
    )


def test_contract_doc_changelog_names_the_current_version():
    text = (ROOT / "docs/contracts/cadence.md").read_text()
    assert f"| {contract.CONTRACT_VERSION} |" in text
