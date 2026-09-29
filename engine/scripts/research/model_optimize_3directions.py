# -*- coding: utf-8 -*-
"""比分模型优化实验 —— 三个方向逐一测试"""
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
print("比分模型优化实验")
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
blind = []
for m in hist:
    if m["date"] < cut:
        continue
    hid, aid = zh.get(m["home_zh"]), zh.get(m["away_zh"])
    if hid and aid:
        blind.append({**m, "hid": hid, "aid": aid})

merged = [("L", d, h, a, hg, ag, None) for d, h, a, hg, ag in tl]
merged += [("B", m["date"], m["hid"], m["aid"], m["actual"][0], m["actual"][1], i)
           for i, m in enumerate(blind)]
merged.sort(key=lambda r: (r[1], 0 if r[0] == "L" else 1))

# 收集训练数据
stats = defaultdict(sfm.TeamStats)
train_data = []
blind_data = []
tot_g = tot_n = 0

for r in merged:
    kind, date, h, a, hg, ag = r[0], r[1], r[2], r[3], r[4], r[5]
    lg_gf = (tot_g / tot_n) if tot_n >= 50 else 2.6
    fv_h = stats[h].vector(0, lg_gf)
    fv_a = stats[a].vector(1, lg_gf)

    if kind == "L" and date < cut and fv_h[12] >= sfm.MIN_HIST and fv_a[12] >= sfm.MIN_HIST:
        train_data.append({
            "X": sfm.feature_row((fv_h, fv_a)),
            "family": sfm.family_of(hg, ag),
            "score": (hg, ag),
        })
    elif kind == "B":
        blind_data.append({
            "X": sfm.feature_row((fv_h, fv_a)),
            "actual": blind[r[6]]["actual"],
            "odds": blind[r[6]]["odds"],
            "date": blind[r[6]]["date"],
        })

    stats[h].add(hg, ag, True)
    stats[a].add(ag, hg, False)
    tot_g += hg + ag
    tot_n += 1

print(f"训练样本：{len(train_data)}，盲测样本：{len(blind_data)}")

# 族内比分频率
fam_dist = defaultdict(Counter)
for d in train_data:
    fam_dist[d["family"]][d["score"]] += 1


def build_packs_from_probs(P, method_name):
    """从族概率构建预测包"""
    packs = []
    for i, b in enumerate(blind_data):
        model_probs = {}
        for ci, cname in enumerate(sfm.CLASSES):
            tot = sum(fam_dist.get(cname, {}).values()) or 1
            for s, c in fam_dist.get(cname, {}).items():
                model_probs[s] = model_probs.get(s, 0.0) + P[i][ci] * (c / tot)

        mdl_sorted = sorted(model_probs.items(), key=lambda kv: -kv[1])
        gap = mdl_sorted[0][1] - mdl_sorted[1][1] if len(mdl_sorted) >= 2 else 0

        packs.append({
            "date": b["date"],
            "actual": b["actual"],
            "odds": b["odds"],
            "model_sorted": mdl_sorted,
            "gap": gap,
        })
    return packs


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
        hit = False

        for (s1, o1), (s2, o2) in all_bets:
            if s1 == p1["actual"] and s2 == p2["actual"]:
                payout += UNIT * o1 * o2
                hit = True

        total_cost += cost
        total_payout += payout
        n_tickets += 1
        if hit:
            n_hits += 1

    profit = (total_payout - total_cost) / total_cost if total_cost > 0 else -1
    return {
        "n_tickets": n_tickets,
        "n_hits": n_hits,
        "total_cost": total_cost,
        "total_payout": total_payout,
        "profit": profit,
        "avg_cost": total_cost / n_tickets if n_tickets > 0 else 0,
    }


def analyze_predictions(packs, name):
    """分析预测分布"""
    pred_top1 = Counter()
    for p in packs:
        pred_top1[p["model_sorted"][0][0]] += 1

    unique = len(pred_top1)
    top3_pct = sum(c for s, c in pred_top1.most_common(3)) / len(packs) * 100
    return {
        "unique": unique,
        "top3_pct": top3_pct,
        "top1_score": pred_top1.most_common(1)[0] if pred_top1 else None,
    }


