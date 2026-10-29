"""English lyrics aligner, vendored from schufo/lyrics-aligner @ 72be8a14b9126c201e0b8920513764c44ccd3f9e
(model.py, align.py). Based on a modified earlier Open Unmix (sigsep/open-unmix-pytorch).

Copyright (c) 2021 Kilian Schulze-Forster

MIT License

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

Paper: K. Schulze-Forster, C. Doire, G. Richard, R. Badeau, "Phoneme Level
Lyrics Alignment and Text-Informed Singing Voice Separation", IEEE/ACM TASLP 29, 2021.

Changes from upstream:
- STFT.forward: torch.stft(return_complex=True) wrapped in torch.view_as_real.
- PyAV decode (muse.audio_decode) instead of librosa.
- Phoneme map is a literal dict instead of a pickle.
- No VAD.
- Dropped unused smax, pad_for_stft, from_config and the word-level helpers;
  optimal_alignment_path loses its print.

Only the worker imports this module (torch at top level).
"""

from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.nn import LSTM, BatchNorm1d, Linear, Parameter

from .align_text import words
from .audio_decode import decode_mono
from .g2p import G2p

PHONEME2IDX = {'#': 0, '$': 1, '%': 2, '>': 3, '-': 4, 'AA': 5, 'AE': 6, 'AH': 7, 'AO': 8, 'AW': 9, 'AY': 10, 'B': 11, 'CH': 12, 'D': 13, 'DH': 14, 'EH': 15, 'ER': 16, 'EY': 17, 'F': 18, 'G': 19, 'HH': 20, 'IH': 21, 'IY': 22, 'JH': 23, 'K': 24, 'L': 25, 'M': 26, 'N': 27, 'NG': 28, 'OW': 29, 'OY': 30, 'P': 31, 'R': 32, 'S': 33, 'SH': 34, 'T': 35, 'TH': 36, 'UH': 37, 'UW': 38, 'V': 39, 'W': 40, 'Y': 41, 'Z': 42, 'ZH': 43}


class NoOp(nn.Module):
    def __init__(self):
        super().__init__()

    def forward(self, x):
        return x


class MatrixDiagonalIndexIterator:
    '''
    Custom iterator class to return successive diagonal indices of a matrix
    '''

    def __init__(self, m, n, k_start=0, bandwidth=None):
        '''
        __init__(self, m, n, k_start=0, bandwidth=None):

        Arguments:
            m (int)         : number of rows in matrix
            n (int)         : number of columns in matrix
            k_start (int)   : (k_start, k_start) index to begin from
            bandwidth (int) : bandwidth to constrain indices within
        '''
        self.m         = m
        self.n         = n
        self.k         = k_start
        self.k_max     = self.m + self.n - k_start - 1
        self.bandwidth = bandwidth

    def __iter__(self):
        return self

    def __next__(self):
        if hasattr(self, 'i') and hasattr(self, 'j'):

            if self.k == self.k_max:
                raise StopIteration

            elif self.k < self.m and self.k < self.n:
                self.i = self.i + [self.k]
                self.j = [self.k] + self.j
                self.k+=1

            elif self.k >= self.m and self.k < self.n:
                self.j.pop(-1)
                self.j = [self.k] + self.j
                self.k+=1

            elif self.k < self.m and self.k >= self.n:
                self.i.pop(0)
                self.i = self.i + [self.k]
                self.k+=1

            elif self.k >= self.m and self.k >= self.n:
                self.i.pop(0)
                self.j.pop(-1)
                self.k+=1

        else:
            self.i = [self.k]
            self.j = [self.k]
            self.k+=1

        if self.bandwidth:
            i_scb, j_scb = sakoe_chiba_band(self.i.copy(), self.j.copy(), self.m, self.n, self.bandwidth)
            return i_scb, j_scb
        else:
            return self.i.copy(), self.j.copy()

def sakoe_chiba_band(i_list, j_list, m, n, bandwidth=1):
    i_scb, j_scb = zip(*[(i, j) for i,j in zip(i_list, j_list)
                         if abs(2*(i*(n-1) - j*(m-1))) < max(m, n)*(bandwidth+1)])
    return list(i_scb), list(j_scb)


