# -*- coding: utf-8 -*-
"""综合优化实验 —— gap阈值 + 温度校准 + 赔率特征 一起测"""
import json
import sys
import numpy as np
from collections import Counter, defaultdict
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path("engine/scripts/research")))

import score_family_model as sfm
from ticket_dynamic_k_backtest import load_hist_full

print("=" * 90)
print("综合优化实验 —— gap阈值 + 温度校准 + 赔率特征")
print("=" * 90)

UNIT = 2.0

# ========================================
# 1. 数据准备
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

stats = defaultdict(sfm.TeamStats)
X_tr, y_tr, X_bl, meta = [], [], [], []
tot_g = tot_n = 0

for r in merged:
    kind, date, h, a, hg, ag = r[0], r[1], r[2], r[3], r[4], r[5]
    lg_gf = (tot_g / tot_n) if tot_n >= 50 else 2.6
    fv_h = stats[h].vector(0, lg_gf)
    fv_a = stats[a].vector(1, lg_gf)

    if kind == "L" and date < cut and fv_h[12] >= sfm.MIN_HIST and fv_a[12] >= sfm.MIN_HIST:
        X_tr.append(sfm.feature_row((fv_h, fv_a)))
        y_tr.append(sfm.CLASSES.index(sfm.family_of(hg, ag)))
    elif kind == "B":
        meta.append(blind[r[6]])
        X_bl.append(sfm.feature_row((fv_h, fv_a)))

    stats[h].add(hg, ag, True)
    stats[a].add(ag, hg, False)
    tot_g += hg + ag
    tot_n += 1

X_tr = np.array(X_tr)
y_tr = np.array(y_tr)
X_bl = np.array(X_bl)

print(f"训练样本：{len(X_tr)}，盲测样本：{len(X_bl)}")

# 训练基础模型
model = sfm.train_softmax(X_tr, y_tr, len(sfm.CLASSES))
P_base = sfm.predict_proba(model, X_bl)

# 族内比分频率
fam_dist = defaultdict(Counter)
for r in merged:
    if r[0] == "L" and r[1] < cut:
        fam_dist[sfm.family_of(r[4], r[5])][(r[4], r[5])] += 1


# ========================================
# 2. 温度校准
# ========================================

def apply_temperature(logits, T):
    """温度缩放：T>1 降低自信度，T<1 增加自信度"""
    scaled = logits / T
    scaled -= scaled.max(axis=1, keepdims=True)
    exp_scaled = np.exp(scaled)
    return exp_scaled / exp_scaled.sum(axis=1, keepdims=True)


def get_logits(model, X):
    """获取 logits（softmax 前的值）"""
    X = (np.asarray(X, dtype=float) - model["mu"]) / model["sd"]
    Xb = np.hstack([X, np.ones((len(X), 1))])
    return Xb @ model["W"]


logits_bl = get_logits(model, X_bl)


# ========================================
# 3. 赔率特征融合
# ========================================

def odds_to_prob(odds_dict):
    """赔率转概率（去水）"""
    if not odds_dict:
        return {}
    total = sum(1/o for o in odds_dict.values() if o and o > 1)
    if total == 0:
        return {}
    return {s: (1/o)/total for s, o in odds_dict.items() if o and o > 1}


def fuse_probs(model_probs, odds_probs, alpha=0.7):
    """融合模型概率和赔率概率

    alpha: 模型权重（0~1），1=纯模型，0=纯赔率
    """
    all_scores = set(model_probs.keys()) | set(odds_probs.keys())
    fused = {}
    for s in all_scores:
        mp = model_probs.get(s, 0.001)
        op = odds_probs.get(s, 0.001)
        fused[s] = alpha * mp + (1 - alpha) * op
    # 归一化
    total = sum(fused.values())
    return {s: p/total for s, p in fused.items()}


# ========================================
# 4. 构建预测包（带温度和赔率融合）
# ========================================

