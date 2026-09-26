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


# ================= Task 6: freq_band fused_legs 接入集成 =================
# spec: docs/2026-09-26-crs-fusion-redesign（.superpowers/sdd/2026-09-26-crs-fusion-redesign）

def test_full_pipeline_toy_match():
    # 玩具场：市场定价 1:1 集中 → 平局族应居首且过闸门
    crs_odds = {"1:1": 4.0, "0:0": 8.0, "1:0": 7.0, "0:1": 9.0, "2:1": 9.0, "2:2": 16.0,
                "2:0": 15.0, "0:2": 18.0, "3:0": 30.0, "0:3": 40.0, "3:1": 20.0, "1:3": 30.0, "3:2": 40.0, "2:3": 40.0,
                "1:2": 12.0, "1:4": 90.0, "0:4": 80.0, "4:0": 60.0, "4:1": 80.0, "5:0": 150.0, "0:5": 200.0}
    counts = {(1, 1): 40, (1, 0): 30, (2, 1): 25, (0, 1): 20, (0, 0): 15, (2, 0): 12, (1, 2): 10}
    q = smooth_template(counts)
    p = extract_mkt_dist(crs_odds)
    f = fuse_crs(q, p, r=load_fusion_crs()["r"])
    ok, mx = family_gate(f)
    fams = family_scores(f)
    assert ok and fams[0]["family"] == "draw"    # 市场模板同向 → 平局族居首

from collections import Counter
from band_calibration import CURRENT_SEASON
from freq_band import fused_legs, _ttg_market_expect

CFG_TOY = {"r": 0.286, "w": 0.35, "alphaLidstone": 0.5, "familyGateThreshold": 0.28}

def _toy_stack():
    """玩具三件套：英超模板 + 双方当季近况（team_strength 生效 → λ 平移可用）。"""
    freq = {"england-premier": Counter({"1:1": 30, "1:0": 25, "2:1": 20, "0:1": 15,
                                        "0:0": 10, "2:0": 8, "1:2": 7, "__n": 115})}
    row = lambda gf, ga: (gf, ga, CURRENT_SEASON, "england-premier")
    form = {"teama": [row(1, 1)] * 10,                       # 主队 (进1.0, 失1.0)
            "teamb": [row(1, 0)] * 5 + [row(1, 1)] * 5}      # 客队 (进1.0, 失0.5)
    zh = {"主队甲": "team-a", "客队乙": "team-b"}
    return freq, form, zh

def test_ttg_market_expect_uniform_eight():
    # 等赔 8 档 → 去水均匀 p=1/8 → e_mkt=(0+..+6)/8 + 7.4/8 = 3.55（s7 用 7.4 代入·spec §2 弱点6）
    ttg = {f"s{k}": 8.0 for k in range(8)}
    assert abs(_ttg_market_expect(ttg) - 3.55) < 1e-9
    assert _ttg_market_expect({"s0": 8.0}) is None           # 档不全 → None（宁缺毋滥）
    assert _ttg_market_expect({}) is None
    assert _ttg_market_expect(None) is None

def test_ttg_market_expect_hot_low_scores():
    # 低总进球热门（s0-s2 赔率低）→ e_mkt 低于均匀 3.55；高进球热门反向
    low = {"s0": 2.0, "s1": 2.2, "s2": 2.5, "s3": 4.5, "s4": 7.0, "s5": 12.0, "s6": 20.0, "s7": 40.0}
    high = {"s0": 40.0, "s1": 20.0, "s2": 12.0, "s3": 7.0, "s4": 4.5, "s5": 2.5, "s6": 2.2, "s7": 2.0}
    assert _ttg_market_expect(low) < 3.55 < _ttg_market_expect(high)

