# -*- coding: utf-8 -*-
"""特征优化实验 —— 移除有害特征，验证效果"""
import json
import sys
import numpy as np
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path("engine/scripts/research")))

import score_family_model as sfm
from ticket_dynamic_k_backtest import load_hist_full, simulate_2c1_dynamic

print("=" * 90)
print("特征优化实验")
print("=" * 90)

# 原始特征（26维）的索引
# 主队: 0-12, 客队: 13-25
# 有害特征：h_n_matches (12), a_n_matches (25)
# 冗余特征：h_lg_gf_ratio (11), a_lg_gf_ratio (24)

def feature_row_v3(fv):
    """v3 优化版：移除有害特征 n_matches 和冗余特征 lg_gf_ratio

    原始 26 维 → 22 维
    移除：索引 11, 12 (主队), 24, 25 (客队)
    """
    h = fv[0]
    a = fv[1]
    # 主队：取 0-10（跳过 11=lg_gf_ratio, 12=n_matches）
    # 客队：取 0-10（跳过 11=lg_gf_ratio, 12=n_matches）
    return h[:11] + a[:11]


def build_model_packs_custom(cut_date, feature_fn):
    """用自定义特征函数构建模型包"""
    zh = {}
    for tid, srcs in sfm.load_aliases().items():
        if srcs.get("zh"):
            zh[srcs["zh"]] = tid

    tl = sfm.league_timeline()
    hist = load_hist_full()

    blind = []
    for m in hist:
        if m["date"] < cut_date:
            continue
        hid, aid = zh.get(m["home_zh"]), zh.get(m["away_zh"])
        if hid and aid:
            blind.append({**m, "hid": hid, "aid": aid})

    merged = [("L", d, h, a, hg, ag, None) for d, h, a, hg, ag in tl]
    merged += [("B", m["date"], m["hid"], m["aid"], m["actual"][0], m["actual"][1], i)
               for i, m in enumerate(blind)]
    merged.sort(key=lambda r: (r[1], 0 if r[0] == "L" else 1))

    stats = defaultdict(sfm.TeamStats)
    X_tr, y_tr, X_bl, meta = [], [], [], []
    tot_g = tot_n = 0

    for r in merged:
        kind, date, h, a, hg, ag = r[0], r[1], r[2], r[3], r[4], r[5]
        lg_gf = (tot_g / tot_n) if tot_n >= 50 else 2.6
        fv_h = stats[h].vector(0, lg_gf)
        fv_a = stats[a].vector(1, lg_gf)

        if kind == "L" and date < cut_date and fv_h[12] >= sfm.MIN_HIST and fv_a[12] >= sfm.MIN_HIST:
            X_tr.append(feature_fn((fv_h, fv_a)))
            y_tr.append(sfm.CLASSES.index(sfm.family_of(hg, ag)))
        elif kind == "B":
            meta.append(blind[r[6]])
            X_bl.append(feature_fn((fv_h, fv_a)))

        stats[h].add(hg, ag, True)
        stats[a].add(ag, hg, False)
        tot_g += hg + ag
        tot_n += 1

    model = sfm.train_softmax(X_tr, y_tr, len(sfm.CLASSES))
    P = sfm.predict_proba(model, X_bl)

    fam_dist = defaultdict(Counter)
    for r in merged:
        if r[0] == "L" and r[1] < cut_date:
            fam_dist[sfm.family_of(r[4], r[5])][(r[4], r[5])] += 1

    packs = []
    for i, m in enumerate(meta):
        model_probs = {}
        for ci, cname in enumerate(sfm.CLASSES):
            tot = sum(fam_dist.get(cname, {}).values()) or 1
            for s, c in fam_dist.get(cname, {}).items():
                model_probs[s] = model_probs.get(s, 0.0) + P[i][ci] * (c / tot)

        mdl_sorted = sorted(model_probs.items(), key=lambda kv: -kv[1])
        gap = mdl_sorted[0][1] - mdl_sorted[1][1] if len(mdl_sorted) >= 2 else 0

        packs.append({
            "date": m["date"],
            "actual": m["actual"],
            "odds": m["odds"],
            "model_sorted": mdl_sorted,
            "gap": gap,
        })

    return packs, len(X_tr), len(X_bl)


