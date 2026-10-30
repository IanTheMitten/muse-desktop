# Offline English grapheme-to-phoneme: CMU dict lookup, GRU fallback for unknown words.
# GRU predict ported from g2p_en 2.1.0 (Kyubyong Park, Jongseok Kim), Apache-2.0.
# CMU dict (c) Carnegie Mellon University, BSD-style licence.
import re
from pathlib import Path

import numpy as np

_GRAPHEMES = ["<pad>", "<unk>", "</s>"] + list("abcdefghijklmnopqrstuvwxyz")
_PHONEMES = ["<pad>", "<unk>", "<s>", "</s>"] + [
    'AA0', 'AA1', 'AA2', 'AE0', 'AE1', 'AE2', 'AH0', 'AH1', 'AH2', 'AO0',
    'AO1', 'AO2', 'AW0', 'AW1', 'AW2', 'AY0', 'AY1', 'AY2', 'B', 'CH', 'D', 'DH',
    'EH0', 'EH1', 'EH2', 'ER0', 'ER1', 'ER2', 'EY0', 'EY1',
    'EY2', 'F', 'G', 'HH',
    'IH0', 'IH1', 'IH2', 'IY0', 'IY1', 'IY2', 'JH', 'K', 'L',
    'M', 'N', 'NG', 'OW0', 'OW1',
    'OW2', 'OY0', 'OY1', 'OY2', 'P', 'R', 'S', 'SH', 'T', 'TH',
    'UH0', 'UH1', 'UH2', 'UW',
    'UW0', 'UW1', 'UW2', 'V', 'W', 'Y', 'Z', 'ZH']
_SPECIAL = {"<unk>", "<pad>", "</s>"}


def _sigmoid(x):
    return 1 / (1 + np.exp(-x))


class G2p:
    def __init__(self, cmudict_path: Path, npz_path: Path | None = None):
        self.graphemes = _GRAPHEMES
        self.phonemes_list = _PHONEMES
        self.g2idx = {g: i for i, g in enumerate(self.graphemes)}
        self.idx2p = {i: p for i, p in enumerate(self.phonemes_list)}
        self.cmu: dict[str, list[str]] = {}
        for line in Path(cmudict_path).read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line or line.startswith(";;;"):
                continue
            word, *phones = line.split()
            if re.search(r"\(\d+\)$", word):
                continue
            self.cmu.setdefault(word.lower(), phones)
        self.variables = None
        if npz_path is not None:
            v = self.variables = np.load(npz_path, allow_pickle=False)
            for k in ("enc_emb", "enc_w_ih", "enc_w_hh", "enc_b_ih", "enc_b_hh",
                      "dec_emb", "dec_w_ih", "dec_w_hh", "dec_b_ih", "dec_b_hh",
                      "fc_w", "fc_b"):
                setattr(self, k, v[k])

    def _grucell(self, x, h, w_ih, w_hh, b_ih, b_hh):
        rzn_ih = np.matmul(x, w_ih.T) + b_ih
        rzn_hh = np.matmul(h, w_hh.T) + b_hh
        k = rzn_ih.shape[-1] * 2 // 3
        rz = _sigmoid(rzn_ih[:, :k] + rzn_hh[:, :k])
        r, z = np.split(rz, 2, -1)
        n = np.tanh(rzn_ih[:, k:] + r * rzn_hh[:, k:])
        return (1 - z) * n + z * h

    def _predict(self, word: str) -> list[str]:
        x = [self.g2idx.get(c, 1) for c in list(word) + ["</s>"]]
        x = np.take(self.enc_emb, np.expand_dims(x, 0), axis=0)
        h = np.zeros((1, self.enc_w_hh.shape[-1]), np.float32)
        for t in range(len(word) + 1):
            h = self._grucell(x[:, t, :], h, self.enc_w_ih, self.enc_w_hh,
                              self.enc_b_ih, self.enc_b_hh)
        dec = np.take(self.dec_emb, [2], axis=0)  # 2: <s>
        preds = []
        for _ in range(20):
            h = self._grucell(dec, h, self.dec_w_ih, self.dec_w_hh, self.dec_b_ih, self.dec_b_hh)
            pred = (np.matmul(h, self.fc_w.T) + self.fc_b).argmax()
            if pred == 3:  # </s>
                break
            preds.append(pred)
            dec = np.take(self.dec_emb, [pred], axis=0)
        return [self.idx2p.get(i, "<unk>") for i in preds]

    def phonemes(self, word: str) -> list[str]:
        word = word.lower()
        if word in self.cmu:
            raw = self.cmu[word]
        elif self.variables is not None and re.search("[a-z]", word):
            raw = self._predict(re.sub(r"[^a-z']", "", word))
        else:
            return []
        return [re.sub(r"\d$", "", p) for p in raw if p not in _SPECIAL]
