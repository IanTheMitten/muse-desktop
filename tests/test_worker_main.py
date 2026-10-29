import io
import json
import subprocess
import sys

from muse.worker_main import real_handlers, serve


def run(lines, handlers):
    out = io.StringIO()
    serve(io.StringIO(lines), out, handlers)
    return [json.loads(x) for x in out.getvalue().splitlines()]


def boom(_):
    raise ValueError("nope")


H = {"echo": lambda a: a["x"], "boom": boom}


def test_ok_and_order():
    got = run('{"id":1,"op":"echo","x":"é"}\n{"id":2,"op":"echo","x":2}\n', H)
    assert got == [{"id": 1, "ok": "é"}, {"id": 2, "ok": 2}]


def test_hello_lists_ops():
    assert run('{"id":1,"op":"hello"}\n', H) == [
        {"id": 1, "ok": {"protocol": 1, "ops": ["boom", "echo"]}}
    ]


def test_unknown_op():
    assert run('{"id":3,"op":"zzz"}\n', H) == [{"id": 3, "error": "unknown op zzz"}]


def test_handler_error():
    assert run('{"id":4,"op":"boom"}\n', H) == [{"id": 4, "error": "ValueError: nope"}]


def test_bad_requests():
    got = run('{bad\n[1]\n{"op":"echo"}\n{"id":"a","op":"echo"}\n', H)
    assert len(got) == 4
    assert all(g["id"] is None and g["error"].startswith("bad request") for g in got)


def test_blank_lines_ignored():
    assert run("\n  \n\n", H) == []


def test_real_handlers_keys_and_no_torch():
    code = (
        "import sys, tempfile, pathlib;"
        "from muse.worker_main import real_handlers;"
        "h = real_handlers(pathlib.Path(tempfile.mkdtemp()));"
        "print(sorted(h)); print('torch' in sys.modules)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout.split("\n")
    assert out[0] == str(
        sorted(["clap_audio", "clap_text", "minilm", "audio_va", "lyrics_va", "mert", "laya"])
    )
    assert out[1] == "False"


def test_laya_missing_weights(tmp_path):
    got = run('{"id":1,"op":"laya","state":{},"questions":{}}\n', real_handlers(tmp_path))
    assert "Laya weights missing" in got[0]["error"]
