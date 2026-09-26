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

def test_power_overround_pool_k_above_one():
    # 生产回归：体彩真实 CRS 池抽水 Σ1/o>1（score_odds/2026-09-25 实测 1.17~1.32）→ k 解须 >1；
    # brief 原二分上界 1.0 会夹死在 k=1，power 去水静默退化为朴素归一（去水惰化）
    odds = {"1:0": 1.8, "1:1": 2.0, "0:1": 3.6}   # Σ1/o = 1.333 抽水池
    dist = extract_mkt_dist(odds)
    assert abs(sum(dist.values()) - 1.0) < 1e-9
    naive = {k: (1/v)/sum(1/x for x in odds.values()) for k, v in odds.items()}
    assert dist[(1,0)] > naive["1:0"]   # 热门项相对朴素归一抬升（k>1 修正方向）

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

from crs_fusion import fuse_crs, shrink_lambda

def test_fuse_normalizes():
    q = {(1,0): 0.3, (1,1): 0.25, (0,1): 0.2, (2,1): 0.15, (2,0): 0.1}
    p = {(1,0): 0.4, (1,1): 0.3, (0,1): 0.1, (2,1): 0.1, (2,0): 0.1}
    f = fuse_crs(q, p)
    assert abs(sum(f.values()) - 1.0) < 1e-9

def test_fuse_market_dominant_when_r_small():
    # r→0 退化为市场分布（spec 弱点5：a*→0 退化仍有效）
    q = {(1,0): 0.9, (1,1): 0.05, (0,1): 0.05}
    p = {(1,0): 0.1, (1,1): 0.2, (0,1): 0.7}
    f = fuse_crs(q, p, r=0.001)
    assert f[(0,1)] > f[(1,0)]   # 跟市场走

def test_fuse_epsilon_on_missing_market_key():
    # 市场缺项 ε 兜底不归零
    q = {(1,0): 0.5, (5,5): 0.5}
    p = {(1,0): 1.0}             # 市场只有一项
    f = fuse_crs(q, p)
    assert f[(5,5)] > 0

def test_shrink_lambda_basic():
    assert abs(shrink_lambda(1.2, 3.0, w=0.35)[0] - (0.35*1.2 + 0.65*3.0)) < 1e-9

def test_shrink_lambda_no_market_degrades():
    lam, shrunk = shrink_lambda(2.5, None, w=0.35)
    assert lam == 2.5 and shrunk is False

def test_shrink_lambda_extremes():
    assert shrink_lambda(2.0, 3.0, w=0.0)[0] == 3.0   # 全市场
    assert shrink_lambda(2.0, 3.0, w=1.0)[0] == 2.0   # 全模型

from crs_fusion import FAMILIES, family_scores, family_gate

def test_five_families_defined():
    assert set(FAMILIES) == {"home_clean", "home_multi", "draw", "away_clean", "away_multi"}
    assert (1,1) in FAMILIES["draw"] and (2,0) in FAMILIES["home_clean"]

def test_family_scores_sorted_with_top2():
    p = {(1,0): 0.18, (1,1): 0.15, (0,1): 0.12, (2,1): 0.10, (2,0): 0.08, (0,0): 0.05}
    fams = family_scores(p)
    assert fams[0]["prob"] >= fams[1]["prob"]
    draw = [f for f in fams if f["family"] == "draw"][0]
    assert draw["top1"][0] == (1,1) and draw["top2"][0] == (0,0)

def test_family_gate_blocks_flat_distribution():
    # 数学审查错误3：高方差场族概率摊平 → 关档
    flat = {(1,0): 0.07, (1,1): 0.07, (0,1): 0.07, (2,1): 0.06, (2,0): 0.06, (0,2): 0.06, (0,0): 0.04}
    ok, mx = family_gate(flat)
    assert ok is False and mx < 0.28

def test_family_gate_passes_concentrated():
    p = {(1,1): 0.30, (0,0): 0.10, (1,0): 0.20, (0,1): 0.05, (2,1): 0.05, (2,0): 0.05}
    ok, mx = family_gate(p)
    assert ok is True and mx >= 0.28

import json, pathlib
from crs_fusion import load_fusion_crs

def test_load_fusion_crs_reads_file():
    cfg = load_fusion_crs()
    assert cfg["enabled"] is True
    assert 0 < cfg["r"] < 1 and 0 <= cfg["w"] <= 1

def test_load_fusion_crs_degrades_to_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr("crs_fusion.FUSION_CRS_PATH", tmp_path / "missing.json")
    cfg = load_fusion_crs()
    assert cfg["degraded"] is True and cfg["r"] == 0.286

def test_load_fusion_crs_fills_missing_keys(tmp_path, monkeypatch):
    # Minor-4：文件存在但缺键 → setdefault 全默认键兜底（不 KeyError 不静默缺参）
    p = tmp_path / "fusion_crs.json"
    p.write_text(json.dumps({"r": 0.3}), encoding="utf-8")
    monkeypatch.setattr("crs_fusion.FUSION_CRS_PATH", p)
    cfg = load_fusion_crs()
    assert cfg["r"] == 0.3                          # 文件值优先
    assert cfg["w"] == 0.35 and cfg["alphaLidstone"] == 0.5
    assert cfg["familyGateThreshold"] == 0.28 and cfg["enabled"] is True
    assert cfg["degraded"] is False                 # 文件在 = 非降级

# ---- 审查移交 Important：smooth_template 池外折叠（真实联赛模板 Σ=0.941 修复）----

def test_smooth_offpool_counts_sum_to_one():
    # 含池外比分计数（6:0/5:3/0:6 等真实联赛模板占历史 1.4%）→ 全 31 项 Σ=1 精确成立
    counts = {(1, 0): 50, (1, 1): 40, (2, 1): 30, (6, 0): 3, (5, 3): 2, (0, 6): 2}
    q = smooth_template(counts)
    assert len(q) == 31
    assert abs(sum(q.values()) - 1.0) < 1e-9

def test_smooth_offpool_folds_into_direction_repr():
    # 方向折叠：6:0→(4,3) 胜其他代表、5:5→(4,4) 平其他、0:6→(3,4) 负其他（体彩兜底项语义）
    base = smooth_template({(1, 0): 100})
    q = smooth_template({(1, 0): 100, (6, 0): 10, (5, 5): 10, (0, 6): 10})
    assert q[(4, 3)] > base[(4, 3)]
    assert q[(4, 4)] > base[(4, 4)]
    assert q[(3, 4)] > base[(3, 4)]

def test_smooth_ignores_non_score_keys():
    # "__n" 等非比分键不进分母：进分母必须在分子有对应（Σ=1 精确成立前提）
    a = smooth_template({(1, 0): 50, (1, 1): 40})
    b = smooth_template({(1, 0): 50, (1, 1): 40, "__n": 999})
    assert a == b
