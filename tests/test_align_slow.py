from pathlib import Path

import pytest

from muse import smart

pytestmark = pytest.mark.slow

TONE = Path(__file__).parent / "fixtures" / "smoke" / "tone.wav"
CACHE = Path.home() / ".cache" / "muse-test-weights"
SCHUFO = CACHE / "weights" / "schufo"


@pytest.fixture(scope="module")
def weights():
    for e in smart.load_manifest()["weights"]:
        if e["path"].startswith("weights/schufo/"):
            smart.download(smart.ManifestFile(**e), CACHE)
    return CACHE / "weights"


def test_schufo(weights):
    from muse.schufo import SchufoAligner

    starts = SchufoAligner(SCHUFO).align(TONE, ["hello there", "[Chorus]", "how are you"])
    assert len(starts) == 3
    assert starts[1] is None
    a, c = starts[0], starts[2]
    assert isinstance(a, float) and isinstance(c, float)
    assert 0 <= a <= c


def test_g2p_real_npz(weights):
    from muse.g2p import G2p
    from muse.schufo import PHONEME2IDX

    ph = G2p(SCHUFO / "cmudict.dict", SCHUFO / "checkpoint20.npz").phonemes("zzyzxian")
    assert ph and all(p in PHONEME2IDX for p in ph)


def test_mms():
    from muse.mms_align import MmsAligner

    starts = MmsAligner().align(TONE, ["사랑해", "愛してる", "baby"], "kor")
    assert len(starts) == 3
    assert all(isinstance(s, float) for s in starts)
    assert starts == sorted(starts)


def test_worker_align(weights):
    from muse.worker_main import real_handlers

    out = real_handlers(weights)["align"](
        {"path": str(TONE), "lines": ["I love you so much", "every single day"]}
    )
    assert out["aligner"] == "schufo"
    assert out["lang"] == "en"
    assert len(out["starts"]) == 2
