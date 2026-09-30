# -*- coding: utf-8 -*-
"""比分模型优化 —— 方向A：两阶段预测（总进球→比分）"""
import json
import sys
import numpy as np
from collections import Counter, defaultdict
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path("engine/scripts/research")))

import score_family_model as sfm
from ticket_dynamic_k_backtest import load_hist_full

UNIT = 2.0

print("=" * 100)
print("比分模型优化 —— 两阶段预测（总进球数 → 比分）")
print("=" * 100)

# ========================================
# 数据准备
# ========================================

zh = {}
for tid, srcs in sfm.load_aliases().items():
    if srcs.get("zh"):
        zh[srcs["zh"]] = tid

tl = sfm.league_timeline()
hist = load_hist_full()

cut = "2026-01-01"

# 构建训练数据
merged = [("L", d, h, a, hg, ag) for d, h, a, hg, ag in tl]
merged.sort(key=lambda r: r[1])

stats = defaultdict(sfm.TeamStats)
train_data = []
tot_g = tot_n = 0

for r in merged:
    date, h, a, hg, ag = r[1], r[2], r[3], r[4], r[5]
    lg_gf = (tot_g / tot_n) if tot_n >= 50 else 2.6
    fv_h = stats[h].vector(0, lg_gf)
    fv_a = stats[a].vector(1, lg_gf)

    if date < cut and fv_h[12] >= sfm.MIN_HIST and fv_a[12] >= sfm.MIN_HIST:
        train_data.append({
            "X": sfm.feature_row((fv_h, fv_a)),
            "score": (hg, ag),
            "total_goals": min(hg + ag, 5),  # 5+合并
            "home_goals": min(hg, 4),  # 4+合并
            "away_goals": min(ag, 4),
            "family": sfm.family_of(hg, ag),
        })

    stats[h].add(hg, ag, True)
    stats[a].add(ag, hg, False)
    tot_g += hg + ag
    tot_n += 1

print(f"训练样本：{len(train_data)}")

# 总进球分布
total_dist = Counter(d["total_goals"] for d in train_data)
print(f"\n总进球分布：")
for t in sorted(total_dist.keys()):
    pct = total_dist[t] / len(train_data) * 100
    print(f"  {t}球：{total_dist[t]} ({pct:.1f}%)")

# 每个总进球数下的比分分布
score_by_total = defaultdict(Counter)
for d in train_data:
    score_by_total[d["total_goals"]][d["score"]] += 1


# ========================================
# 盲测数据
# ========================================

blind = []
for m in hist:
    if m["date"] < cut:
        continue
    hid, aid = zh.get(m["home_zh"]), zh.get(m["away_zh"])
    if hid and aid:
        blind.append({**m, "hid": hid, "aid": aid})

merged_full = [("L", d, h, a, hg, ag, None) for d, h, a, hg, ag in tl]
merged_full += [("B", m["date"], m["hid"], m["aid"], m["actual"][0], m["actual"][1], i)
                for i, m in enumerate(blind)]
merged_full.sort(key=lambda r: (r[1], 0 if r[0] == "L" else 1))

stats2 = defaultdict(sfm.TeamStats)
blind_data = []
tot_g2 = tot_n2 = 0

for r in merged_full:
    kind, date, h, a, hg, ag = r[0], r[1], r[2], r[3], r[4], r[5]
    lg_gf = (tot_g2 / tot_n2) if tot_n2 >= 50 else 2.6
    fv_h = stats2[h].vector(0, lg_gf)
    fv_a = stats2[a].vector(1, lg_gf)

    if kind == "B":
        blind_data.append({
            "X": sfm.feature_row((fv_h, fv_a)),
            "actual": blind[r[6]]["actual"],
            "odds": blind[r[6]]["odds"],
            "date": blind[r[6]]["date"],
        })

    if kind == "L":
        stats2[h].add(hg, ag, True)
        stats2[a].add(ag, hg, False)
        tot_g2 += hg + ag
        tot_n2 += 1

print(f"盲测样本：{len(blind_data)}")


# ========================================
# 方法1：基准（原始族模型）
# ========================================

print("\n" + "=" * 100)
print("方法1：基准（原始族模型）")
print("=" * 100)

X_tr = np.array([d["X"] for d in train_data])
y_fam = np.array([sfm.CLASSES.index(d["family"]) for d in train_data])
X_bl = np.array([b["X"] for b in blind_data])

model_fam = sfm.train_softmax(X_tr, y_fam, len(sfm.CLASSES))
P_fam = sfm.predict_proba(model_fam, X_bl)

# 族内比分频率
fam_dist = defaultdict(Counter)
for d in train_data:
    fam_dist[d["family"]][d["score"]] += 1

# 构建预测包
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


# ========================================
# 方法2：两阶段（总进球→比分）
# ========================================

print("\n" + "=" * 100)
print("方法2：两阶段预测（总进球→比分）")
print("=" * 100)

# 第一阶段：预测总进球数
y_total = np.array([d["total_goals"] for d in train_data])
n_total_classes = len(set(y_total))
print(f"总进球类别数：{n_total_classes}")

model_total = sfm.train_softmax(X_tr, y_total, n_total_classes)
P_total = sfm.predict_proba(model_total, X_bl)