# ========================================
# 基准：原始模型
# ========================================

print("\n" + "=" * 100)
print("基准：原始模型")
print("=" * 100)

X_tr = np.array([d["X"] for d in train_data])
y_tr = np.array([sfm.CLASSES.index(d["family"]) for d in train_data])
X_bl = np.array([b["X"] for b in blind_data])

model_base = sfm.train_softmax(X_tr, y_tr, len(sfm.CLASSES))
P_base = sfm.predict_proba(model_base, X_bl)

packs_base = build_packs_from_probs(P_base, "基准")
result_base = run_backtest(packs_base)
pred_base = analyze_predictions(packs_base, "基准")

print(f"  盈利率：{result_base['profit']*100:+.2f}%")
print(f"  命中：{result_base['n_hits']}/{result_base['n_tickets']}")
print(f"  预测种类：{pred_base['unique']}，Top3占比：{pred_base['top3_pct']:.1f}%")


# ========================================
# 方向 A：修复族分布（balanced 训练）
# ========================================

print("\n" + "=" * 100)
print("方向 A：修复族分布（balanced 训练 + 调整族权重）")
print("=" * 100)

# A1: balanced 训练
print("\nA1: balanced=True（类别加权）")
model_A1 = sfm.train_softmax(X_tr, y_tr, len(sfm.CLASSES), balanced=True)
P_A1 = sfm.predict_proba(model_A1, X_bl)
packs_A1 = build_packs_from_probs(P_A1, "A1")
result_A1 = run_backtest(packs_A1)
pred_A1 = analyze_predictions(packs_A1, "A1")
print(f"  盈利率：{result_A1['profit']*100:+.2f}%")
print(f"  命中：{result_A1['n_hits']}/{result_A1['n_tickets']}")
print(f"  预测种类：{pred_A1['unique']}，Top3占比：{pred_A1['top3_pct']:.1f}%")

# A2: 手动调整族概率（压低 draw/home_clean，提升 multi）
print("\nA2: 手动调整族概率（压低高频族）")
P_A2 = P_base.copy()
# draw=2, home_clean=0, home_multi=1, away_clean=3, away_multi=4, other=5
adjust = np.array([0.8, 1.5, 0.7, 0.9, 1.5, 1.2])  # 压低 draw/home_clean，提升 multi
P_A2 = P_A2 * adjust
P_A2 = P_A2 / P_A2.sum(axis=1, keepdims=True)
packs_A2 = build_packs_from_probs(P_A2, "A2")
result_A2 = run_backtest(packs_A2)
pred_A2 = analyze_predictions(packs_A2, "A2")
print(f"  盈利率：{result_A2['profit']*100:+.2f}%")
print(f"  命中：{result_A2['n_hits']}/{result_A2['n_tickets']}")
print(f"  预测种类：{pred_A2['unique']}，Top3占比：{pred_A2['top3_pct']:.1f}%")


# ========================================
# 方向 B：直接预测比分（跳过族）
# ========================================

print("\n" + "=" * 100)
print("方向 B：直接预测比分（跳过族中间层）")
print("=" * 100)

# 收集所有出现过的比分
all_scores = set()
for d in train_data:
    all_scores.add(d["score"])
all_scores = sorted(all_scores)
score_to_idx = {s: i for i, s in enumerate(all_scores)}
n_scores = len(all_scores)

print(f"  比分种类：{n_scores}")

# B1: 直接多分类
print("\nB1: 直接 softmax 多分类（所有比分）")
y_tr_score = np.array([score_to_idx[d["score"]] for d in train_data])

model_B1 = sfm.train_softmax(X_tr, y_tr_score, n_scores, l2=0.5)
P_B1_raw = sfm.predict_proba(model_B1, X_bl)

# 构建预测包
packs_B1 = []
for i, b in enumerate(blind_data):
    model_probs = {all_scores[j]: P_B1_raw[i][j] for j in range(n_scores)}
    mdl_sorted = sorted(model_probs.items(), key=lambda kv: -kv[1])
    gap = mdl_sorted[0][1] - mdl_sorted[1][1] if len(mdl_sorted) >= 2 else 0
    packs_B1.append({
        "date": b["date"],
        "actual": b["actual"],
        "odds": b["odds"],
        "model_sorted": mdl_sorted,
        "gap": gap,
    })

