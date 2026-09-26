# engine/scripts/tests/test_crs_fusion.py
"""crs_fusion 融合引擎测试。开发者 sszhang"""
import pytest
from crs_fusion import extract_mkt_dist

def test_power_dewater_sums_to_one():
    # 3 项玩具市场: 抽水 20%
    odds = {"1:0": 2.5, "1:1": 3.5, "0:1": 4.0}
    dist = extract_mkt_dist(odds)
    assert abs(sum(dist.values()) - 1.0) < 1e-9

def test_power_dewater_monotone():
    # 赔率低的项概率必须高（保序）
    odds = {"1:0": 2.0, "1:1": 4.0, "0:1": 8.0}
    dist = extract_mkt_dist(odds)
    assert dist[(1,0)] > dist[(1,1)] > dist[(0,1)]

def test_power_dewater_lifts_tail_vs_naive():
    # 高抽水场景下 power 法必须比朴素归一给长赔项更高概率（favorite-longshot 修正, spec §3）
    odds = {"1:0": 2.0, "0:4": 200.0, "1:1": 3.5, "4:0": 150.0}
    dist = extract_mkt_dist(odds)
    naive = {k: (1/v)/sum(1/x for x in odds.values()) for k, v in odds.items()}
    h, a = 0, 4
    key = (h, a)
    assert dist[key] > naive["0:4"]

def test_extract_skips_invalid_entries():
    # brief 笔误修正：原 fixture "0:1": "x" 为非数字值（须跳过）却断言 (0,1) in dist，自相矛盾；
    # 改为 0:1 给合法值、"x" 挂 2:2 补测跳过路径，断言行保持 brief 原文
    odds = {"1:0": 2.5, "bad": 0, "1:1": 3.5, "0:1": 4.0, "2:2": "x"}
    dist = extract_mkt_dist(odds)
    assert (1,0) in dist and (1,1) in dist and (0,1) in dist
    assert (2,2) not in dist

from crs_fusion import smooth_template

def test_smooth_all_positive_even_unseen():
    # 数学审查错误1修正：c=0 的比分平滑后必须 >0（先验支撑集不得阉割后验）
    counts = {(1,0): 50, (1,1): 40, (2,1): 30}
    q = smooth_template(counts)
    assert all(p > 0 for p in q.values())
    assert (0,4) in q and q[(0,4)] > 0   # 从未出现的比分也有平滑概率

def test_smooth_sums_to_one():
    counts = {(1,0): 50, (1,1): 40}
    q = smooth_template(counts)
    assert abs(sum(q.values()) - 1.0) < 1e-9

def test_smooth_preserves_ranking():
    counts = {(1,0): 50, (1,1): 40, (2,1): 30}
    q = smooth_template(counts)
    assert q[(1,0)] > q[(1,1)] > q[(2,1)]
