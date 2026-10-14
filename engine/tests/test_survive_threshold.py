import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pytest

import freq_band as fb
from score_ev import build_freq_table


@pytest.fixture(scope="module")
def pool():
    return fb.global_pool(build_freq_table())


def _expect_above(pool, th):
    n = pool["__n"]
    q = {s: c / n for s, c in pool.items() if s != "__n"}
    keep = {s: v for s, v in q.items() if v >= th}
    z = sum(keep.values())
    return sum(v * sum(int(x) for x in s.split(":")) for s, v in keep.items()) / z


def test_survive_q_is_half_percent():
    assert fb.SURVIVE_Q == 0.005


def test_threshold_choice_beats_one_percent(pool):
    """0.5% 相对 1% 须挽回 >=0.10 球（剔除的全是高分格）。"""
    assert _expect_above(pool, 0.005) - _expect_above(pool, 0.01) >= 0.10


def test_lower_threshold_has_diminishing_return(pool):
    """0.3% 相对 0.5% 边际收益须 <0.06 球——这是不再降阈的依据。"""
    assert _expect_above(pool, 0.003) - _expect_above(pool, 0.005) < 0.06


def test_cut_probability_mass_acceptable(pool):
    """0.5% 阈剔除的真实概率须 <5%（1% 阈剔 7.8% 是修正前的问题）。"""
    n = pool["__n"]
    cut = sum(c / n for s, c in pool.items() if s != "__n" and c / n < fb.SURVIVE_Q)
    assert cut < 0.05
