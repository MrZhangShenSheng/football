# -*- coding: utf-8 -*-
"""诊断：为什么两阶段模型小样本时崩溃"""
import json
import sys
import numpy as np
from collections import Counter, defaultdict
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path("engine/scripts/research")))

import score_family_model as sfm
from ticket_dynamic_k_backtest import load_hist_full

print("=" * 100)
print("诊断：为什么两阶段模型小样本时崩溃")
print("=" * 100)

# 加载数据
zh = {}
for tid, srcs in sfm.load_aliases().items():
    if srcs.get("zh"):
        zh[srcs["zh"]] = tid

tl = sfm.league_timeline()
hist = load_hist_full()


def analyze_cut(cut_date):
    """分析特定切分点的详细情况"""
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
    train_data = []
    blind_data = []
    tot_g = tot_n = 0

    for r in merged:
        kind, date, h, a, hg, ag = r[0], r[1], r[2], r[3], r[4], r[5]
        lg_gf = (tot_g / tot_n) if tot_n >= 50 else 2.6
        fv_h = stats[h].vector(0, lg_gf)
        fv_a = stats[a].vector(1, lg_gf)

        if kind == "L" and date < cut_date and fv_h[12] >= sfm.MIN_HIST and fv_a[12] >= sfm.MIN_HIST:
            train_data.append({
                "X": sfm.feature_row((fv_h, fv_a)),
                "score": (hg, ag),
                "total_goals": min(hg + ag, 5),
                "family": sfm.family_of(hg, ag),
            })
        elif kind == "B":
            blind_data.append({
                "X": sfm.feature_row((fv_h, fv_a)),
                "actual": blind[r[6]]["actual"],
                "odds": blind[r[6]]["odds"],
                "date": blind[r[6]]["date"],
            })

        if kind == "L":
            stats[h].add(hg, ag, True)
            stats[a].add(ag, hg, False)
            tot_g += hg + ag
            tot_n += 1

    X_tr = np.array([d["X"] for d in train_data])
    X_bl = np.array([b["X"] for b in blind_data])

    # ========== 基准模型 ==========
    y_fam = np.array([sfm.CLASSES.index(d["family"]) for d in train_data])
    model_fam = sfm.train_softmax(X_tr, y_fam, len(sfm.CLASSES))
    P_fam = sfm.predict_proba(model_fam, X_bl)

    fam_dist = defaultdict(Counter)
    for d in train_data:
        fam_dist[d["family"]][d["score"]] += 1

    packs_base = []
    for i, b in enumerate(blind_data):
        model_probs = {}
        for ci, cname in enumerate(sfm.CLASSES):
            tot = sum(fam_dist.get(cname, {}).values()) or 1
            for s, c in fam_dist.get(cname, {}).items():
                model_probs[s] = model_probs.get(s, 0.0) + P_fam[i][ci] * (c / tot)
        mdl_sorted = sorted(model_probs.items(), key=lambda kv: -kv[1])
        gap = mdl_sorted[0][1] - mdl_sorted[1][1] if len(mdl_sorted) >= 2 else 0
        packs_base.append({
            "date": b["date"], "actual": b["actual"], "odds": b["odds"],
            "model_sorted": mdl_sorted, "gap": gap,
        })

    # ========== 两阶段模型 ==========
    y_total = np.array([d["total_goals"] for d in train_data])
    n_classes = len(set(y_total))
    model_total = sfm.train_softmax(X_tr, y_total, n_classes)
    P_total = sfm.predict_proba(model_total, X_bl)

    score_by_total = defaultdict(Counter)
    for d in train_data:
        score_by_total[d["total_goals"]][d["score"]] += 1

    packs_two = []
    for i, b in enumerate(blind_data):
        model_probs = {}
        for total_g in range(n_classes):
            p_total = P_total[i][total_g]
            scores_in_total = score_by_total[total_g]
            total_cnt = sum(scores_in_total.values()) or 1
            for s, cnt in scores_in_total.items():
                model_probs[s] = model_probs.get(s, 0.0) + p_total * (cnt / total_cnt)
        mdl_sorted = sorted(model_probs.items(), key=lambda kv: -kv[1])
        gap = mdl_sorted[0][1] - mdl_sorted[1][1] if len(mdl_sorted) >= 2 else 0
        packs_two.append({
            "date": b["date"], "actual": b["actual"], "odds": b["odds"],
            "model_sorted": mdl_sorted, "gap": gap,
        })

    return {
        "n_blind": len(blind_data),
        "packs_base": packs_base,
        "packs_two": packs_two,
        "train_total_dist": Counter(d["total_goals"] for d in train_data),
        "blind_total_dist": Counter(min(b["actual"][0] + b["actual"][1], 5) for b in blind_data),
    }


# ========================================
# 对比好/坏切分点
# ========================================

good_cut = "2026-01-01"  # 两阶段 +17.5%
bad_cut = "2026-06-01"   # 两阶段 -42.9%

print(f"\n【好切分点】{good_cut}")
good = analyze_cut(good_cut)
print(f"盲测场数：{good['n_blind']}")

print(f"\n【坏切分点】{bad_cut}")
bad = analyze_cut(bad_cut)
print(f"盲测场数：{bad['n_blind']}")


# ========================================
# 1. 总进球分布对比
# ========================================

print("\n" + "=" * 100)
print("1. 总进球分布对比（训练 vs 盲测）")
print("=" * 100)

print(f"\n{'总进球':>8} | {'好-训练':>10} {'好-盲测':>10} {'差异':>8} | {'坏-训练':>10} {'坏-盲测':>10} {'差异':>8}")
print("-" * 85)

