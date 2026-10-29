"""MERT-v1-95M content embedding (the content leg), loaded from a local dir
when `source` is given. torch/transformers are imported inside `__init__`
only, never at module import."""

from pathlib import Path

from .audio_decode import decode_mono

MERT_MODEL = "m-a-p/MERT-v1-95M"
MERT_SAMPLE_RATE = 24000
_CHUNK_SECONDS = 5


class MertEncoder:
    model_name = MERT_MODEL

    def __init__(self, source: Path | None = None):
        import torch
        from transformers import AutoModel, Wav2Vec2FeatureExtractor

        src = str(source) if source else self.model_name
        self._torch = torch
        self._model = AutoModel.from_pretrained(src, trust_remote_code=True)
        self._model.eval()
        self._processor = Wav2Vec2FeatureExtractor.from_pretrained(
            src, trust_remote_code=True
        )

    def _layer_states(self, inputs):
        # transformers 5 + MERT remote code returns hidden_states=None, so
        # collect the 13 states (layer-0 input + 12 layer outputs) with hooks.
        torch = self._torch
        layers = self._model.encoder.layers
        states = []

        def first(o):
            return o[0] if isinstance(o, tuple) else o

        hooks = [layers[0].register_forward_pre_hook(
            lambda mod, a, kw: states.append(a[0] if a else kw["hidden_states"]),
            with_kwargs=True,
        )]
        hooks += [layer.register_forward_hook(lambda mod, a, o: states.append(first(o)))
                  for layer in layers]
        try:
            with torch.no_grad():
                self._model(**inputs)
        finally:
            for h in hooks:
                h.remove()
        return torch.stack(states).squeeze(1)  # (layers, time, hidden)

    def encode(self, path: Path | str) -> list[float]:
        torch = self._torch
        waveform = torch.from_numpy(decode_mono(path, MERT_SAMPLE_RATE))
        chunk_len = _CHUNK_SECONDS * MERT_SAMPLE_RATE
        chunk_vectors = []
        for start in range(0, waveform.shape[0], chunk_len):
            chunk = waveform[start : start + chunk_len]
            if chunk.shape[0] < chunk_len:
                padded = torch.zeros(chunk_len)
                padded[: chunk.shape[0]] = chunk
                chunk = padded
            inputs = self._processor(
                chunk.numpy(), sampling_rate=MERT_SAMPLE_RATE, return_tensors="pt"
            )
            # mean over time per layer, then uniform mean across layers
            chunk_vectors.append(self._layer_states(inputs).mean(dim=1).mean(dim=0))
        if not chunk_vectors:
            raise ValueError(f"No audio samples decoded from {path}")
        return torch.stack(chunk_vectors).mean(dim=0).numpy().astype("float32").tolist()
