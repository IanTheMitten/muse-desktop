"""Gate 1 check: CLAP energy vs stored audio_va arousal, Spearman rho.

Run on a pre-migration library (before migration 23 drops audio_va). Read-only.
"""
import argparse
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from muse import energy
from muse.config import Config
from muse.embed import unpack_vector
from muse.worker import WorkerClient, worker_command

_SQL = (
    "SELECT e.audio_vec, e.audio_va FROM embeddings e"
    " JOIN tracks t USING (track_rowid)"
    " WHERE t.status = 'done' AND e.audio_vec IS NOT NULL AND e.audio_va IS NOT NULL"
    " ORDER BY e.track_rowid"
)


def check(conn, encode_text) -> dict:
    calm = encode_text(energy.CALM_PROMPT)
    energetic = encode_text(energy.ENERGETIC_PROMPT)
    raws: list[float] = []
    arousals: list[float] = []
    for row in conn.execute(_SQL).fetchall():
        raws.append(energy.raw_energy(unpack_vector(row[0]), calm, energetic))
        arousals.append(unpack_vector(row[1])[1])
    rho = energy.spearman(raws, arousals)
    return {"n": len(raws), "rho": rho, "passed": rho is not None and rho >= energy.GATE_RHO}


def main(argv=None) -> int:
    cfg = Config.from_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(cfg.library_db))
    parser.add_argument("--worker-cmd", default=os.environ.get("MUSE_WORKER_CMD"))
    args = parser.parse_args(argv)

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    client = WorkerClient(worker_command(cfg.models_dir, args.worker_cmd))
    try:
        client.begin()
        result = check(conn, lambda t: client.request("clap_text", text=t))
    finally:
        client.stop()
        conn.close()
    rho = result["rho"]
    print(f"n={result['n']}")
    print(f"rho={'none' if rho is None else f'{rho:.3f}'}")
    print("PASS" if result["passed"] else f"FAIL (gate: rho >= {energy.GATE_RHO})")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
