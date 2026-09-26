# engine/scripts/tests/test_crs_fusion_audit.py
"""crs_fusion_audit 回测验收器核心函数测试（TDD·离线注入 fixtures 不触网）。
覆盖：actual→池键映射 / logloss / 族 top1 命中统计 / 总进球分桶校准 /
配对差 bootstrap CI / three_dists 三方分布装配 / 输出 JSON 键卫生。
开发者 sszhang"""
import json
import math

import pytest

from crs_fusion import EPS_MARKET, fuse_crs
from crs_fusion_audit import (
    actual_pool_key,
    bucket_calibration,
    family_top1_stats,
    logloss,
    paired_bootstrap_ci,
    tg_buckets,
    three_dists,
)

# ---- actual_pool_key：实际比分 → CRS 池键（池外折叠进兜底代表键）----

def test_actual_pool_key_in_pool_unchanged():
    assert actual_pool_key(1, 0) == (1, 0)
    assert actual_pool_key(5, 2) == (5, 2)          # 挂牌极端比分原样
    assert actual_pool_key(4, 3) == (4, 3)          # 胜其他代表键本身

def test_actual_pool_key_out_of_pool_folds_to_rep():
    assert actual_pool_key(6, 0) == (4, 3)          # 胜其他
    assert actual_pool_key(5, 3) == (4, 3)
    assert actual_pool_key(5, 5) == (4, 4)          # 平其他
    assert actual_pool_key(0, 7) == (3, 4)          # 负其他
    assert actual_pool_key(0, 6) == (3, 4)
    assert actual_pool_key(2, 5) == (2, 5)          # 2:x 全列挂牌 → 池内原样

# ---- logloss：分布对实际比分（含池外映射与缺项 ε 兜底）----

def test_logloss_basic():
    dist = {(1, 0): 0.5, (0, 0): 0.5}
    assert logloss(dist, 1, 0) == pytest.approx(-math.log(0.5))

def test_logloss_out_of_pool_actual_uses_rep_key():
    dist = {(4, 3): 0.02, (1, 0): 0.98}
    assert logloss(dist, 6, 0) == pytest.approx(-math.log(0.02))

def test_logloss_missing_key_falls_back_to_eps():
    dist = {(1, 0): 1.0}                            # 市场臂无代表键场景
    assert logloss(dist, 5, 5) == pytest.approx(-math.log(EPS_MARKET))

# ---- family_top1_stats：top1 族实际命中 vs 融合期望 ----

def _toy_family_rows():
    return [
        {"top1": {"family": "draw", "prob": 0.5, "members": [(0, 0), (1, 1), (2, 2)]},
         "actual": (1, 1)},                          # 命中
        {"top1": {"family": "home_clean", "prob": 0.4, "members": [(1, 0), (2, 0), (3, 0)]},
         "actual": (0, 1)},                          # 未命中
    ]

def test_family_top1_stats_ratio_and_pass():
    out = family_top1_stats(_toy_family_rows())
    assert out["n"] == 2
    assert out["hits"] == 1
    assert out["hitRate"] == pytest.approx(0.5)
    assert out["expectedRate"] == pytest.approx(0.45)   # (0.5+0.4)/2
    assert out["ratio"] == pytest.approx(0.5 / 0.45)
    assert out["pass"] is True                          # ratio 1.11 ≥ 0.9

def test_family_top1_stats_fail_when_actual_below_expected():
    rows = _toy_family_rows() + [
        {"top1": {"family": "draw", "prob": 0.5, "members": [(0, 0), (1, 1), (2, 2)]},
         "actual": (3, 3)},                          # 连续未命中压低 ratio
        {"top1": {"family": "draw", "prob": 0.5, "members": [(0, 0), (1, 1), (2, 2)]},
         "actual": (0, 1)},
        {"top1": {"family": "draw", "prob": 0.5, "members": [(0, 0), (1, 1), (2, 2)]},
         "actual": (0, 2)},
    ]
    out = family_top1_stats(rows)
    assert out["hits"] == 1 and out["n"] == 5
    assert out["ratio"] < 0.9 and out["pass"] is False  # 0.2/0.48=0.42

def test_family_top1_stats_empty_rows_safe():
    out = family_top1_stats([])
    assert out["n"] == 0 and out["pass"] is False

# ---- tg_buckets：31 维比分分布 → 0/1/2/3/4+ 五档 ----

def _toy_dist():
    return {(0, 0): 0.10, (1, 0): 0.20, (0, 1): 0.15, (1, 1): 0.10, (2, 0): 0.05,
            (2, 1): 0.20, (0, 3): 0.05, (3, 3): 0.05, (4, 3): 0.10}

def test_tg_buckets_partition():
    b = tg_buckets(_toy_dist())
    assert b["0"] == pytest.approx(0.10)
    assert b["1"] == pytest.approx(0.35)            # 1:0 + 0:1
    assert b["2"] == pytest.approx(0.15)            # 1:1 + 2:0
    assert b["3"] == pytest.approx(0.25)            # 2:1 + 0:3
    assert b["4+"] == pytest.approx(0.15)           # 3:3 + 4:3
    assert sum(b.values()) == pytest.approx(sum(_toy_dist().values()))

# ---- bucket_calibration：预测均值 vs 实际频率 ----