# 构建预测包
packs_two_stage = []
for i, b in enumerate(blind_data):
    model_probs = {}

    # 对每个总进球数
    for total_g in range(n_total_classes):
        p_total = P_total[i][total_g]
        # 该总进球数下的比分分布
        scores_in_total = score_by_total[total_g]
        total_cnt = sum(scores_in_total.values()) or 1

        for s, cnt in scores_in_total.items():
            model_probs[s] = model_probs.get(s, 0.0) + p_total * (cnt / total_cnt)

    mdl_sorted = sorted(model_probs.items(), key=lambda kv: -kv[1])
    gap = mdl_sorted[0][1] - mdl_sorted[1][1] if len(mdl_sorted) >= 2 else 0
    packs_two_stage.append({
        "date": b["date"], "actual": b["actual"], "odds": b["odds"],
        "model_sorted": mdl_sorted, "gap": gap,
    })


# ========================================
# 方法3：两阶段（主队进球+客队进球→比分）
# ========================================

print("\n" + "=" * 100)
print("方法3：双模型（主队进球 + 客队进球）")
print("=" * 100)

# 主队进球模型
y_home = np.array([d["home_goals"] for d in train_data])
n_home_classes = len(set(y_home))
model_home = sfm.train_softmax(X_tr, y_home, n_home_classes)
P_home = sfm.predict_proba(model_home, X_bl)

# 客队进球模型
y_away = np.array([d["away_goals"] for d in train_data])
n_away_classes = len(set(y_away))
model_away = sfm.train_softmax(X_tr, y_away, n_away_classes)
P_away = sfm.predict_proba(model_away, X_bl)

print(f"主队进球类别：{n_home_classes}，客队进球类别：{n_away_classes}")

# 构建预测包（独立假设：P(h,a) = P(h) * P(a)）
packs_dual = []
for i, b in enumerate(blind_data):
    model_probs = {}

    for hg in range(n_home_classes):
        for ag in range(n_away_classes):
            p = P_home[i][hg] * P_away[i][ag]
            model_probs[(hg, ag)] = p

    mdl_sorted = sorted(model_probs.items(), key=lambda kv: -kv[1])
    gap = mdl_sorted[0][1] - mdl_sorted[1][1] if len(mdl_sorted) >= 2 else 0
    packs_dual.append({
        "date": b["date"], "actual": b["actual"], "odds": b["odds"],
        "model_sorted": mdl_sorted, "gap": gap,
    })


# ========================================
# 回测对比
# ========================================

def run_backtest(packs, gap_threshold=0.05):
    """回测"""
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

        day_sorted = sorted(day_packs, key=lambda p: -p["gap"])[:2]
        p1, p2 = day_sorted[0], day_sorted[1]

        k1 = 1 if p1["gap"] > gap_threshold else 2
        k2 = 1 if p2["gap"] > gap_threshold else 2

        picks1 = [(s, p1["odds"].get(s, 999)) for s, prob in p1["model_sorted"][:k1]]
        picks2 = [(s, p2["odds"].get(s, 999)) for s, prob in p2["model_sorted"][:k2]]

        if not picks1 or not picks2:
            continue

        all_bets = list(product(picks1, picks2))
        cost = len(all_bets) * UNIT
        payout = 0.0

        for (s1, o1), (s2, o2) in all_bets:
            if s1 == p1["actual"] and s2 == p2["actual"]:
                payout += UNIT * o1 * o2

        total_cost += cost
        total_payout += payout
        n_tickets += 1
        if payout > 0:
            n_hits += 1

    profit = (total_payout - total_cost) / total_cost if total_cost > 0 else -1
    return {
        "n_tickets": n_tickets,
        "n_hits": n_hits,
        "total_cost": total_cost,
        "total_payout": total_payout,
        "profit": profit,
    }


def analyze_predictions(packs):
    """分析预测分布"""
    pred_top1 = Counter()
    hit_cnt = 0
    for p in packs:
        pred = p["model_sorted"][0][0]
        pred_top1[pred] += 1
        if pred == p["actual"]:
            hit_cnt += 1
    return {
        "unique": len(pred_top1),
        "top1_hit": hit_cnt,
        "top1_hit_rate": hit_cnt / len(packs) * 100,
        "top3_scores": pred_top1.most_common(3),
    }


print("\n" + "=" * 100)
print("回测对比")
print("=" * 100)

methods = [
    ("基准（族模型）", packs_base),
    ("两阶段（总进球→比分）", packs_two_stage),
    ("双模型（主+客进球）", packs_dual),
]

print(f"\n{'方法':<25} {'盈利率':>12} {'命中':>8} {'成本':>10} {'派彩':>10} {'Top1命中率':>12} {'预测种类':>10}")
print("-" * 100)

for name, packs in methods:
    r = run_backtest(packs)
    a = analyze_predictions(packs)
    mark = "✅" if r["profit"] > 0 else ""
    print(f"{name:<25} {r['profit']*100:>+11.2f}% {r['n_hits']:>8} {r['total_cost']:>10.0f} {r['total_payout']:>10.0f} "
          f"{a['top1_hit_rate']:>11.1f}% {a['unique']:>10} {mark}")

print("-" * 100)

# 预测分布对比
print(f"\n【预测分布对比】")
for name, packs in methods:
    a = analyze_predictions(packs)
    print(f"\n{name}:")
    print(f"  Top1 命中率：{a['top1_hit_rate']:.1f}%")
    print(f"  预测种类：{a['unique']}")
    print(f"  Top3 预测：{a['top3_scores']}")