def dtw_matrix(scores, mode='faster', idx_to_skip=None):
    """
    Computes the accumulated score matrix by the "DTW forward operation"
    Args:
        scores: score matrix, shape(batch_size, length_sequence1, length_sequence2)
        mode:
        idx: list of indices,
             in mode 'skip_idx_faster' the possibility of skiping phonemes with given idx is considered

    Returns:
        dtw_matrix: accumulated scores, shape (batch_size, length_sequence1, length_sequence2)

    """
    B, N, M = scores.size()
    device = scores.device

    if mode == 'faster':
        # there is an issue with pytorch backward computation when using 'faster' with pytorch 1.2.0:
        # https://github.com/pytorch/pytorch/issues/24853
        dtw_matrix = torch.ones((B, N+1, M+1), device=device) * -100000

        dtw_matrix[:, 0, 0] = torch.ones((B,), device=device) * 200000
        # Sweep diagonally through alphas (as done in https://github.com/lyprince/sdtw_pytorch/blob/master/sdtw.py)
        # See also https://towardsdatascience.com/gpu-optimized-dynamic-programming-8d5ba3d7064f
        for (m,n),(m_m1,n_m1) in zip(MatrixDiagonalIndexIterator(m = M + 1, n = N + 1, k_start=1),
                                     MatrixDiagonalIndexIterator(m = M, n= N, k_start=0)):

            d1 = dtw_matrix[:, n_m1, m].unsqueeze(2) # shape(B, number_of_considered_values, 1)
            d2 = dtw_matrix[:, n_m1, m_m1].unsqueeze(2)
            max_values, idx = torch.max(torch.cat([d1, d2], dim=2), dim=2)
            dtw_matrix[:, n, m] = scores[:, n_m1, m_m1] + max_values
        return dtw_matrix[:, 1:N+1, 1:M+1]


class STFT(nn.Module):
    def __init__(
        self,
        n_fft=4096,
        n_hop=1024,
        center=False
    ):
        super(STFT, self).__init__()
        self.window = nn.Parameter(
            torch.hann_window(n_fft),
            requires_grad=False
        )

        self.n_fft = n_fft
        self.n_hop = n_hop
        self.center = center

    def forward(self, x):
        """
        Input: (nb_samples, nb_channels, nb_timesteps)
        Output:(nb_samples, nb_channels, nb_bins, nb_frames, 2)
        """

        nb_samples, nb_channels, nb_timesteps = x.size()

        # merge nb_samples and nb_channels for multichannel stft
        x = x.reshape(nb_samples*nb_channels, -1)

        # compute stft with parameters as close as possible scipy settings
        stft_f = torch.view_as_real(torch.stft(
            x,
            n_fft=self.n_fft, hop_length=self.n_hop,
            window=self.window, center=self.center,
            normalized=False, onesided=True,
            pad_mode='reflect', return_complex=True
        ))

        # reshape back to channel dimension
        stft_f = stft_f.contiguous().view(
            nb_samples, nb_channels, self.n_fft // 2 + 1, -1, 2
        )

        # shape (nb_samples, nb_channels, nb_bins, nb_frames, 2)
        return stft_f


class Spectrogram(nn.Module):
    def __init__(
        self,
        power=1,
        mono=True
    ):
        super(Spectrogram, self).__init__()
        self.power = power
        self.mono = mono

    def forward(self, stft_f):
        """
        Input: complex STFT
            (nb_samples, nb_channels, nb_bins, nb_frames, 2)
        Output: Power/Mag Spectrogram
            (nb_frames, nb_samples, nb_channels, nb_bins)
        """
        stft_f = stft_f.transpose(2, 3)
        # take the magnitude
        stft_f = stft_f.pow(2).sum(-1).pow(self.power / 2.0)

        # downmix in the mag domain
        if self.mono:
            stft_f = torch.mean(stft_f, 1, keepdim=True)

        # permute output for LSTM convenience
        return stft_f.permute(2, 0, 1, 3)


def index2one_hot(index_tensor, vocabulary_size):
    """
    Transforms index representation to one hot representation
    :param index_tensor: shape: (batch_size, sequence_length, 1) tensor containing character indices
    :param vocabulary_size: scalar, size of the vocabulary
    :return: chars_one_hot: shape: (batch_size, sequence_length, vocabulary_size)
    """

    device = index_tensor.device
    index_tensor = index_tensor.type(torch.LongTensor).to(device)

    batch_size = index_tensor.size()[0]
    char_sequence_len = index_tensor.size()[1]
    chars_one_hot = torch.zeros((batch_size, char_sequence_len, vocabulary_size), device=device)
    chars_one_hot.scatter_(dim=2, index=index_tensor, value=1)

    return chars_one_hot


