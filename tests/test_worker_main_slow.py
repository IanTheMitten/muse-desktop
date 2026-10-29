import json
import subprocess
import sys
from pathlib import Path

import pytest

LAYA = Path.home() / "models" / "laya-muse-v4"
TONE = Path(__file__).parent / "fixtures" / "smoke" / "tone.wav"


@pytest.mark.slow
def test_worker_serves_real_models(tmp_path):
    if not LAYA.is_dir():
        pytest.skip("no local laya-muse-v4")
    (tmp_path / "laya").symlink_to(LAYA)
    proc = subprocess.Popen(
        [sys.executable, "-m", "muse.worker_main", "--weights", str(tmp_path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8",
    )

    def ask(i, op, **args):
        proc.stdin.write(json.dumps({"id": i, "op": op, **args}) + "\n")
        proc.stdin.flush()
        return json.loads(proc.stdout.readline())

    hello = ask(1, "hello")
    assert hello["ok"]["protocol"] == 1 and "mert" in hello["ok"]["ops"]
    assert len(ask(2, "mert", path=str(TONE))["ok"]) == 768
    assert len(ask(3, "clap_audio", path=str(TONE))["ok"]) == 512
    q = {"mood": {"type": "choice", "instructions": "Pick the mood.", "criteria": ["happy", "sad"]}}
    reply = ask(4, "laya", state={"lyrics": "sunshine and joy"}, questions=q)
    assert "mood" in reply["ok"]["answers"], reply
    proc.stdin.close()
    assert proc.wait(timeout=20) == 0
