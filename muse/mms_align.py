"""MMS forced-aligner (CTC, 1130 languages): one start time per lyric line.
Windowing follows the ctc-forced-aligner reference, reimplemented without it.
torch/transformers are imported inside `__init__` only, never at module import."""
import json
import math
import re
from pathlib import Path

import numpy as np

from .audio_decode import decode_mono
from .ctc_align import forced_align

MMS_MODEL = "MahmoudAshraf/mms-300m-1130-forced-aligner"
MMS_REVISION = "49402e9577b1158620820667c218cd494cc44486"
SAMPLE_RATE = 16000
WINDOW_S = 30
CONTEXT_S = 2
BATCH = 4
FRAME_S = 0.02  # stride 320 / 16000
_STRIDE = 320


class MmsAligner:
    def __init__(self, source: Path | None = None):
        import torch
        import uroman
        from transformers import Wav2Vec2ForCTC

        self._torch = torch
        if source:
            self._model = Wav2Vec2ForCTC.from_pretrained(str(source))
            vocab_path = Path(source) / "vocab.json"
        else:
            from huggingface_hub import hf_hub_download

            self._model = Wav2Vec2ForCTC.from_pretrained(MMS_MODEL, revision=MMS_REVISION)
            vocab_path = hf_hub_download(MMS_MODEL, "vocab.json", revision=MMS_REVISION)
        self._model.eval()
        with open(vocab_path, encoding="utf-8") as f:
            self._vocab = {k.lower(): v for k, v in json.load(f).items()}
        self._star = len(self._vocab)
        self._blank = self._vocab["<blank>"]
        self._uroman = uroman.Uroman()

    def _emissions(self, audio_path: Path) -> np.ndarray:
        torch = self._torch
        audio = torch.from_numpy(decode_mono(audio_path, SAMPLE_RATE)).float()
        window, context = WINDOW_S * SAMPLE_RATE, CONTEXT_S * SAMPLE_RATE
        with torch.inference_mode():
            if audio.shape[0] <= window:
                logits = self._model(audio[None]).logits[0]
            else:
                ext = math.ceil(audio.shape[0] / window) * window - audio.shape[0]
                padded = torch.nn.functional.pad(audio, (context, context + ext))
                wins = padded.unfold(0, window + 2 * context, window)
                ctx_f, win_f = context // _STRIDE, window // _STRIDE
                parts = []
                for i in range(0, wins.shape[0], BATCH):
                    out = self._model(wins[i : i + BATCH]).logits
                    parts.append(out[:, ctx_f : ctx_f + win_f].flatten(0, 1))
                logits = torch.cat(parts)
                if ext:
                    logits = logits[: logits.shape[0] - ext // _STRIDE]
            em = torch.log_softmax(logits, dim=-1).float().numpy()
        return np.concatenate([em, np.zeros((em.shape[0], 1), np.float32)], axis=1)

    def _ids(self, line: str, lang3: str | None) -> list[int]:
        text = self._uroman.romanize_string(line, lcode=lang3).lower()
        text = re.sub("[^a-z' ]", " ", text)
        return [self._vocab[c] for c in text if c != " " and c in self._vocab]

    def align(self, audio_path: Path, lines: list[str], lang3: str | None) -> list[float | None]:
        targets = [self._star]
        first: list[int | None] = []
        for line in lines:
            ids = self._ids(line, lang3)
            if not ids:
                first.append(None)
                continue
            first.append(len(targets))
            targets += ids + [self._star]
        if len(targets) == 1:
            return [None] * len(lines)
        frames, _ = forced_align(self._emissions(audio_path), targets, self._blank)
        return [None if i is None else frames[i] * FRAME_S for i in first]
