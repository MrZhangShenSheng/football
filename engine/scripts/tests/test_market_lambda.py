# engine/scripts/tests/test_market_lambda.py
# -*- coding: utf-8 -*-
"""v35-P4 D3 市场反解引擎测试。开发者 sszhang"""
import math

import market_lambda as ml


CRS = {"0:0": 9.0, "1:1": 6.2, "2:1": 7.0, "1:0": 5.3, "0:1": 11.5, "2:0": 6.0,
       "0:2": 30.0, "2:2": 17.0, "3:0": 10.5, "other_h": 70.0, "other_d": 500.0,
       "other_a": 400.0, "3:1": 12.0, "1:2": 17.0, "3:2": 30.0, "2:3": 90.0,
       "3:3": 100.0, "4:0": 25.0, "4:1": 32.0, "4:2": 80.0, "0:3": 120.0, "1:3": 300.0,
       "0:4": 400.0, "1:4": 300.0, "2:4": 300.0, "4:3": 150.0, "3:4": 400.0,
       "5:0": 70.0, "5:1": 90.0, "5:2": 250.0, "0:5": 500.0}
# 注: brief 原样例 30 键·断言 31 格（体彩 CRS 完整盘常量）——补 "0:5" 对齐（负向 0:x 尾格衔接）
TTG = {"0": 9.0, "1": 4.1, "2": 3.15, "3": 3.75, "4": 6.5, "5": 12.5, "6": 21.0, "7": 30.0}


def test_crs_key_map():
    assert ml.crs_key("1:1") == "s01s01"
    assert ml.crs_key("other_h") == "s1sh"
    assert ml.crs_key("junk") == ""


def test_crs_power_devig_sum_and_keys():
    p = ml.crs_power_devig(CRS)
    assert len(p) == 31 and abs(sum(p.values()) - 1.0) < 1e-9
    assert "s01s01" in p and "s1sh" in p and "1:1" not in p
    assert max(p, key=p.get) == "s01s00"          # 最低赔格(1:0@5.3)去水后仍居首


def test_crs_power_devig_sparse_returns_empty():
    assert ml.crs_power_devig({"1:1": 6.0, "0:0": 9.0}) == {}


def test_lam_sum_reasonable():
    assert 2.0 < ml.lam_sum_from_ttg(TTG) < 3.2   # 样例盘 E[T]≈2.49


def test_ttg_boom_small():
    b = ml.ttg_boom(TTG)
    assert 0.0 <= b < 0.2                          # 6+7 档小概率


def test_solve_lamh_roundtrip():
    import sys
    sys.path.insert(0, "engine/scripts")
    import score_matrix as sm
    ls = ml.lam_sum_from_ttg(TTG)
    p_h = 0.6
    lh = ml.solve_lamh(ls, p_h)
    back = sm.had_from(sm.dc_matrix(lh, ls - lh, ml.RHO_ZERO))["h"]
    assert abs(back - p_h) < 0.01                  # 二分 40 次收敛
