# engine/tests/test_pick_logic.py
# -*- coding: utf-8 -*-
"""选场疫苗：V6随机基线可复现 + V7全量vs选出对照 + 五信号消融开关。开发者 sszhang"""
import pytest
import pick_logic as pk

def _cands(n=6):
    """合成候选：不同熵/概率带/联赛/信息事件。"""
    cands = []
    for i in range(n):
        cands.append({
            "matchId": f"m{i}", "league": "lgA" if i % 2 == 0 else "lgB",
            "matrix": {"s1s0": 0.12, "s2s1": 0.10, "s1s1": 0.09},   # 部分矩阵（熵用其算）
            "p_top": 0.12 + 0.01 * i,                                 # 信号位
            "calibBandOk": i % 3 != 0,                                # S2 校准带
            "leagueCalibOk": i % 2 == 0,                              # S3 联赛校准
            "infoEvent": i == 4,                                      # S4 伤停/首发变更
        })
    return cands

def test_pick_returns_reasons():
    picks = pk.pick(_cands(), top_n=2, weights={"s1_entropy": 0.3, "s2_calib": 0.2, "s3_league": 0.2,
                                                "s4_info": 0.2, "s5_disagreement": 0.1})
    assert len(picks) == 2
    assert all("reasons" in p and "score" in p for p in picks)     # 入选理由必须落盘

def test_pick_respects_league_gate():
    """S3 门：联赛校准不达标的场不进 picks（权重再高也不行）。"""
    picks = pk.pick(_cands(), top_n=6, weights={"s1_entropy": 1.0})
    assert all(p["leagueCalibOk"] for p in picks)

def test_v6_random_baseline_reproducible():
    """V6：同种子随机基线可复现（确定性）+ 分布统计字段齐。"""
    d1 = pk.random_baseline_dist(_cands(), top_n=2, n_perm=200, seed=42)
    d2 = pk.random_baseline_dist(_cands(), top_n=2, n_perm=200, seed=42)
    assert d1 == d2
    assert {"mean", "p95", "perm_n"} <= set(d1)

def test_v7_full_vs_picked_report_fields():
    """V7：对照报告字段强制（hit_picked/hit_full/diff/ci）。"""
    rep = pk.full_vs_picked({"hitPicked": 0.3, "n": 40}, {"hitFull": 0.22, "n": 120})
    assert {"hitPicked", "hitFull", "diff"} <= set(rep)
