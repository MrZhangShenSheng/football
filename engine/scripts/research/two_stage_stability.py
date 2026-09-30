# -*- coding: utf-8 -*-
"""两阶段模型 —— 多切分点稳定性验证"""
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
print("两阶段模型 —— 多切分点稳定性验证")
print("=" * 100)

# 加载数据
zh = {}
for tid, srcs in sfm.load_aliases().items():
    if srcs.get("zh"):
        zh[srcs["zh"]] = tid

tl = sfm.league_timeline()
hist = load_hist_full()


def run_test(cut_date, model_type="base"):
    """
    model_type: "base"=族模型, "two_stage"=两阶段(总进球→比分), "dual"=双模型(主+客)
    """
    # 构建数据
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
                "home_goals": min(hg, 4),
                "away_goals": min(ag, 4),
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

    if len(train_data) < 100 or len(blind_data) < 50:
        return None

    X_tr = np.array([d["X"] for d in train_data])
    X_bl = np.array([b["X"] for b in blind_data])

    if model_type == "base":
        # 族模型
        y_tr = np.array([sfm.CLASSES.index(d["family"]) for d in train_data])
        model = sfm.train_softmax(X_tr, y_tr, len(sfm.CLASSES))
        P = sfm.predict_proba(model, X_bl)

        fam_dist = defaultdict(Counter)
        for d in train_data:
            fam_dist[d["family"]][d["score"]] += 1

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
                "date": b["date"], "actual": b["actual"], "odds": b["odds"],
                "model_sorted": mdl_sorted, "gap": gap,
            })

    elif model_type == "two_stage":
        # 两阶段：总进球→比分
        y_total = np.array([d["total_goals"] for d in train_data])
        n_classes = len(set(y_total))
        model = sfm.train_softmax(X_tr, y_total, n_classes)
        P_total = sfm.predict_proba(model, X_bl)

        # 每个总进球数下的比分分布
        score_by_total = defaultdict(Counter)
        for d in train_data:
            score_by_total[d["total_goals"]][d["score"]] += 1

        packs = []
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
            packs.append({
                "date": b["date"], "actual": b["actual"], "odds": b["odds"],
                "model_sorted": mdl_sorted, "gap": gap,
            })

    elif model_type == "dual":
        # 双模型：主队进球 + 客队进球
        y_home = np.array([d["home_goals"] for d in train_data])
        y_away = np.array([d["away_goals"] for d in train_data])
        n_home = len(set(y_home))
        n_away = len(set(y_away))

        model_home = sfm.train_softmax(X_tr, y_home, n_home)
        model_away = sfm.train_softmax(X_tr, y_away, n_away)
        P_home = sfm.predict_proba(model_home, X_bl)
        P_away = sfm.predict_proba(model_away, X_bl)

        packs = []
        for i, b in enumerate(blind_data):
            model_probs = {}
            for hg in range(n_home):
                for ag in range(n_away):
                    model_probs[(hg, ag)] = P_home[i][hg] * P_away[i][ag]

            mdl_sorted = sorted(model_probs.items(), key=lambda kv: -kv[1])
            gap = mdl_sorted[0][1] - mdl_sorted[1][1] if len(mdl_sorted) >= 2 else 0
            packs.append({
                "date": b["date"], "actual": b["actual"], "odds": b["odds"],
                "model_sorted": mdl_sorted, "gap": gap,
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

        selected = sorted(day_packs, key=lambda p: -p["gap"])[:2]
        p1, p2 = selected[0], selected[1]

        k1 = 1 if p1["gap"] > 0.05 else 2
        k2 = 1 if p2["gap"] > 0.05 else 2

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
        "n_train": len(train_data),
        "n_blind": len(blind_data),
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

models = [
    ("基准(族模型)", "base"),
    ("两阶段(总进球)", "two_stage"),
    ("双模型(主+客)", "dual"),
]

print(f"\n{'切分点':<12} {'盲测':>6} |", end="")
for name, _ in models:
    print(f" {name:>18} |", end="")
print()
print("-" * 95)

# 收集结果
all_results = {m[1]: [] for m in models}

for cut in cuts:
    print(f"{cut:<12} ", end="")

    # 先跑基准获取盲测数
    r_base = run_test(cut, "base")
    if r_base is None:
        print("N/A")
        continue

    print(f"{r_base['n_blind']:>6} |", end="")

    for name, mtype in models:
        r = run_test(cut, mtype)
        if r:
            mark = "✅" if r["profit"] > 0 else ""
            print(f" {r['profit']*100:>+7.1f}%({r['n_hits']:>2}) {mark:<2}|", end="")
            all_results[mtype].append(r)
        else:
            print(f" {'N/A':>18} |", end="")
    print()

print("-" * 95)

# 汇总
print(f"\n【汇总】")
print(f"{'模型':<20} {'正收益':>10} {'总票数':>10} {'总成本':>10} {'总派彩':>10} {'总盈利率':>12}")
print("-" * 80)

for name, mtype in models:
    results = all_results[mtype]
    if not results:
        continue

    positive = sum(1 for r in results if r["profit"] > 0)
    total_tickets = sum(r["n_tickets"] for r in results)
    total_cost = sum(r["total_cost"] for r in results)
    total_payout = sum(r["total_payout"] for r in results)
    total_profit = (total_payout - total_cost) / total_cost if total_cost > 0 else 0

    mark = "✅" if positive == len(results) else ""
    print(f"{name:<20} {positive}/{len(results):>7} {total_tickets:>10} {total_cost:>10.0f} {total_payout:>10.0f} {total_profit*100:>+11.2f}% {mark}")