class InformedOpenUnmix3(nn.Module):
    """
    Open Unmix with an additional text encoder and attention mechanism
    """
    def __init__(
        self,
        n_fft=512,
        n_hop=256,
        input_is_spectrogram=False,
        hidden_size=512,
        nb_channels=1,
        sample_rate=16000,
        audio_encoder_layers=2,
        nb_layers=3,
        input_mean=None,
        input_scale=None,
        max_bin=257,
        unidirectional=False,
        power=1,
        vocab_size=44,
        audio_transform='STFT'
    ):
        """
        Input: (nb_samples, nb_channels, nb_timesteps)
            or (nb_frames, nb_samples, nb_channels, nb_bins)
        Output: Power/Mag Spectrogram
                (nb_frames, nb_samples, nb_channels, nb_bins)
        """

        super(InformedOpenUnmix3, self).__init__()

        self.return_alphas = False
        self.optimal_path_alphas = False

        # text processing
        self.vocab_size = vocab_size

        self.lstm_txt = LSTM(vocab_size, hidden_size//2, num_layers=1, batch_first=True, bidirectional=True)

        # attention
        w_s_init = torch.empty(hidden_size, hidden_size)
        k = torch.sqrt(torch.tensor(1).type(torch.float32) / hidden_size)
        nn.init.uniform_(w_s_init, -k, k)
        self.w_s = nn.Parameter(w_s_init, requires_grad=True)

        # connection
        self.fc_c = Linear(hidden_size * 2, hidden_size)
        self.bn_c = BatchNorm1d(hidden_size)

        self.nb_output_bins = n_fft // 2 + 1
        if max_bin:
            self.nb_bins = max_bin
        else:
            self.nb_bins = self.nb_output_bins

        self.hidden_size = hidden_size

        self.stft = STFT(n_fft=n_fft, n_hop=n_hop)
        self.spec = Spectrogram(power=power, mono=(nb_channels == 1))
        self.register_buffer('sample_rate', torch.tensor(sample_rate))

        if input_is_spectrogram:
            self.transform = NoOp()
        elif audio_transform == 'STFT':
            self.transform = nn.Sequential(self.stft, self.spec)

        # audio encoder
        self.fc1 = Linear(
            self.nb_bins*nb_channels, hidden_size,
            bias=False
        )

        self.bn1 = BatchNorm1d(hidden_size)

        if unidirectional:
            lstm_hidden_size = hidden_size
        else:
            lstm_hidden_size = hidden_size // 2

        self.audio_encoder_lstm = LSTM(input_size=hidden_size, hidden_size=lstm_hidden_size,
                                       num_layers=audio_encoder_layers, bidirectional=not unidirectional,
                                       batch_first=False, dropout=0.4)


        self.lstm = LSTM(
            input_size=hidden_size,
            hidden_size=lstm_hidden_size,
            num_layers=nb_layers,
            bidirectional=not unidirectional,
            batch_first=False,
            dropout=0.4,
        )

        self.fc2 = Linear(
            in_features=hidden_size*2,
            out_features=hidden_size,
            bias=False
        )

        self.bn2 = BatchNorm1d(hidden_size)

        self.fc3 = Linear(
            in_features=hidden_size,
            out_features=self.nb_output_bins*nb_channels,
            bias=False
        )

        self.bn3 = BatchNorm1d(self.nb_output_bins*nb_channels)

        if input_mean is not None:
            input_mean = torch.from_numpy(
                -input_mean[:self.nb_bins]
            ).float()
        else:
            input_mean = torch.zeros(self.nb_bins)

        if input_scale is not None:
            input_scale = torch.from_numpy(
                1.0/input_scale[:self.nb_bins]
            ).float()
        else:
            input_scale = torch.ones(self.nb_bins)

        self.input_mean = Parameter(input_mean)
        self.input_scale = Parameter(input_scale)

        self.output_scale = Parameter(
            torch.ones(self.nb_output_bins).float()
        )
        self.output_mean = Parameter(
            torch.ones(self.nb_output_bins).float()
        )

    def forward(self, x):

        text_idx = x[1].unsqueeze(dim=2)  # text as index sequence
        x = x[0]  # mix

        x = self.transform(x)
        nb_frames, nb_samples, nb_channels, nb_bins = x.data.shape

        # -------------------------------------------------------------------------------------------------------------
        # text processing
        text_onehot = index2one_hot(text_idx, self.vocab_size)  # shape (nb_samples, sequence_len, vocabulary_size)

        h, _ = self.lstm_txt(text_onehot)  # lstm expects shape (batch_size, sequence_len, nb_features)

        # -------------------------------------------------------------------------------------------------------------
        # audio processing

        mix = x.detach().clone()

        # crop
        x = x[..., :self.nb_bins]

        # shift and scale input to mean=0 std=1 (across all bins)
        x += self.input_mean
        x *= self.input_scale

        # to (nb_frames*nb_samples, nb_channels*nb_bins)
        # and encode to (nb_frames*nb_samples, hidden_size)
        x = self.fc1(x.reshape(-1, nb_channels*self.nb_bins))
        # normalize every instance in a batch
        x = self.bn1(x)
        x = x.reshape(nb_frames, nb_samples, self.hidden_size)
        # squash range ot [-1, 1]
        x = torch.tanh(x)

        x, _ = self.audio_encoder_lstm(x)

        # -------------------------------------------------------------------------------------------------------------
        # attention
        batch_size = h.size(0)
        x = x.transpose(0, 1)  # to shape (nb_samples, nb_frames, self.hidden_size)

        # compute score = g_n * W_s * h_m in two steps
        side_info_transformed = torch.bmm(self.w_s.expand(batch_size, -1, -1),
                                          torch.transpose(h, 1, 2))

        scores = torch.bmm(x, side_info_transformed)
        dtw_alphas = dtw_matrix(scores, mode='faster')
        alphas = F.softmax(dtw_alphas, dim=2)


        # compute context vectors
        context = torch.bmm(torch.transpose(h, 1, 2), torch.transpose(alphas, 1, 2))

        # make shape: (nb_samples, N, hidden_size)
        context = torch.transpose(context, 1, 2)

        # -------------------------------------------------------------------------------------------------------------
        # connection of audio and text
        concat = torch.cat((context, x), dim=2)
        x = self.fc_c(concat)
        x = self.bn_c(x.transpose(1, 2))  # (nb_samples, hidden_size, nb_frames)
        x = torch.tanh(x)

        x = x.transpose(1, 2)
        x = x.transpose(0, 1)  # --> (nb_frames, nb_samples, hidden_size)

        # apply 3-layers of stacked LSTM
        lstm_out = self.lstm(x)

        # lstm skip connection
        x = torch.cat([x, lstm_out[0]], -1)

        # first dense stage + batch norm
        x = self.fc2(x.reshape(-1, x.shape[-1]))
        x = self.bn2(x)

        x = F.relu(x)

        # second dense stage + layer norm
        x = self.fc3(x)
        x = self.bn3(x)

        # reshape back to original dim
        x = x.reshape(nb_frames, nb_samples, nb_channels, self.nb_output_bins)

        # apply output scaling
        x *= self.output_scale
        x += self.output_mean

        # since our output is non-negative, we can apply RELU
        x = F.relu(x) * mix

        return x, alphas, scores


def compute_phoneme_onsets(optimal_path_matrix, hop_length, sampling_rate, return_skipped_idx=False):
    """

    Args:
        optimal_path_matrix: binary numpy array with shape (N, M)
        hop_length: int, hop length of the STFT
        sampling_rate: int, sampling frequency of the audio files

    Returns:
        phoneme_onsets: list
    """

    phoneme_indices = np.argmax(optimal_path_matrix, axis=1)

    # find positions that have been skiped:
    skipped_idx = [x+1 for i, (x, y) in
                   enumerate(zip(phoneme_indices[:-1], phoneme_indices[1:]))
                   if x == y - 2]

    # compute index of list elements whose right neighbor is different from itself
    last_idx_before_change = [i for i, (x, y) in
                              enumerate(zip(phoneme_indices[:-1], phoneme_indices[1:]))
                              if x != y]

    phoneme_onsets = [(n + 1) * hop_length / sampling_rate for n in last_idx_before_change]
    phoneme_onsets.insert(0, 0)  # the first space token's onset is 0

    if return_skipped_idx:
        return phoneme_onsets, skipped_idx
    else:
        for idx in skipped_idx:
            # set the onset of skipped tokens to the onset of the previous token
            phoneme_onsets.insert(idx, phoneme_onsets[idx])
        return phoneme_onsets


def accumulated_cost_numpy(score_matrix, init=None):
    """
    Computes the accumulated score matrix by the "DTW forward operation"

    Args:
        score_matrix: torch.Tensor of shape (batch_size, length_sequence1, length_sequence2)
        init: int, value to initialize the point (0, 0) in accumpated cost matrix

    Returns:
        dtw_matrix: accumulated score matrix

    """
    B, N, M = score_matrix.size()
    score_matrix = score_matrix.numpy().astype('float64')

    dtw_matrix = np.ones((N + 1, M + 1)) * -100000
    dtw_matrix[0, 0] = init

    # Sweep diagonally through alphas (as done in https://github.com/lyprince/sdtw_pytorch/blob/master/sdtw.py)
    # See also https://towardsdatascience.com/gpu-optimized-dynamic-programming-8d5ba3d7064f
    for (m,n),(m_m1,n_m1) in zip(MatrixDiagonalIndexIterator(m = M + 1, n = N + 1, k_start=1),
                                 MatrixDiagonalIndexIterator(m = M, n= N, k_start=0)):
        d1 = dtw_matrix[n_m1, m] # shape(number_of_considered_values)
        d2 = dtw_matrix[n_m1, m_m1]
        max_values = np.maximum(d1, d2)
        dtw_matrix[n, m] = score_matrix[0, n_m1, m_m1] + max_values
    return dtw_matrix[1:N+1, 1:M+1]


def optimal_alignment_path(matrix, init=200):
    """
    Args:
        matrix: torch.Tensor with shape (1, sequence_length1, sequence_length2)
        init: int, value to initialize the point (0, 0) in accumpated cost matrix

    Returns:
        optimal_path_matrix:
    """

    # forward step DTW
    accumulated_scores = accumulated_cost_numpy(matrix, init=init)

    N, M = accumulated_scores.shape

    optimal_path_matrix = np.zeros((N, M))
    optimal_path_matrix[-1, -1] = 1  # last phoneme is active at last time frame
    # backtracking: go backwards through time steps n and put value of active m to 1 in optimal_path_matrix
    n = N - 2
    m = M - 1

    while m > 0:
        d1 = accumulated_scores[n, m]  # score at n of optimal phoneme at n-1
        d2 = accumulated_scores[n, m - 1]  # score at n of phoneme before optimal phoneme at n-1
        arg_max = np.argmax([d1, d2])  # = 0 if same phoneme active as before, = 1 if previous phoneme active
        optimal_path_matrix[n, m - arg_max] = 1
        n -= 1
        m -= arg_max
        if n == -2:
            break
    optimal_path_matrix[0:n+1, 0] = 1

    return optimal_path_matrix


class SchufoAligner:
    def __init__(self, source: Path):
        self.model = InformedOpenUnmix3()
        state = torch.load(source / "model_parameters.pth", map_location="cpu", weights_only=True)
        self.model.load_state_dict(state, strict=True)
        self.model.eval()
        self.g2p = G2p(source / "cmudict.dict", source / "checkpoint20.npz")

    def align(self, audio_path: Path, lines: list[str]) -> list[float | None]:
        tokens = ['>']
        first: list[int | None] = []
        for line in lines:
            start = None
            for word in words(line):
                phones = [p for p in self.g2p.phonemes(word) if p in PHONEME2IDX]
                if not phones:
                    continue
                if start is None:
                    start = len(tokens)
                tokens.extend(phones)
                tokens.append('>')
            first.append(start)
        if all(s is None for s in first):
            return [None] * len(lines)
        audio = torch.from_numpy(decode_mono(audio_path, 16000))
        idx = torch.tensor([PHONEME2IDX[t] for t in tokens], dtype=torch.float32)
        with torch.no_grad():
            _, _, scores = self.model([audio[None, None, :], idx[None, :]])
        onsets = compute_phoneme_onsets(optimal_alignment_path(scores), 256, 16000)
        return [None if s is None else float(onsets[s]) for s in first]
