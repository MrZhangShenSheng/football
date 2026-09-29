# -*- coding: utf-8 -*-
"""按日过滤策略 —— 多切分点稳定性验证"""
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
print("按日过滤策略 —— 多切分点稳定性验证")
print("=" * 100)

# 加载数据
zh = {}
for tid, srcs in sfm.load_aliases().items():
    if srcs.get("zh"):
        zh[srcs["zh"]] = tid

tl = sfm.league_timeline()
hist = load_hist_full()


def build_packs(cut_date):
    """构建预测包"""
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

    if len(X_tr) < 100 or len(X_bl) < 50:
        return None

    model = sfm.train_softmax(np.array(X_tr), np.array(y_tr), len(sfm.CLASSES))
    P = sfm.predict_proba(model, np.array(X_bl))

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

    return packs


def run_backtest(packs, min_matches=2, gap_threshold=0.05):
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
        if len(day_packs) < min_matches:
            continue

        selected = sorted(day_packs, key=lambda p: -p["gap"])[:2]
        p1, p2 = selected[0], selected[1]

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


# 多切分点测试
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

# 策略配置
strategies = [
    ("基准(>=2场)", 2),
    (">=5场", 5),
    (">=8场", 8),
    (">=10场", 10),
    (">=12场", 12),
]

print(f"\n{'切分点':<12} ", end="")
for name, _ in strategies:
    print(f"{name:>15}", end="")
print()
print("-" * 95)

# 收集所有结果
all_results = {name: [] for name, _ in strategies}

for cut in cuts:
    packs = build_packs(cut)
    if packs is None:
        continue

    print(f"{cut:<12} ", end="")

    for name, min_m in strategies:
        r = run_backtest(packs, min_matches=min_m)
        if r["n_tickets"] > 0:
            mark = "✅" if r["profit"] > 0 else ""
            print(f"{r['profit']*100:>+8.1f}%({r['n_tickets']:>3}){mark}", end="")
            all_results[name].append(r)
        else:
            print(f"{'N/A':>15}", end="")
    print()

print("-" * 95)

# 汇总
print(f"\n【汇总】")
print(f"{'策略':<15} {'正收益':>10} {'总票数':>10} {'总成本':>10} {'总派彩':>10} {'总盈利率':>12}")
print("-" * 75)

for name, _ in strategies:
    results = all_results[name]
    if not results:
        continue

    positive = sum(1 for r in results if r["profit"] > 0)
    total_tickets = sum(r["n_tickets"] for r in results)
    total_cost = sum(r["total_cost"] for r in results)
    total_payout = sum(r["total_payout"] for r in results)
    total_profit = (total_payout - total_cost) / total_cost if total_cost > 0 else 0

    mark = "✅" if positive == len(results) else ""
    print(f"{name:<15} {positive:>5}/{len(results):<4} {total_tickets:>10} {total_cost:>10.0f} {total_payout:>10.0f} {total_profit*100:>+11.2f}% {mark}")

print("-" * 75)


# ========================================
# 组合测试：>=N场 + gap阈值
# ========================================

print(f"\n" + "=" * 100)
print("组合测试：场次过滤 × gap阈值")
print("=" * 100)

min_matches_list = [2, 5, 8, 10]
gap_thresholds = [0.03, 0.04, 0.05, 0.06]

best_combo = None
best_positive_rate = 0
best_profit = -999

print(f"\n{'场次':>6} {'gap阈值':>10} {'正收益':>10} {'总盈利率':>12}")
print("-" * 45)

for min_m in min_matches_list:
    for gap_th in gap_thresholds:
        positive = 0
        total_cost = 0
        total_payout = 0
        n_cuts = 0

        for cut in cuts:
            packs = build_packs(cut)
            if packs is None:
                continue

            r = run_backtest(packs, min_matches=min_m, gap_threshold=gap_th)
            if r["n_tickets"] > 0:
                n_cuts += 1
                total_cost += r["total_cost"]
                total_payout += r["total_payout"]
                if r["profit"] > 0:
                    positive += 1

        if n_cuts > 0:
            total_profit = (total_payout - total_cost) / total_cost if total_cost > 0 else 0
            mark = "✅" if positive == n_cuts else ""
            print(f"{'>=' + str(min_m):>6} {gap_th:>10.2f} {positive:>5}/{n_cuts:<4} {total_profit*100:>+11.2f}% {mark}")

            # 更新最优
            positive_rate = positive / n_cuts
            if positive_rate > best_positive_rate or (positive_rate == best_positive_rate and total_profit > best_profit):
                best_positive_rate = positive_rate
                best_profit = total_profit
                best_combo = (min_m, gap_th, positive, n_cuts, total_profit)

print("-" * 45)

if best_combo:
    print(f"\n最优组合：>={best_combo[0]}场 + gap阈值={best_combo[1]}")
    print(f"  正收益：{best_combo[2]}/{best_combo[3]}")
    print(f"  总盈利率：{best_combo[4]*100:+.2f}%")
