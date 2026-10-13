# engine/tests/test_tail_dataset.py
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from tail_dataset import label_skeleton, market_anchor_features


def _full_pool(hi=8.0):
    pool = {f"{i}:{j}": hi for i in range(3) for j in range(3)}
    pool.update({"other_h": 40.0, "other_d": 40.0, "other_a": 40.0})
    pool.update({f"{i}:{j}": 30.0 for i in range(3, 6) for j in range(3)})
    pool.update({f"{i}:{j}": 30.0 for i in range(3) for j in range(3, 6)})
    return pool


def test_label_skeleton_axes():
    assert label_skeleton("2:1") == {"tg": 3, "tgBin": 3, "direction": 0, "cleanSheet": 2}
    assert label_skeleton("0:5") == {"tg": 5, "tgBin": 5, "direction": 2, "cleanSheet": 0}
    assert label_skeleton("3:0") == {"tg": 3, "tgBin": 3, "direction": 0, "cleanSheet": 1}
    assert label_skeleton("0:0") == {"tg": 0, "tgBin": 0, "direction": 1, "cleanSheet": 2}  # 0:0 归"无"
    assert label_skeleton("2:6")["tgBin"] == 7        # tg=8 截断到 7+


def test_market_anchor_dimensions_and_direction():
    row = {"crsOpen": _full_pool()}
    f = market_anchor_features(row)
    assert len(f) == 11
    assert 0.0 < f["mkt_p_home_open"] < 1.0
    assert f["mkt_p_tg_ge5_open"] > 0.0
    # 0:0 赔率调最低 → 三向里平局聚合应最大
    row2 = {"crsOpen": {**_full_pool(), "0:0": 1.5}}
    f2 = market_anchor_features(row2)
    assert f2["mkt_p_draw_open"] > f2["mkt_p_home_open"]


def test_market_anchor_insufficient_pool():
    assert market_anchor_features({"crsOpen": {"1:1": 7.0}}) == {}
