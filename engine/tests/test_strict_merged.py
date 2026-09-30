# -*- coding: utf-8 -*-
"""strict_merged 回测时间线防自泄漏（2026-09-30 v4b +341% 自泄漏事故）。开发者 sszhang"""
from common import LEAK_LAG_DAYS, strict_merged


def _order(merged):
    return [(r[0], r[2], r[3]) for r in merged]


def test_same_match_league_row_same_day_comes_after_prediction():
    # 联赛库与体彩同日记同一场：该场赛果不得先于预测入统计
    m = strict_merged([("2026-09-20", "a", "b", 2, 1)], [("2026-09-20", "a", "b", 2, 1, 0)])
    assert _order(m) == [("B", "a", "b"), ("L", "a", "b")]


def test_same_match_league_row_one_day_earlier_comes_after_prediction():
    # 实测分布含 −1：联赛库比体彩早一天记同一场，同样不得先入统计
    m = strict_merged([("2026-09-19", "a", "b", 2, 1)], [("2026-09-20", "a", "b", 2, 1, 0)])
    assert _order(m) == [("B", "a", "b"), ("L", "a", "b")]


def test_earlier_league_result_beyond_lag_is_visible():
    # 预测日前 lag+1 天的赛果正常入统计
    m = strict_merged([("2026-09-17", "a", "c", 1, 0)], [("2026-09-20", "a", "b", 2, 1, 7)])
    assert _order(m) == [("L", "a", "c"), ("B", "a", "b")]
    assert m[1][6] == 7


def test_lag_default_covers_observed_gap():
    assert LEAK_LAG_DAYS >= 2