for total in range(6):
    g_tr = good["train_total_dist"].get(total, 0)
    g_bl = good["blind_total_dist"].get(total, 0)
    g_tr_pct = g_tr / sum(good["train_total_dist"].values()) * 100
    g_bl_pct = g_bl / sum(good["blind_total_dist"].values()) * 100
    g_diff = g_bl_pct - g_tr_pct

    b_tr = bad["train_total_dist"].get(total, 0)
    b_bl = bad["blind_total_dist"].get(total, 0)
    b_tr_pct = b_tr / sum(bad["train_total_dist"].values()) * 100
    b_bl_pct = b_bl / sum(bad["blind_total_dist"].values()) * 100 if sum(bad["blind_total_dist"].values()) > 0 else 0
    b_diff = b_bl_pct - b_tr_pct

    print(f"{total:>8} | {g_tr_pct:>9.1f}% {g_bl_pct:>9.1f}% {g_diff:>+7.1f}% | {b_tr_pct:>9.1f}% {b_bl_pct:>9.1f}% {b_diff:>+7.1f}%")


# ========================================
# 2. 预测集中度对比
# ========================================

print("\n" + "=" * 100)
print("2. 预测集中度对比")
print("=" * 100)

for name, cut, data in [("好切分点", good_cut, good), ("坏切分点", bad_cut, bad)]:
    print(f"\n【{name}】")

    # 基准模型
    base_top1 = Counter(p["model_sorted"][0][0] for p in data["packs_base"])
    two_top1 = Counter(p["model_sorted"][0][0] for p in data["packs_two"])

    print(f"  基准模型 Top1 预测分布：")
    for s, c in base_top1.most_common(5):
        print(f"    {s}: {c} ({c/len(data['packs_base'])*100:.1f}%)")

    print(f"  两阶段模型 Top1 预测分布：")
    for s, c in two_top1.most_common(5):
        print(f"    {s}: {c} ({c/len(data['packs_two'])*100:.1f}%)")


# ========================================
# 3. Gap 分布对比
# ========================================

print("\n" + "=" * 100)
print("3. Gap 分布对比")
print("=" * 100)

for name, data in [("好切分点", good), ("坏切分点", bad)]:
    print(f"\n【{name}】")

    base_gaps = [p["gap"] for p in data["packs_base"]]
    two_gaps = [p["gap"] for p in data["packs_two"]]

    print(f"  基准模型 gap: 均值={np.mean(base_gaps):.4f}, 中位数={np.median(base_gaps):.4f}, max={max(base_gaps):.4f}")
    print(f"  两阶段模型 gap: 均值={np.mean(two_gaps):.4f}, 中位数={np.median(two_gaps):.4f}, max={max(two_gaps):.4f}")


# ========================================
# 4. Top1 命中率对比
# ========================================

print("\n" + "=" * 100)
print("4. Top1/Top2 命中率对比")
print("=" * 100)

for name, data in [("好切分点", good), ("坏切分点", bad)]:
    print(f"\n【{name}】")

    for model_name, packs in [("基准", data["packs_base"]), ("两阶段", data["packs_two"])]:
        top1_hit = sum(1 for p in packs if p["model_sorted"][0][0] == p["actual"])
        top2_hit = sum(1 for p in packs if p["actual"] in [s for s, _ in p["model_sorted"][:2]])
        n = len(packs)
        print(f"  {model_name}: Top1={top1_hit}/{n} ({top1_hit/n*100:.1f}%), Top2={top2_hit}/{n} ({top2_hit/n*100:.1f}%)")


# ========================================
# 5. 实际比分分布
# ========================================

print("\n" + "=" * 100)
print("5. 实际比分分布对比")
print("=" * 100)

for name, data in [("好切分点", good), ("坏切分点", bad)]:
    print(f"\n【{name}】实际比分 Top10：")
    actual_dist = Counter(p["actual"] for p in data["packs_base"])
    for s, c in actual_dist.most_common(10):
        pct = c / len(data["packs_base"]) * 100
        print(f"  {s}: {c} ({pct:.1f}%)")


# ========================================
# 6. 两阶段模型的核心问题
# ========================================

print("\n" + "=" * 100)
print("6. 核心问题分析")
print("=" * 100)

# 两阶段模型几乎只预测 (1,1)
# 检查 (1,1) 的实际出现率

for name, data in [("好切分点", good), ("坏切分点", bad)]:
    # 两阶段预测 (1,1) 的次数
    two_11_pred = sum(1 for p in data["packs_two"] if p["model_sorted"][0][0] == (1,1))
    # 实际 (1,1) 的次数
    actual_11 = sum(1 for p in data["packs_two"] if p["actual"] == (1,1))
    # 两阶段预测 (1,1) 且命中的次数
    two_11_hit = sum(1 for p in data["packs_two"]
                     if p["model_sorted"][0][0] == (1,1) and p["actual"] == (1,1))

    n = len(data["packs_two"])
    print(f"\n【{name}】")
    print(f"  两阶段预测(1,1)：{two_11_pred}/{n} ({two_11_pred/n*100:.1f}%)")
    print(f"  实际出现(1,1)：{actual_11}/{n} ({actual_11/n*100:.1f}%)")
    print(f"  预测(1,1)命中：{two_11_hit}/{two_11_pred} ({two_11_hit/two_11_pred*100:.1f}%)" if two_11_pred > 0 else "  预测(1,1)命中：N/A")
