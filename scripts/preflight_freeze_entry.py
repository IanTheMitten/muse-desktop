"""Task 3 freeze probe: the worker's imports and two model loads, frozen."""
from pathlib import Path

import av  # noqa: F401
import laya
import numpy as np
import sentence_transformers  # noqa: F401
import torch
import transformers  # noqa: F401
from huggingface_hub import snapshot_download
from transformers import AutoModel, Wav2Vec2FeatureExtractor

src = snapshot_download("m-a-p/MERT-v1-95M", local_files_only=True)
model = AutoModel.from_pretrained(src, trust_remote_code=True).eval()
proc = Wav2Vec2FeatureExtractor.from_pretrained(src, trust_remote_code=True)
x = proc(np.zeros(24000, dtype=np.float32), sampling_rate=24000, return_tensors="pt")
with torch.no_grad():
    model(**x)

agent = laya.load(str(Path.home() / "models/laya-muse-v4"), device="cpu")
agent.system_one(
    {"lyrics": "sunshine"},
    {"mood": {"type": "choice", "instructions": "Pick.", "criteria": ["happy", "sad"]}},
)
print("FREEZE-OK")
