import sys
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import freq_band as fb


def _blob():
    """低分模板池：1:1 高频、4:1 低频（0.4%——旧 1% 阈必剔、新 0.5% 阈仍剔）。"""
    return Counter({"__n": 1000, "1:1": 300, "1:0": 250, "0:1": 150, "0:0": 100,
                    "2:1": 90, "1:2": 60, "2:0": 30, "0:2": 15, "4:1": 4, "3:1": 1})


HIGH_TTG = {"s0": 30.0, "s1": 8.0, "s2": 4.0, "s3": 3.2, "s4": 3.4,
            "s5": 6.0, "s6": 11.0, "s7": 15.0}   # 去水期望约 3.9 球


def test_anchored_q_lifts_expectation_to_market():
    q = fb.anchored_q(_blob(), None, HIGH_TTG)
    z = sum(v for s, v in q.items())
    e = sum(v * sum(int(x) for x in s.split(":")) for s, v in q.items()) / z
    target = fb._ttg_market_expect(HIGH_TTG)
    assert abs(e - target) < 0.02


def test_anchored_q_no_market_is_identity():
    assert fb.anchored_q(_blob(), None, {}) == fb.shifted_q(_blob(), None)


def test_anchored_q_shape_dimension_untouched():
    """同总进球格间比例守恒：1:0/0:1（各1球）比值不变。"""
    q0 = fb.shifted_q(_blob(), None)
    q1 = fb.anchored_q(_blob(), None, HIGH_TTG)
    r0 = q0["1:0"] / q0["0:1"]
    r1 = q1["1:0"] / q1["0:1"]
    assert abs(r1 - r0) < 1e-9


def test_freq_legs_anchored_revives_high_score_cell():
    """锚定后生存阈用锚定 q 判——高总分市场下 4:1 质量被抬升可越过阈
    （若仍用锚前 q_pure 判阈则 0.4% 永死——T6 接线正确性核心断言）。"""
    odds_day = {"matches": [
        {"matchNumStr": "001", "league": "意甲", "home": "A", "away": "B",
         "had": {"h": 1.3, "d": 5.5, "a": 9.0},
         "ttg": HIGH_TTG,
         "crs": {"4:1": 34.0, "2:1": 15.0, "1:1": 6.5}}]}
    zh = {}
    legs = fb.freq_legs(odds_day, {"italy-serie-a": _blob()}, {}, zh,
                        band=(10.0, 40.0))
    scores = [l["score"] for l in legs]
    # 4:1 锚定后须可入榜（是否第一取决于形状，但不可因纯频率 0.4% 被生存阈处决）
    assert "4:1" in scores, f"4:1 仍被生存阈拦: {scores}"
