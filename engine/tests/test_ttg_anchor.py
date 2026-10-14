import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pytest

from ttg_anchor import (anchor_to_market, dist_expect, market_ttg_expect,
                        solve_tilt, tilt_dist)

BASE = {"0:0": .07, "1:0": .10, "0:1": .075, "1:1": .119, "2:1": .091,
        "1:2": .072, "2:0": .070, "2:2": .057, "3:1": .043, "3:2": .026,
        "0:2": .045, "3:0": .040, "1:3": .028, "0:3": .023, "4:1": .015,
        "4:0": .017, "2:3": .021, "3:3": .012}


def test_tilt_zero_is_identity():
    out = tilt_dist(BASE, 0.0)
    z = sum(BASE.values())
    for s in BASE:
        assert abs(out[s] - BASE[s] / z) < 1e-12


@pytest.mark.parametrize("target", [2.5, 3.0, 3.42, 3.8])
def test_solve_tilt_hits_target(target):
    k = solve_tilt(BASE, target)
    assert abs(dist_expect(tilt_dist(BASE, k)) - target) < 1e-6


def test_shape_preserved_within_same_total():
    """同总进球的格子之间相对比例不变——形状维度不被锚定触碰（C 方案核心）。"""
    k = solve_tilt(BASE, 3.42)
    out = tilt_dist(BASE, k)
    assert abs(out["2:1"] / out["1:2"] - BASE["2:1"] / BASE["1:2"]) < 1e-9
    assert abs(out["3:1"] / out["1:3"] - BASE["3:1"] / BASE["1:3"]) < 1e-9


def test_monotonic_in_k():
    es = [dist_expect(tilt_dist(BASE, k)) for k in (-0.2, 0.0, 0.2, 0.4)]
    assert es == sorted(es)


def test_low_cells_shrink_when_anchoring_up():
    k = solve_tilt(BASE, 3.42)
    out = tilt_dist(BASE, k)
    z = sum(BASE.values())
    for s in ("0:0", "1:0", "0:1"):
        assert out[s] < BASE[s] / z


def test_market_ttg_expect_devig():
    odds = {"s0": 24.0, "s1": 7.75, "s2": 4.55, "s3": 3.70, "s4": 4.50,
            "s5": 6.50, "s6": 11.00, "s7": 13.00}
    e = market_ttg_expect(odds)
    assert e == pytest.approx(3.416, abs=0.01)


def test_market_ttg_guards():
    assert market_ttg_expect({}) is None
    assert market_ttg_expect({"s0": "x"}) is None
    assert market_ttg_expect({"s0": 2.0, "s1": 2.0}) is None  # <3 档不结


def test_anchor_to_market_end_to_end():
    """锚定后期望=市场，锚前=原分布。"""
    r = anchor_to_market(BASE, {"s0": 24.0, "s1": 7.75, "s2": 4.55, "s3": 3.70,
                                "s4": 4.50, "s5": 6.50, "s6": 11.00, "s7": 13.00})
    assert dist_expect(r["dist"]) == pytest.approx(r["target"], abs=1e-6)
    assert r["before"] < r["target"]            # BASE 期望 2.55 → 拉上
    assert r["k"] > 0


def test_anchor_without_market_is_identity():
    r = anchor_to_market(BASE, {})
    z = sum(BASE.values())
    assert r["k"] == 0.0
    assert abs(r["dist"]["1:1"] - BASE["1:1"] / z) < 1e-12
