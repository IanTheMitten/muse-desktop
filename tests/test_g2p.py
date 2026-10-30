from muse.g2p import G2p


def _g2p(tmp_path):
    p = tmp_path / "cmu.dict"
    p.write_text("hello HH AH0 L OW1\nhello(2) HH EH0 L OW1\n;;; c\nworld W ER1 L D # x\n",
                 encoding="utf-8")
    return G2p(p)


def test_dict_lookup_strips_stress_and_keeps_first(tmp_path):
    g = _g2p(tmp_path)
    assert g.phonemes("Hello") == ["HH", "AH", "L", "OW"]
    assert g.phonemes("world") == ["W", "ER", "L", "D"]


def test_unknown_without_npz_is_empty(tmp_path):
    g = _g2p(tmp_path)
    assert g.variables is None
    assert g.phonemes("zzyzx") == []