def run_backtest(packs):
    """跑动态选腿回测"""
    from itertools import product

    by_day = defaultdict(list)
    for p in packs:
        by_day[p["date"]].append(p)

    total_cost = 0.0
    total_payout = 0.0
    n_tickets = 0
    n_hits = 0

    for day in sorted(by_day):
        day_packs = by_day[day]
        if len(day_packs) < 2:
            continue

        day_sorted = sorted(day_packs, key=lambda p: -p["gap"])
        p1, p2 = day_sorted[0], day_sorted[1]

        result = simulate_2c1_dynamic(p1, p2, "dynamic_1_2", (0.05, 0.02))
        if result is None:
            continue

        total_cost += result["cost"]
        total_payout += result["payout"]
        n_tickets += 1
        if result["hit"]:
            n_hits += 1

    profit = (total_payout - total_cost) / total_cost if total_cost > 0 else 0
    return {
        "n_tickets": n_tickets,
        "total_cost": total_cost,
        "total_payout": total_payout,
        "n_hits": n_hits,
        "profit": profit,
    }


def calc_family_acc(packs, cut_date):
    """计算族预测准确率"""
    zh = {}
    for tid, srcs in sfm.load_aliases().items():
        if srcs.get("zh"):
            zh[srcs["zh"]] = tid

    correct = 0
    total = 0

    for p in packs:
        # 从 model_sorted 推断预测的族
        pred_score = p["model_sorted"][0][0]
        pred_fam = sfm.family_of(pred_score[0], pred_score[1])
        actual_fam = sfm.family_of(p["actual"][0], p["actual"][1])

        total += 1
        if pred_fam == actual_fam:
            correct += 1

    return correct / total if total > 0 else 0


# ========================================
# 实验对比
# ========================================

cut = "2026-01-01"

# 定义多个特征版本
def feature_row_v3a(fv):
    """v3a：只移除 n_matches（保留 lg_gf_ratio）→ 24维"""
    h = fv[0]
    a = fv[1]
    # 移除索引 12 (h_n_matches) 和 25 (a_n_matches)
    return h[:12] + a[:12]

def feature_row_v3b(fv):
    """v3b：只移除 lg_gf_ratio（保留 n_matches）→ 24维"""
    h = fv[0]
    a = fv[1]
    # 移除索引 11 (h_lg_gf_ratio) 和 24 (a_lg_gf_ratio)
    return h[:11] + [h[12]] + a[:11] + [a[12]]

def feature_row_v3c(fv):
    """v3c：增加交叉特征（进球差、失球差）→ 28维"""
    h = fv[0]
    a = fv[1]
    base = h[:13] + a[:13]
    # 新特征
    gf_diff = h[0] - a[0]  # 主队进球 - 客队进球
    ga_diff = h[1] - a[1]  # 主队失球 - 客队失球
    return base + [gf_diff, ga_diff]

def feature_row_v3d(fv):
    """v3d：移除有害 + 增加交叉 → 24维"""
    h = fv[0]
    a = fv[1]
    # 移除 n_matches，增加交叉特征
    base = h[:12] + a[:12]
    gf_diff = h[0] - a[0]
    ga_diff = h[1] - a[1]
    return base

experiments = [
    ("原始 26 维", sfm.feature_row),
    ("v3a: 移除 n_matches (24维)", feature_row_v3a),
    ("v3b: 移除 lg_gf_ratio (24维)", feature_row_v3b),
    ("v3c: 增加交叉特征 (28维)", feature_row_v3c),
    ("v3: 移除 n_matches + lg_gf_ratio (22维)", feature_row_v3),
]

print("\n【多版本特征对比】")
print("=" * 100)
print(f"{'版本':<40} {'维度':>6} {'族准确率':>10} {'命中':>6} {'成本':>8} {'派彩':>8} {'盈利率':>10}")
print("-" * 100)

results = []
for name, fn in experiments:
    packs, n_tr, n_bl = build_model_packs_custom(cut, fn)
    result = run_backtest(packs)
    fam_acc = calc_family_acc(packs, cut)

    dim = len(fn(([0]*14, [0]*14)))
    mark = "✅" if result["profit"] > 0 else ""
    print(f"{name:<40} {dim:>6} {fam_acc*100:>9.2f}% {result['n_hits']:>6} "
          f"{result['total_cost']:>8.0f} {result['total_payout']:>8.0f} {result['profit']*100:>+9.2f}% {mark}")

    results.append({
        "name": name,
        "dim": dim,
        "fam_acc": fam_acc,
        "profit": result["profit"],
        "n_hits": result["n_hits"],
        "cost": result["total_cost"],
        "payout": result["total_payout"],
    })

print("-" * 100)

# 最优
best = max(results, key=lambda x: x["profit"])
print(f"\n最优版本：{best['name']}")
print(f"  盈利率：{best['profit']*100:+.2f}%")
print(f"  族准确率：{best['fam_acc']*100:.2f}%")
