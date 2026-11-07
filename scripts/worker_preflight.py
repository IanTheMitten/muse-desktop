"""Task 3 preflight: every worker model loads from a local dir and answers once;
PyAV decodes every smoke fixture. Run:
    uv run --extra embed python scripts/worker_preflight.py
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

REPOS = {
    "mert": "m-a-p/MERT-v1-95M",
    "clap": "laion/larger_clap_music_and_speech",
}
LAYA = Path.home() / "models/laya-muse-v4"
rows = []


def local_dir(repo: str) -> str:
    from huggingface_hub import snapshot_download

    try:
        return snapshot_download(repo, local_files_only=True)
    except Exception as e:  # fallback: model id
        print(f"NOTE: no local snapshot for {repo} ({e!r}); using model id")
        return repo


def timed(name, load, infer):
    t0 = time.time()
    obj = load()
    t1 = time.time()
    out = infer(obj)
    t2 = time.time()
    shape = getattr(out, "shape", None) or (len(out) if hasattr(out, "__len__") else "?")
    rows.append((name, t1 - t0, t2 - t1, shape))
    return obj


def decode(path, rate):
    import av

    resampler = av.AudioResampler(format="flt", layout="mono", rate=rate)
    chunks = []
    with av.open(str(path)) as c:
        for frame in c.decode(audio=0):
            for f in resampler.resample(frame):
                chunks.append(f.to_ndarray().reshape(-1))
        for f in resampler.resample(None):
            chunks.append(f.to_ndarray().reshape(-1))
    return np.concatenate(chunks).astype(np.float32)


def main():
    import torch

    tone = decode(ROOT / "tests/fixtures/smoke/tone.wav", 24000)
    d = {k: local_dir(r) for k, r in REPOS.items()}

    def mert_load():
        from transformers import AutoModel, Wav2Vec2FeatureExtractor

        m = AutoModel.from_pretrained(d["mert"], trust_remote_code=True).eval()
        p = Wav2Vec2FeatureExtractor.from_pretrained(d["mert"], trust_remote_code=True)
        return m, p

    def mert_inf(mp):
        m, p = mp
        x = p(tone[: 24000 * 5], sampling_rate=24000, return_tensors="pt")
        # transformers 5.22 + MERT remote code: output_hidden_states=True returns
        # hidden_states=None (the custom forward bypasses capture_outputs), so
        # collect the 13 states with hooks: layer-0 input, then each layer output.
        states = []
        first = lambda o: o[0] if isinstance(o, tuple) else o
        hs = [m.encoder.layers[0].register_forward_pre_hook(
            lambda mod, a, kw: states.append(a[0] if a else kw["hidden_states"]), with_kwargs=True)]
        hs += [layer.register_forward_hook(lambda mod, a, o: states.append(first(o)))
               for layer in m.encoder.layers]
        try:
            with torch.no_grad():
                m(**x)
        finally:
            for h in hs:
                h.remove()
        assert len(states) == 13, len(states)
        return torch.stack(states).squeeze(1).mean(1).mean(0)

    timed("mert", mert_load, mert_inf)

    def clap_load():
        from transformers import ClapModel, ClapProcessor

        return ClapModel.from_pretrained(d["clap"]).eval(), ClapProcessor.from_pretrained(d["clap"])

    def clap_inf(mp):
        m, p = mp
        a = decode(ROOT / "tests/fixtures/smoke/tone.wav", 48000)
        x = p(audio=[a[:48000]], sampling_rate=48000, return_tensors="pt")
        with torch.no_grad():
            return m.get_audio_features(**x).pooler_output[0]

    timed("clap", clap_load, clap_inf)

    def laya_load():
        import laya

        return laya.load(str(LAYA), device="cpu")

    q = {"mood": {"type": "choice", "instructions": "Pick the mood.", "criteria": ["happy", "sad"]}}
    timed("laya", laya_load, lambda a: a.system_one({"lyrics": "sunshine and joy"}, q))
    print(rows[-1])

    print("\nmodel      load_s  infer_s  shape")
    for n, a, b, s in rows:
        print(f"{n:<10} {a:6.1f}  {b:7.2f}  {s}")

    print("\nformat  rate   samples  seconds")
    for f in sorted((ROOT / "tests/fixtures/smoke").glob("tone.*")):
        for rate in (16000, 24000, 48000):
            n = len(decode(f, rate))
            print(f"{f.suffix[1:]:<6} {rate:>6} {n:>8} {n / rate:7.3f}")


main()
