"""The Cadence contract: version, surface list, and the OpenAPI snapshot.

See docs/contracts/cadence.md and ADR 0007. The surface is the handful of
operations Cadence calls; the snapshot pins their normalized OpenAPI shape so
a change to any of them fails a test until the version is bumped.
"""

import argparse
import copy
import json
import sys
from pathlib import Path

from pydantic import BaseModel

CONTRACT_VERSION = "1.0"

CADENCE_SURFACE: tuple[tuple[str, str], ...] = (
    ("get", "/api/contract"),
    ("get", "/api/library/search"),
    ("get", "/api/library/search/vibe"),
    ("post", "/api/plays"),
    ("patch", "/api/plays/{play_id}"),
    ("post", "/api/plays/{play_id}/close"),
    ("post", "/api/ratings"),
    ("get", "/api/tracks/{track_rowid}/stream"),
)

SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / "docs" / "contracts" / "cadence.openapi.json"

_PROSE_KEYS = {"description", "summary", "title", "operationId"}
_REF_PREFIX = "#/components/schemas/"


class ContractVersion(BaseModel):
    version: str


def _normalize(node, schemas):
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith(_REF_PREFIX):
            return _normalize(schemas[ref[len(_REF_PREFIX):]], schemas)
        return {
            k: _normalize(v, schemas)
            for k, v in node.items()
            if not (k in _PROSE_KEYS and isinstance(v, str))
        }
    if isinstance(node, list):
        return [_normalize(v, schemas) for v in node]
    return node


def surface_snapshot(openapi: dict, surface: tuple[tuple[str, str], ...] = CADENCE_SURFACE) -> dict:
    schemas = openapi.get("components", {}).get("schemas", {})
    paths: dict = {}
    for method, path in surface:
        paths.setdefault(path, {})[method] = _normalize(openapi["paths"][path][method], schemas)
    return {"version": CONTRACT_VERSION, "paths": paths}


def write_snapshot(openapi: dict, path: Path = SNAPSHOT_PATH) -> bool:
    new = surface_snapshot(openapi)
    if path.exists():
        old = json.loads(path.read_text())
        if old["paths"] != new["paths"] and old["version"] == CONTRACT_VERSION:
            return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(new, indent=2, sort_keys=True) + "\n")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="muse.contract")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true")
    group.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)

    from .api import create_app

    openapi = create_app().openapi()
    if args.check:
        committed = json.loads(SNAPSHOT_PATH.read_text())
        if committed == surface_snapshot(openapi):
            return 0
        print("Cadence surface differs from docs/contracts/cadence.openapi.json", file=sys.stderr)
        return 1
    if write_snapshot(openapi):
        return 0
    print(
        f"Surface changed but CONTRACT_VERSION is still {CONTRACT_VERSION}; "
        "bump it in muse/contract.py first (docs/contracts/cadence.md)",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