def test_fused_legs_lambda_shrink_wiring():
    # λ 收缩接线：有 8 档市场 TTG → λsum 向 e_mkt 收缩(w=0.35)且 λh/λa 比例保持；
    # 无 TTG → 原 λ + shrunk=False。e_mkt 手算锚=等赔 8 档 3.55
    freq, form, zh = _toy_stack()
    m = {"matchNumStr": "周六001", "league": "英超", "home": "主队甲", "away": "客队乙"}
    ttg = {f"s{k}": 8.0 for k in range(8)}
    day_with = {"matches": [dict(m, crs={}, ttg=ttg)]}
    day_wo = {"matches": [dict(m, crs={}, ttg={})]}
    r_wo = fused_legs(day_wo, freq, form, zh, CFG_TOY)[0]
    r_with = fused_legs(day_with, freq, form, zh, CFG_TOY)[0]
    assert r_wo["shrunk"] is False and r_wo["lambda"] is not None   # 无 TTG 用原 λ
    assert r_with["shrunk"] is True
    s0, s1 = sum(r_wo["lambda"]), sum(r_with["lambda"])
    assert abs(s1 - (0.35 * s0 + 0.65 * 3.55)) < 0.02              # James-Stein 收缩公式
    lw, ls = r_wo["lambda"], r_with["lambda"]
    assert abs(lw[0] / lw[1] - ls[0] / ls[1]) < 0.005              # 同 scale 回分（比例保持）

def test_fused_legs_end_to_end_schema_and_fusion():
    # 端到端：22 项市场价 ≥20 → marketFused=True；schema 齐全；族概率降序
    crs_odds = {"1:1": 4.0, "0:0": 8.0, "1:0": 7.0, "0:1": 9.0, "2:1": 9.0, "2:2": 16.0,
                "2:0": 15.0, "0:2": 18.0, "3:0": 30.0, "0:3": 40.0, "3:1": 20.0, "1:3": 30.0,
                "3:2": 40.0, "2:3": 40.0, "1:2": 12.0, "1:4": 90.0, "0:4": 80.0,
                "4:0": 60.0, "4:1": 80.0, "5:0": 150.0, "0:5": 200.0}
    freq, form, zh = _toy_stack()
    day = {"matches": [{"matchNumStr": "周六001", "league": "英超", "home": "主队甲",
                        "away": "客队乙", "crs": crs_odds, "ttg": {}}]}
    rows = fused_legs(day, freq, form, zh, CFG_TOY)
    r = rows[0]
    assert set(r) >= {"code", "match", "families", "gate", "p_final_top3",
                      "marketFused", "shrunk", "lambda"}
    assert r["marketFused"] is True
    probs = [f["prob"] for f in r["families"]]
    assert probs == sorted(probs, reverse=True) and len(r["families"]) == 5
    assert r["families"][0]["family"] == "draw"               # 市场模板同向（玩具同款市场）

def test_fused_legs_degrades_to_template_without_market():
    # 市场价 <20 项 → 纯模板降级 marketFused=False，仍出条目（铁律 8 空轮≠漏跑）
    freq, form, zh = _toy_stack()
    day = {"matches": [{"matchNumStr": "周六001", "league": "英超", "home": "主队甲",
                        "away": "客队乙", "crs": {"1:1": 6.0, "1:0": 7.0}, "ttg": {}}]}
    rows = fused_legs(day, freq, form, zh, CFG_TOY)
    assert len(rows) == 1 and rows[0]["marketFused"] is False
    assert rows[0]["families"]                                # 纯模板族排序仍在

def test_fused_legs_no_template_falls_to_global_pool():
    # 联赛无模板（欧冠类）→ global_pool 全局池；池也空 → 均匀先验仍出条目不崩
    freq, form, zh = _toy_stack()
    day = {"matches": [{"matchNumStr": "周六002", "league": "欧冠", "home": "X",
                        "away": "Y", "crs": {}, "ttg": {}}]}
    rows = fused_legs(day, freq, form, zh, CFG_TOY)
    assert len(rows) == 1 and rows[0]["lambda"] is None       # X/Y 无近况 → 纯模板
    rows_empty = fused_legs(day, {}, {}, {}, CFG_TOY)         # 全空 freq_table
    assert len(rows_empty) == 1 and rows_empty[0]["gate"]["maxProb"] > 0
