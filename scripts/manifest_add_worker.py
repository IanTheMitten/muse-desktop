"""Usage: manifest_add_worker.py <triple> <zip> <url> -- record a published worker zip."""

import hashlib
import json
import sys
from pathlib import Path

MANIFEST = Path(__file__).resolve().parent.parent / "muse" / "smart_manifest.json"


def main(triple: str, zip_path: str, url: str) -> None:
    h = hashlib.sha256()
    with open(zip_path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    manifest = json.loads(MANIFEST.read_text())
    manifest["workers"][triple] = {
        "path": f"muse-worker-{triple}.zip",
        "url": url,
        "sha256": h.hexdigest(),
        "size": Path(zip_path).stat().st_size,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*sys.argv[1:])