def build_packs_with_params(T=1.0, alpha=1.0):
    """构建预测包

    T: 温度参数
    alpha: 模型权重（1=纯模型）
    """
    if T != 1.0:
        P = apply_temperature(logits_bl, T)
    else:
        P = P_base

    packs = []
    for i, m in enumerate(meta):
        # 模型概率
        model_probs = {}
        for ci, cname in enumerate(sfm.CLASSES):
            tot = sum(fam_dist.get(cname, {}).values()) or 1
            for s, c in fam_dist.get(cname, {}).items():
                model_probs[s] = model_probs.get(s, 0.0) + P[i][ci] * (c / tot)

        # 赔率概率
        if alpha < 1.0:
            odds_probs = odds_to_prob(m["odds"])
            final_probs = fuse_probs(model_probs, odds_probs, alpha)
        else:
            final_probs = model_probs

        mdl_sorted = sorted(final_probs.items(), key=lambda kv: -kv[1])
        gap = mdl_sorted[0][1] - mdl_sorted[1][1] if len(mdl_sorted) >= 2 else 0

        packs.append({
            "date": m["date"],
            "actual": m["actual"],
            "odds": m["odds"],
            "model_sorted": mdl_sorted,
            "gap": gap,
        })

    return packs


# ========================================
# 5. 回测函数
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

        day_sorted = sorted(day_packs, key=lambda p: -p["gap"])
        p1, p2 = day_sorted[0], day_sorted[1]

        # 动态选腿
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

    profit = (total_payout - total_cost) / total_cost if total_cost > 0 else 0
    return {
        "n_tickets": n_tickets,
        "n_hits": n_hits,
        "cost": total_cost,
        "payout": total_payout,
        "profit": profit,
    }


# ========================================
# 6. 网格搜索
# ========================================

print("\n【网格搜索最优参数组合】")
print("=" * 100)

# 参数网格
temperatures = [0.8, 1.0, 1.2, 1.5, 2.0]
alphas = [1.0, 0.9, 0.8, 0.7, 0.5]
gap_thresholds = [0.03, 0.04, 0.05, 0.06, 0.08]

results = []
total_combos = len(temperatures) * len(alphas) * len(gap_thresholds)
print(f"测试 {total_combos} 个参数组合...\n")

best_profit = -999
best_params = None

for T in temperatures:
    packs_T = build_packs_with_params(T=T, alpha=1.0)

    for alpha in alphas:
        if alpha < 1.0:
            packs = build_packs_with_params(T=T, alpha=alpha)
        else:
            packs = packs_T

        for gap_th in gap_thresholds:
            result = run_backtest(packs, gap_threshold=gap_th)

            results.append({
                "T": T,
                "alpha": alpha,
                "gap_th": gap_th,
                **result,
            })

            if result["profit"] > best_profit:
                best_profit = result["profit"]
                best_params = (T, alpha, gap_th)

# 输出 top 20
print(f"{'T':>5} {'alpha':>7} {'gap_th':>8} {'票数':>6} {'命中':>6} {'成本':>8} {'派彩':>8} {'盈利率':>10}")
print("-" * 80)

for r in sorted(results, key=lambda x: -x["profit"])[:20]:
    mark = "✅" if r["profit"] > 0 else ""
    best_mark = " ⭐" if (r["T"], r["alpha"], r["gap_th"]) == best_params else ""
    print(f"{r['T']:>5.1f} {r['alpha']:>7.1f} {r['gap_th']:>8.2f} {r['n_tickets']:>6} {r['n_hits']:>6} "
          f"{r['cost']:>8.0f} {r['payout']:>8.0f} {r['profit']*100:>+9.2f}% {mark}{best_mark}")

print("-" * 80)

# 最优参数
print(f"\n【最优参数组合】")
print(f"  温度 T = {best_params[0]}")
print(f"  模型权重 alpha = {best_params[1]}")
print(f"  Gap 阈值 = {best_params[2]}")
print(f"  盈利率 = {best_profit*100:+.2f}%")

# 对比基准
baseline = [r for r in results if r["T"] == 1.0 and r["alpha"] == 1.0 and r["gap_th"] == 0.05][0]
print(f"\n【对比基准】(T=1.0, alpha=1.0, gap=0.05)")
print(f"  基准盈利率: {baseline['profit']*100:+.2f}%")
print(f"  最优盈利率: {best_profit*100:+.2f}%")
print(f"  提升: {(best_profit - baseline['profit'])*100:+.2f}%")
