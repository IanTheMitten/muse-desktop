"""Write muse/smart_manifest.json: pinned weight files with sha256 and size.

Network: Hugging Face tree API for LFS files, a download for small non-LFS
files. Laya files are hashed from a local checkout. An existing `workers`
object is kept.
"""

import argparse
import hashlib
import json
from pathlib import Path

import requests

PINNED = {
    "mert": ("m-a-p/MERT-v1-95M", "12af15fef9d0ac838c3f475bfbbf26d2060dd4f5", [
        "config.json", "configuration_MERT.py", "modeling_MERT.py",
        "preprocessor_config.json", "pytorch_model.bin"]),
    "clap": ("laion/larger_clap_music_and_speech", "195c3a3e68faebb3e2088b9a79e79b43ddbda76b", [
        "config.json", "merges.txt", "preprocessor_config.json", "pytorch_model.bin",
        "special_tokens_map.json", "tokenizer.json", "tokenizer_config.json", "vocab.json"]),
    "minilm": ("sentence-transformers/all-MiniLM-L6-v2", "1110a243fdf4706b3f48f1d95db1a4f5529b4d41", [
        "1_Pooling/config.json", "config.json", "config_sentence_transformers.json",
        "model.safetensors", "modules.json", "sentence_bert_config.json",
        "special_tokens_map.json", "tokenizer.json", "tokenizer_config.json", "vocab.txt"]),
    "audio_va": ("audeering/wav2vec2-large-robust-12-ft-emotion-msp-dim", "6eba34a2485ea31cb03600241787c3a5edab8626", [
        "LICENSE", "config.json", "model.safetensors", "preprocessor_config.json", "vocab.json"]),
    "lyrics_va": ("j-hartmann/emotion-english-distilroberta-base", "0e1cd914e3d46199ed785853e12b57304e04178b", [
        "config.json", "merges.txt", "pytorch_model.bin", "special_tokens_map.json",
        "tokenizer.json", "tokenizer_config.json", "vocab.json"]),
    "mms": ("MahmoudAshraf/mms-300m-1130-forced-aligner", "49402e9577b1158620820667c218cd494cc44486", [
        "config.json", "model.safetensors", "vocab.json"]),
}
RAW = [
    {"path": "weights/schufo/model_parameters.pth",
     "url": "https://raw.githubusercontent.com/schufo/lyrics-aligner/72be8a14b9126c201e0b8920513764c44ccd3f9e/model_parameters.pth",
     "sha256": "cf907661b6e3c477b24cf61f0bdf2a08aa1cfdc1e326f112861559a81785469e", "size": 40357640},
    {"path": "weights/schufo/cmudict.dict",
     "url": "https://raw.githubusercontent.com/cmusphinx/cmudict/74790861f652b15e4ac49015a90074ad62a27690/cmudict.dict",
     "sha256": "81917843c7f44ce2b094ac63873c2c7a4cf802040792c455ba3ca406891c3d22", "size": 3618488},
    {"path": "weights/schufo/checkpoint20.npz",
     "url": "https://raw.githubusercontent.com/Kyubyong/g2p/c6439c274c42b9724a7fee1dc07ca6a4c68a0538/g2p_en/checkpoint20.npz",
     "sha256": "b8af35e4596d8dd5836dfd3fe9b2ba4f97b9c311efe8879544cbcfcbd566d8c6", "size": 3342298},
]
LAYA_FILES = [
    "encoder/config.json", "model.safetensors", "rl_agent_config.json",
    "tokenizer/tokenizer.json", "tokenizer/tokenizer_config.json",
]


def sha_stream(chunks) -> tuple[str, int]:
    h, n = hashlib.sha256(), 0
    for c in chunks:
        h.update(c)
        n += len(c)
    return h.hexdigest(), n


def hf_entries(key: str, repo: str, rev: str, files: list[str]) -> list[dict]:
    r = requests.get(f"https://huggingface.co/api/models/{repo}/tree/{rev}?recursive=1", timeout=60)
    r.raise_for_status()
    tree = {t["path"]: t for t in r.json()}
    out = []
    for f in files:
        url = f"https://huggingface.co/{repo}/resolve/{rev}/{f}"
        lfs = tree[f].get("lfs")
        if lfs:
            sha, size = lfs["oid"], lfs["size"]
        else:
            with requests.get(url, stream=True, timeout=60) as d:
                d.raise_for_status()
                sha, size = sha_stream(d.iter_content(1 << 20))
        out.append({"path": f"weights/{key}/{f}", "url": url, "sha256": sha, "size": size})
    return out


def laya_entries(laya_dir: Path, repo: str) -> list[dict]:
    out = []
    for f in LAYA_FILES:
        with open(laya_dir / f, "rb") as fh:
            sha, size = sha_stream(iter(lambda: fh.read(1 << 20), b""))
        out.append({
            "path": f"weights/laya/{f}",
            "url": f"https://huggingface.co/{repo}/resolve/main/{f}",
            "sha256": sha,
            "size": size,
        })
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--laya-dir", type=Path, default=Path("~/models/laya-muse-v4").expanduser())
    ap.add_argument("--laya-repo", default="IanTheMitten/laya-muse-v4")
    ap.add_argument("--out", type=Path, default=Path("muse/smart_manifest.json"))
    args = ap.parse_args()
    workers = {}
    if args.out.exists():
        workers = json.loads(args.out.read_text()).get("workers", {})
    weights = []
    for key, (repo, rev, files) in PINNED.items():
        weights += hf_entries(key, repo, rev, files)
    weights += RAW
    weights += laya_entries(args.laya_dir, args.laya_repo)
    args.out.write_text(json.dumps({"version": 2, "weights": weights, "workers": workers}, indent=2) + "\n")
    print(f"{len(weights)} files, {sum(w['size'] for w in weights)} bytes")


if __name__ == "__main__":
    main()
