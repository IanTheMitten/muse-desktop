"""Download manifest weights for the given keys into DIR (files land at DIR/weights/<key>/...)."""
import argparse
import sys
from pathlib import Path

from muse import smart


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keys", required=True, help="comma-separated, e.g. mert,minilm")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    prefixes = tuple(f"weights/{k}/" for k in args.keys.split(","))
    wanted = [f for f in map(lambda e: smart.ManifestFile(**e), smart.load_manifest()["weights"]) if f.path.startswith(prefixes)]
    if not wanted:
        print(f"no manifest weights match {args.keys}")
        return 1
    for f in wanted:
        print(f.path, flush=True)
        smart.download(f, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
