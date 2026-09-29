# -*- coding: utf-8 -*-
"""选场策略大样本验证 —— 多切分点测试"""
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
print("选场策略大样本验证")
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


def run_backtest(packs, select_fn, gap_threshold=0.05):
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

        selected = select_fn(day_packs)
        if len(selected) < 2:
            continue

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


# 选场函数
def select_top_gap(day_packs):
    """选 gap 最大的2场（基准）"""
    return sorted(day_packs, key=lambda p: -p["gap"])[:2]

def select_low_odds(day_packs):
    """选 top1 赔率最低的2场"""
    def top1_odds(p):
        s = p["model_sorted"][0][0]
        return p["odds"].get(s, 999)
    return sorted(day_packs, key=top1_odds)[:2]


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

print(f"\n{'切分点':<12} {'盲测场':>8} | {'基准(gap最大,0.05)':>20} | {'赔率最低+gap0.03':>20} | {'赔率最低+gap0.05':>20}")
print("-" * 95)

results_base = []
results_low_odds_03 = []
results_low_odds_05 = []

for cut in cuts:
    packs = build_packs(cut)
    if packs is None:
        continue

    n_blind = len(packs)

    # 基准：gap最大 + gap阈值0.05
    r_base = run_backtest(packs, select_top_gap, gap_threshold=0.05)

    # 赔率最低 + gap阈值0.03
    r_low_03 = run_backtest(packs, select_low_odds, gap_threshold=0.03)

    # 赔率最低 + gap阈值0.05
    r_low_05 = run_backtest(packs, select_low_odds, gap_threshold=0.05)

    mark_base = "✅" if r_base["profit"] > 0 else ""
    mark_low_03 = "✅" if r_low_03["profit"] > 0 else ""
    mark_low_05 = "✅" if r_low_05["profit"] > 0 else ""

    print(f"{cut:<12} {n_blind:>8} | {r_base['profit']*100:>+8.1f}% {r_base['n_hits']:>3}命中 {mark_base:<2} | "
          f"{r_low_03['profit']*100:>+8.1f}% {r_low_03['n_hits']:>3}命中 {mark_low_03:<2} | "
          f"{r_low_05['profit']*100:>+8.1f}% {r_low_05['n_hits']:>3}命中 {mark_low_05:<2}")

    results_base.append(r_base)
    results_low_odds_03.append(r_low_03)
    results_low_odds_05.append(r_low_05)

print("-" * 95)

# 汇总
def summarize(results, name):
    total_cost = sum(r["total_cost"] for r in results)
    total_payout = sum(r["total_payout"] for r in results)
    total_hits = sum(r["n_hits"] for r in results)
    total_tickets = sum(r["n_tickets"] for r in results)
    profit = (total_payout - total_cost) / total_cost if total_cost > 0 else -1
    positive = sum(1 for r in results if r["profit"] > 0)
    return {
        "name": name,
        "positive": positive,
        "total": len(results),
        "profit": profit,
        "total_cost": total_cost,
        "total_payout": total_payout,
        "total_hits": total_hits,
        "total_tickets": total_tickets,
    }

s_base = summarize(results_base, "基准(gap最大+0.05)")
s_low_03 = summarize(results_low_odds_03, "赔率最低+gap0.03")
s_low_05 = summarize(results_low_odds_05, "赔率最低+gap0.05")

print(f"\n【汇总】")
print(f"{'策略':<25} {'正收益':>10} {'总盈利率':>12} {'总成本':>10} {'总派彩':>10} {'总命中':>10}")
print("-" * 85)
for s in [s_base, s_low_03, s_low_05]:
    mark = "✅" if s["profit"] > 0 else ""
    print(f"{s['name']:<25} {s['positive']}/{s['total']:>8} {s['profit']*100:>+11.1f}% "
          f"{s['total_cost']:>10.0f} {s['total_payout']:>10.0f} {s['total_hits']:>10} {mark}")
