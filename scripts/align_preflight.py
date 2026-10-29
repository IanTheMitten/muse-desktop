"""M4b Task 1 preflight: schufo weights load into its model, MMS-FA loads from three
files, uroman and py3langid work offline; timings for 240 s of audio. Run:
    uv run --extra embed python scripts/align_preflight.py
"""
import importlib.util
import resource
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import requests  # noqa: E402
import torch  # noqa: E402

from muse import smart  # noqa: E402
from muse.audio_decode import decode_mono  # noqa: E402
from muse.smart import ManifestFile  # noqa: E402

CACHE = Path.home() / ".cache" / "muse-test-weights"
SCHUFO = "https://raw.githubusercontent.com/schufo/lyrics-aligner/72be8a14b9126c201e0b8920513764c44ccd3f9e/model.py"
RAW = {
    "model_parameters.pth": (
        "https://raw.githubusercontent.com/schufo/lyrics-aligner/72be8a14b9126c201e0b8920513764c44ccd3f9e/model_parameters.pth",
        "cf907661b6e3c477b24cf61f0bdf2a08aa1cfdc1e326f112861559a81785469e", 40357640),
    "cmudict.dict": (
        "https://raw.githubusercontent.com/cmusphinx/cmudict/74790861f652b15e4ac49015a90074ad62a27690/cmudict.dict",
        "81917843c7f44ce2b094ac63873c2c7a4cf802040792c455ba3ca406891c3d22", 3618488),
    "checkpoint20.npz": (
        "https://raw.githubusercontent.com/Kyubyong/g2p/c6439c274c42b9724a7fee1dc07ca6a4c68a0538/g2p_en/checkpoint20.npz",
        "b8af35e4596d8dd5836dfd3fe9b2ba4f97b9c311efe8879544cbcfcbd566d8c6", 3342298),
}
TONE = ROOT / "tests/fixtures/smoke/tone.wav"
rows = []


def rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # KB on linux


def row(name: str, t0: float, note: str = "") -> None:
    rows.append((name, time.time() - t0, rss_mb(), note))
    print(f"[{name}] {rows[-1][1]:.1f}s rss={rss_mb():.0f}MB {note}", flush=True)


# (a) schufo files
t0 = time.time()
paths = {
    n: smart.download(ManifestFile(path=f"weights/schufo/{n}", url=u, sha256=h, size=s), CACHE)
    for n, (u, h, s) in RAW.items()
}
row("schufo-download", t0)

# (b) schufo model
t0 = time.time()
src = ROOT / "build" / "schufo-src"
src.mkdir(parents=True, exist_ok=True)
(src / "model.py").write_text(requests.get(SCHUFO, timeout=60).text)
spec = importlib.util.spec_from_file_location("schufo_model", src / "model.py")
sm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sm)


def stft_forward(self, x):
    n, c, _ = x.size()
    x = x.reshape(n * c, -1)
    f = torch.view_as_real(torch.stft(
        x, n_fft=self.n_fft, hop_length=self.n_hop, window=self.window,
        center=self.center, normalized=False, onesided=True,
        pad_mode="reflect", return_complex=True))
    return f.contiguous().view(n, c, self.n_fft // 2 + 1, -1, 2)


sm.STFT.forward = stft_forward
model = sm.InformedOpenUnmix3()
wo = "True"
try:
    state = torch.load(paths["model_parameters.pth"], map_location="cpu", weights_only=True)
except Exception as e:  # recorded, then retried
    print(f"weights_only=True raised: {type(e).__name__}: {e}")
    wo = "False (True raised)"
    state = torch.load(paths["model_parameters.pth"], map_location="cpu", weights_only=False)
model.load_state_dict(state, strict=True)
model.eval()
row("schufo-load", t0, f"weights_only={wo}")


def run_schufo(audio: np.ndarray, tokens: list[int]):
    x = torch.from_numpy(audio.astype(np.float32))[None, None, :]
    t = torch.tensor([tokens], dtype=torch.long)
    with torch.no_grad():
        return model([x, t])[2]


t0 = time.time()
tone = decode_mono(TONE, 16000)
scores = run_schufo(tone, [3, 26, 5, 3, 27, 7, 3, 35, 22, 3, 24, 3])
print("schufo scores shape", tuple(scores.shape))
row("schufo-tone", t0, f"scores={tuple(scores.shape)}")

noise = np.random.default_rng(0).standard_normal(240 * 16000) * 0.1
t0 = time.time()
toks = [3 if i % 4 == 0 else 5 + i % 39 for i in range(1500)]
scores = run_schufo(noise, toks)
print("schufo 240s scores shape", tuple(scores.shape))
row("schufo-240s", t0, f"scores={tuple(scores.shape)}")

# (c) MMS-FA
import huggingface_hub  # noqa: E402
import transformers  # noqa: E402

t0 = time.time()
d = huggingface_hub.snapshot_download(
    "MahmoudAshraf/mms-300m-1130-forced-aligner",
    revision="49402e9577b1158620820667c218cd494cc44486",
    allow_patterns=["config.json", "model.safetensors", "vocab.json"])
row("mms-download", t0)
t0 = time.time()
mms = transformers.Wav2Vec2ForCTC.from_pretrained(d).eval()
row("mms-load", t0)
t0 = time.time()
with torch.no_grad():
    logits = mms(torch.from_numpy(tone.astype(np.float32))[None]).logits
print("mms logits shape", tuple(logits.shape))
assert logits.shape[-1] == 31
row("mms-tone", t0, f"logits={tuple(logits.shape)}")
t0 = time.time()
win = torch.from_numpy(noise[: 34 * 16000].astype(np.float32))
with torch.no_grad():
    for _ in range(2):
        mms(win[None].repeat(4, 1))
row("mms-8x34s", t0, "2 batches of 4")

# (d) uroman
import uroman  # noqa: E402

t0 = time.time()
ur = uroman.Uroman()
row("uroman-init", t0)
t0 = time.time()
for s, c in [("사랑해 너를", "kor"), ("愛してる", "jpn"), ("hello", None)]:
    print("uroman", repr(s), "->", repr(ur.romanize_string(s, lcode=c)))
row("uroman-run", t0)

# (e) py3langid
import py3langid  # noqa: E402

t0 = time.time()
codes = [py3langid.classify(s)[0] for s in
         ["I will always love you, baby", "너를 사랑해 영원히", "君を愛してる ずっと"]]
print("classify codes", codes)
assert codes == ["en", "ko", "ja"], codes
row("langid", t0, str(codes))

print("PREFLIGHT-OK")