result_B1 = run_backtest(packs_B1)
pred_B1 = analyze_predictions(packs_B1, "B1")
print(f"  盈利率：{result_B1['profit']*100:+.2f}%")
print(f"  命中：{result_B1['n_hits']}/{result_B1['n_tickets']}")
print(f"  预测种类：{pred_B1['unique']}，Top3占比：{pred_B1['top3_pct']:.1f}%")

# B2: 只预测高频比分（Top20）
print("\nB2: 只预测高频比分（Top20 + other）")
score_freq = Counter(d["score"] for d in train_data)
top_scores = [s for s, c in score_freq.most_common(20)]
top_scores_set = set(top_scores)

y_tr_top = []
for d in train_data:
    if d["score"] in top_scores_set:
        y_tr_top.append(top_scores.index(d["score"]))
    else:
        y_tr_top.append(len(top_scores))  # other
y_tr_top = np.array(y_tr_top)

model_B2 = sfm.train_softmax(X_tr, y_tr_top, len(top_scores) + 1, l2=0.5)
P_B2_raw = sfm.predict_proba(model_B2, X_bl)

# 构建预测包（只用 top20，忽略 other）
packs_B2 = []
for i, b in enumerate(blind_data):
    model_probs = {top_scores[j]: P_B2_raw[i][j] for j in range(len(top_scores))}
    mdl_sorted = sorted(model_probs.items(), key=lambda kv: -kv[1])
    gap = mdl_sorted[0][1] - mdl_sorted[1][1] if len(mdl_sorted) >= 2 else 0
    packs_B2.append({
        "date": b["date"],
        "actual": b["actual"],
        "odds": b["odds"],
        "model_sorted": mdl_sorted,
        "gap": gap,
    })

result_B2 = run_backtest(packs_B2)
pred_B2 = analyze_predictions(packs_B2, "B2")
print(f"  盈利率：{result_B2['profit']*100:+.2f}%")
print(f"  命中：{result_B2['n_hits']}/{result_B2['n_tickets']}")
print(f"  预测种类：{pred_B2['unique']}，Top3占比：{pred_B2['top3_pct']:.1f}%")


# ========================================
# 方向 C：加入比分特异性特征
# ========================================

print("\n" + "=" * 100)
print("方向 C：加入比分特异性特征")
print("=" * 100)

# 重新收集数据，加入新特征
stats2 = defaultdict(lambda: {
    "base": sfm.TeamStats(),
    "zero_zero": 0,  # 0:0 次数
    "high_score": 0,  # 总进球>=4 次数
    "clean_sheet": 0,  # 零封次数
    "n": 0,
})

train_data_C = []
blind_data_C = []

for r in merged:
    kind, date, h, a, hg, ag = r[0], r[1], r[2], r[3], r[4], r[5]

    sh, sa = stats2[h], stats2[a]
    nh = max(sh["n"], 1)
    na = max(sa["n"], 1)

    # 新特征
    h_zero_zero_rate = sh["zero_zero"] / nh
    a_zero_zero_rate = sa["zero_zero"] / na
    h_high_score_rate = sh["high_score"] / nh
    a_high_score_rate = sa["high_score"] / na
    h_clean_rate = sh["clean_sheet"] / nh
    a_clean_rate = sa["clean_sheet"] / na

    lg_gf = (tot_g / tot_n) if tot_n >= 50 else 2.6
    fv_h = sh["base"].vector(0, lg_gf)
    fv_a = sa["base"].vector(1, lg_gf)

    base_features = sfm.feature_row((fv_h, fv_a))
    new_features = [
        h_zero_zero_rate, a_zero_zero_rate,
        h_high_score_rate, a_high_score_rate,
        h_clean_rate, a_clean_rate,
    ]
    full_features = base_features + new_features

    if kind == "L" and date < cut and fv_h[12] >= sfm.MIN_HIST and fv_a[12] >= sfm.MIN_HIST:
        train_data_C.append({
            "X": full_features,
            "family": sfm.family_of(hg, ag),
            "score": (hg, ag),
        })
    elif kind == "B":
        idx = r[6]
        blind_data_C.append({
            "X": full_features,
            "actual": blind[idx]["actual"],
            "odds": blind[idx]["odds"],
            "date": blind[idx]["date"],
        })

    # 更新统计
    sh["base"].add(hg, ag, True)
    sa["base"].add(ag, hg, False)
    sh["n"] += 1
    sa["n"] += 1
    if hg == 0 and ag == 0:
        sh["zero_zero"] += 1
        sa["zero_zero"] += 1
    if hg + ag >= 4:
        sh["high_score"] += 1
        sa["high_score"] += 1
    if ag == 0:
        sh["clean_sheet"] += 1
    if hg == 0:
        sa["clean_sheet"] += 1

