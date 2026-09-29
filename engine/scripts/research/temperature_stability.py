# -*- coding: utf-8 -*-
"""温度参数稳定性验证 —— 多切分点测试 T=0.8"""
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

def apply_temperature(logits, T):
    """温度缩放"""
    scaled = logits / T
    scaled -= scaled.max(axis=1, keepdims=True)
    exp_scaled = np.exp(scaled)
    return exp_scaled / exp_scaled.sum(axis=1, keepdims=True)


def get_logits(model, X):
    """获取 logits"""
    X = (np.asarray(X, dtype=float) - model["mu"]) / model["sd"]
    Xb = np.hstack([X, np.ones((len(X), 1))])
    return Xb @ model["W"]


def run_full_pipeline(cut_date, T=1.0, gap_threshold=0.05):
    """完整流水线：数据→训练→预测→回测"""
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
            X_tr.append(sfm.feature_row((fv_h, fv_a)))
            y_tr.append(sfm.CLASSES.index(sfm.family_of(hg, ag)))
        elif kind == "B":
            meta.append(blind[r[6]])
            X_bl.append(sfm.feature_row((fv_h, fv_a)))

        stats[h].add(hg, ag, True)
        stats[a].add(ag, hg, False)
        tot_g += hg + ag
        tot_n += 1

    if len(X_tr) == 0 or len(X_bl) == 0:
        return None

    X_tr = np.array(X_tr)
    y_tr = np.array(y_tr)
    X_bl = np.array(X_bl)

    # 训练
    model = sfm.train_softmax(X_tr, y_tr, len(sfm.CLASSES))

    # 预测（带温度）
    logits = get_logits(model, X_bl)
    P = apply_temperature(logits, T)

    # 族内比分频率
    fam_dist = defaultdict(Counter)
    for r in merged:
        if r[0] == "L" and r[1] < cut_date:
            fam_dist[sfm.family_of(r[4], r[5])][(r[4], r[5])] += 1

    # 构建预测包
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

    # 回测
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
        "n_train": len(X_tr),
        "n_blind": len(X_bl),
        "n_tickets": n_tickets,
        "n_hits": n_hits,
        "total_cost": total_cost,
        "total_payout": total_payout,
        "profit": profit,
    }


# ========================================
# 主测试
# ========================================

print("=" * 100)
print("温度参数稳定性验证 —— 多切分点测试")
print("=" * 100)

cuts = [
    "2025-12-01",
    "2026-01-01",
    "2026-02-01",
    "2026-03-01",
    "2026-04-01",
    "2026-05-01",
    "2026-06-01",
    "2026-07-01",
]

temperatures = [1.0, 0.9, 0.8, 0.7]

print(f"\n{'切分点':<12}", end="")
for T in temperatures:
    print(f"{'T='+str(T):>15}", end="")
print()
print("-" * 75)

results_by_T = {T: [] for T in temperatures}

for cut in cuts:
    print(f"{cut:<12}", end="")
    for T in temperatures:
        r = run_full_pipeline(cut, T=T, gap_threshold=0.05)
        if r:
            mark = "✅" if r["profit"] > 0 else ""
            print(f"{r['profit']*100:>+13.1f}%{mark}", end="")
            results_by_T[T].append(r["profit"])
        else:
            print(f"{'N/A':>15}", end="")
    print()

print("-" * 75)

# 汇总
print(f"\n{'汇总':<12}", end="")
for T in temperatures:
    profits = results_by_T[T]
    if profits:
        avg = sum(profits) / len(profits)
        positive = sum(1 for p in profits if p > 0)
        print(f"{avg*100:>+8.1f}% ({positive}/{len(profits)})", end="")
print()

print("\n" + "=" * 100)
print("详细对比：T=1.0 (基准) vs T=0.8 (最优)")
print("=" * 100)

print(f"\n{'切分点':<12} {'盲测场':>8} {'T=1.0票数':>10} {'T=1.0盈利':>12} {'T=0.8票数':>10} {'T=0.8盈利':>12} {'差异':>10}")
print("-" * 85)

total_diff = 0
for cut in cuts:
    r10 = run_full_pipeline(cut, T=1.0, gap_threshold=0.05)
    r08 = run_full_pipeline(cut, T=0.8, gap_threshold=0.05)
    if r10 and r08:
        diff = r08["profit"] - r10["profit"]
        total_diff += diff
        mark = "⬆️" if diff > 0 else ("⬇️" if diff < 0 else "")
        print(f"{cut:<12} {r10['n_blind']:>8} {r10['n_tickets']:>10} {r10['profit']*100:>+11.1f}% "
              f"{r08['n_tickets']:>10} {r08['profit']*100:>+11.1f}% {diff*100:>+9.1f}% {mark}")

print("-" * 85)
print(f"{'平均差异':<12} {'':<8} {'':<10} {'':<12} {'':<10} {'':<12} {total_diff/len(cuts)*100:>+9.1f}%")
