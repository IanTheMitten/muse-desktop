"""Smoke the frozen worker: start the exe, send each op, check dims, close stdin, expect exit 0 within 20 s."""
import argparse
import json
import subprocess
import sys
from pathlib import Path

DIMS = {"mert": 768, "clap_audio": 512, "minilm": 384}
PATH_OPS = {"mert", "clap_audio"}
QUESTION = {"mood": {"type": "choice", "instructions": "Pick the mood.", "criteria": ["happy", "sad"]}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path, required=True)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--ops", required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    args = parser.parse_args(argv)
    ops = args.ops.split(",")
    proc = subprocess.Popen(
        [str(args.worker), "--weights", str(args.weights)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8",
    )

    def ask(rid: int, op: str) -> object:
        req = {"id": rid, "op": op}
        if op in PATH_OPS:
            req["path"] = str(args.fixture.resolve())
        elif op == "minilm":
            req["text"] = "sunshine and joy"
        elif op == "laya":
            req.update(state={"lyrics": "sunshine and joy"}, questions=QUESTION)
        proc.stdin.write(json.dumps(req) + "\n")
        proc.stdin.flush()
        reply = json.loads(proc.stdout.readline() or "null")
        if not reply or reply.get("id") != rid or "ok" not in reply:
            raise RuntimeError(f"{op}: {reply}")
        return reply["ok"]

    try:
        for rid, op in enumerate(ops, 1):
            ok = ask(rid, op)
            if op == "hello":
                missing = [o for o in ops if o != "hello" and o not in ok["ops"]]
                if ok["protocol"] != 1 or missing:
                    raise RuntimeError(f"hello: {ok} lacks {missing}")
            elif op in DIMS:
                if len(ok) != DIMS[op]:
                    raise RuntimeError(f"{op}: dim {len(ok)} != {DIMS[op]}")
            elif op == "laya":
                if not isinstance(ok, dict) or "mood" not in json.dumps(ok):
                    raise RuntimeError(f"laya: {ok}")
            print(f"ok {op}", flush=True)
        proc.stdin.close()
        code = proc.wait(timeout=20)
        if code != 0:
            raise RuntimeError(f"exit code {code}")
    except Exception as error:
        proc.kill()
        print(f"WORKER-SMOKE-FAIL: {error}")
        return 1
    print("WORKER-SMOKE-OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