print(f"  训练样本：{len(train_data_C)}，特征维度：{len(train_data_C[0]['X'])}")

X_tr_C = np.array([d["X"] for d in train_data_C])
y_tr_C = np.array([sfm.CLASSES.index(d["family"]) for d in train_data_C])
X_bl_C = np.array([b["X"] for b in blind_data_C])

# 族内比分频率（用新训练数据）
fam_dist_C = defaultdict(Counter)
for d in train_data_C:
    fam_dist_C[d["family"]][d["score"]] += 1

model_C = sfm.train_softmax(X_tr_C, y_tr_C, len(sfm.CLASSES))
P_C = sfm.predict_proba(model_C, X_bl_C)

# 构建预测包
packs_C = []
for i, b in enumerate(blind_data_C):
    model_probs = {}
    for ci, cname in enumerate(sfm.CLASSES):
        tot = sum(fam_dist_C.get(cname, {}).values()) or 1
        for s, c in fam_dist_C.get(cname, {}).items():
            model_probs[s] = model_probs.get(s, 0.0) + P_C[i][ci] * (c / tot)

    mdl_sorted = sorted(model_probs.items(), key=lambda kv: -kv[1])
    gap = mdl_sorted[0][1] - mdl_sorted[1][1] if len(mdl_sorted) >= 2 else 0

    packs_C.append({
        "date": b["date"],
        "actual": b["actual"],
        "odds": b["odds"],
        "model_sorted": mdl_sorted,
        "gap": gap,
    })

result_C = run_backtest(packs_C)
pred_C = analyze_predictions(packs_C, "C")
print(f"  盈利率：{result_C['profit']*100:+.2f}%")
print(f"  命中：{result_C['n_hits']}/{result_C['n_tickets']}")
print(f"  预测种类：{pred_C['unique']}，Top3占比：{pred_C['top3_pct']:.1f}%")


# ========================================
# 汇总对比
# ========================================

print("\n" + "=" * 100)
print("汇总对比（含成本）")
print("=" * 100)

all_results = [
    ("基准（原始）", result_base, pred_base),
    ("A1: balanced训练", result_A1, pred_A1),
    ("A2: 手动调整族权重", result_A2, pred_A2),
    ("B1: 直接预测所有比分", result_B1, pred_B1),
    ("B2: 直接预测Top20比分", result_B2, pred_B2),
    ("C: 加入比分特异性特征", result_C, pred_C),
]

print(f"\n{'方法':<25} {'盈利率':>10} {'命中':>10} {'成本':>10} {'派彩':>10} {'净利润':>10}")
print("-" * 85)
for name, result, pred in sorted(all_results, key=lambda x: -x[1]["profit"]):
    mark = "✅" if result["profit"] > 0 else ""
    net = result["total_payout"] - result["total_cost"]
    print(f"{name:<25} {result['profit']*100:>+9.2f}% {result['n_hits']:>10} "
          f"{result['total_cost']:>10.0f} {result['total_payout']:>10.0f} {net:>+10.0f} {mark}")
    print(f"{name:<25} {result['profit']*100:>+9.2f}% {result['n_hits']:>10} "
          f"{pred['unique']:>10} {pred['top3_pct']:>9.1f}% {mark}")