def test_bucket_calibration_aggregates():
    b1 = {"0": 0.1, "1": 0.35, "2": 0.15, "3": 0.2, "4+": 0.2}
    b2 = {"0": 0.2, "1": 0.3, "2": 0.2, "3": 0.2, "4+": 0.1}
    out = bucket_calibration([(b1, 1), (b2, 5)])
    assert out["n"] == 2
    assert out["buckets"]["1"]["predMean"] == pytest.approx(0.325)
    assert out["buckets"]["1"]["actualFreq"] == pytest.approx(0.5)
    assert out["buckets"]["4+"]["predMean"] == pytest.approx(0.15)
    assert out["buckets"]["4+"]["actualFreq"] == pytest.approx(0.5)
    assert out["tail4plus"]["predMean"] == pytest.approx(0.15)
    assert out["tail4plus"]["actualShare"] == pytest.approx(0.5)

def test_bucket_calibration_tg_to_bucket_mapping():
    # 实际 tg=0/1/2/3 直落档，tg>=4 全进 4+（7 球也是）
    b = {"0": 0.0, "1": 0.0, "2": 0.0, "3": 0.0, "4+": 1.0}
    out = bucket_calibration([(b, 0), (b, 7)])
    assert out["buckets"]["0"]["actualFreq"] == pytest.approx(0.5)
    assert out["buckets"]["4+"]["actualFreq"] == pytest.approx(0.5)

# ---- paired_bootstrap_ci：配对差均值 + CI95 ----

def test_bootstrap_constant_diffs_degenerate_ci():
    mean, lo, hi = paired_bootstrap_ci([1.0] * 50)
    assert mean == pytest.approx(1.0)
    assert lo == pytest.approx(1.0) and hi == pytest.approx(1.0)

def test_bootstrap_deterministic_with_seed():
    diffs = [0.3, -0.1, 0.5, 0.2, -0.4, 0.6, 0.1, -0.2, 0.4, 0.3] * 5
    a = paired_bootstrap_ci(diffs, n_boot=200, seed=7)
    b = paired_bootstrap_ci(diffs, n_boot=200, seed=7)
    assert a == b

def test_bootstrap_clearly_positive_excludes_zero():
    diffs = [1.0] * 30 + [0.2] * 10
    mean, lo, hi = paired_bootstrap_ci(diffs, n_boot=500, seed=1)
    assert mean > 0 and lo > 0 and lo <= mean <= hi

# ---- three_dists：三方分布装配（注入 freq_table/form 离线跑）----

def _toy_fixture():
    from collections import Counter
    freq_table = {"toy-lg": Counter({"1:0": 40, "1:1": 30, "0:1": 20, "2:1": 10, "__n": 100})}
    crs = {f"{h}:{a}": 3.0 + (h + a) for h in range(3) for a in range(3)}
    crs.update({"3:0": 15.0, "3:1": 20.0, "3:2": 25.0, "3:3": 30.0,
                "4:0": 40.0, "4:1": 45.0, "4:2": 50.0, "5:0": 60.0, "5:1": 70.0, "5:2": 80.0,
                "胜其他": 90.0, "平其他": 95.0, "负其他": 85.0})   # 真实存档同款非数值兜底键
    m = {"matchNumStr": "周六001", "league": "玩具未知联赛", "home": "甲", "away": "乙",
         "crs": crs, "ttg": None}
    return m, freq_table, {}, {}                    # form/zh 空 → λ None 纯模板

def test_three_dists_sums_and_fusion_math():
    m, ft, form, zh = _toy_fixture()
    out = three_dists(m, ft, form, zh)
    for arm in ("tpl", "mkt", "fused"):
        assert sum(out[arm].values()) == pytest.approx(1.0, abs=1e-9)
    assert out["lam"] is None and out["shrunk"] is False
    # 融合 = 对数意见池比值关系逐项可验
    q, p, pf = out["tpl"], out["mkt"], out["fused"]
    r = 0.286
    for s in ((1, 0), (1, 1), (2, 1)):
        expect = q[s] ** r * p[s] ** (1 - r)
        got_ratio = pf[s] / pf[(1, 0)]
        want_ratio = expect / (q[(1, 0)] ** r * p[(1, 0)] ** (1 - r))
        assert got_ratio == pytest.approx(want_ratio, rel=1e-9)
    assert out["gate"]["maxProb"] > 0

def test_three_dists_template_is_smoothed_pure_freq():
    m, ft, form, zh = _toy_fixture()
    out = three_dists(m, ft, form, zh)
    # λ=None → shifted_q=纯频率 → 平滑 (c/n + α/N)/(1+31α/N)，α=0.5
    q10 = out["tpl"][(1, 0)]
    assert q10 == pytest.approx((0.40 + 0.5 / 100) / (1 + 31 * 0.5 / 100), rel=1e-9)

def test_three_dists_fused_includes_template_only_keys():
    # 市场无代表键 → 融合后代表键仍 >0（ε 兜底，不零吸收）
    m, ft, form, zh = _toy_fixture()
    out = three_dists(m, ft, form, zh)
    assert out["fused"][(4, 4)] > 0 and out["mkt"].get((4, 4)) is None

def test_three_dists_market_below_min_items_degrades():
    m, ft, form, zh = _toy_fixture()
    m = dict(m, crs={"1:0": 2.0, "0:0": 8.0})       # 2 项 < 20 → 市场臂空
    out = three_dists(m, ft, form, zh)
    assert out["mkt"] == {} and out["fused"] == out["tpl"]

def test_three_dists_json_key_hygiene_via_summary():
    # (h,a) 元组键不得进输出 JSON：per-match 摘要须可 json.dumps（元组键会 TypeError）
    m, ft, form, zh = _toy_fixture()
    out = three_dists(m, ft, form, zh)
    summary = {"top1": out["families"][0]["family"],
               "top1Scores": [f"{s[0]}:{s[1]}" for s in out["families"][0]["members"]]}
    assert json.dumps(summary, ensure_ascii=False)   # 元组键混入即抛 TypeError
