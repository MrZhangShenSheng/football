# -*- coding: utf-8 -*-
"""B1 模型大样本验证 —— 多切分点测试"""
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
print("B1 模型（直接预测比分）大样本验证")
print("=" * 100)

# 加载数据
zh = {}
for tid, srcs in sfm.load_aliases().items():
    if srcs.get("zh"):
        zh[srcs["zh"]] = tid

tl = sfm.league_timeline()
hist = load_hist_full()


def run_full_test(cut_date, model_type="base"):
    """完整测试流程

    model_type: "base"=族模型, "B1"=直接预测比分
    """
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

    if len(train_data) < 100 or len(blind_data) < 50:
        return None

    X_tr = np.array([d["X"] for d in train_data])
    X_bl = np.array([b["X"] for b in blind_data])

    if model_type == "base":
        # 族模型
        y_tr = np.array([sfm.CLASSES.index(d["family"]) for d in train_data])
        model = sfm.train_softmax(X_tr, y_tr, len(sfm.CLASSES))
        P = sfm.predict_proba(model, X_bl)

        # 族内比分频率
        fam_dist = defaultdict(Counter)
        for d in train_data:
            fam_dist[d["family"]][d["score"]] += 1

        # 构建预测包
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

    else:  # B1: 直接预测比分
        # 比分标签
        score_set = sorted(set(d["score"] for d in train_data))
        score_to_idx = {s: i for i, s in enumerate(score_set)}
        y_tr = np.array([score_to_idx[d["score"]] for d in train_data])

        model = sfm.train_softmax(X_tr, y_tr, len(score_set))
        P = sfm.predict_proba(model, X_bl)

        # 构建预测包
        packs = []
        for i, b in enumerate(blind_data):
            model_probs = {score_set[j]: P[i][j] for j in range(len(score_set))}
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

        day_sorted = sorted(day_packs, key=lambda p: -p["gap"])[:2]
        p1, p2 = day_sorted[0], day_sorted[1]

        k1 = 1 if p1["gap"] > 0.05 else 2
        k2 = 1 if p2["gap"] > 0.05 else 2

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

print(f"\n{'切分点':<12} {'盲测场':>8} {'票数':>6} | {'基准盈利':>10} {'基准净利':>10} | {'B1盈利':>10} {'B1净利':>10} | {'差异':>10}")
print("-" * 105)

base_results = []
b1_results = []

for cut in cuts:
    r_base = run_full_test(cut, "base")
    r_b1 = run_full_test(cut, "B1")

    if r_base and r_b1:
        base_net = r_base["total_payout"] - r_base["total_cost"]
        b1_net = r_b1["total_payout"] - r_b1["total_cost"]
        diff = r_b1["profit"] - r_base["profit"]

        mark_base = "✅" if r_base["profit"] > 0 else ""
        mark_b1 = "✅" if r_b1["profit"] > 0 else ""
        better = "⬆️" if r_b1["profit"] > r_base["profit"] else "⬇️"

        print(f"{cut:<12} {r_base['n_blind']:>8} {r_base['n_tickets']:>6} | "
              f"{r_base['profit']*100:>+9.1f}%{mark_base} {base_net:>+10.0f} | "
              f"{r_b1['profit']*100:>+9.1f}%{mark_b1} {b1_net:>+10.0f} | "
              f"{diff*100:>+9.1f}% {better}")

        base_results.append(r_base)
        b1_results.append(r_b1)

print("-" * 105)

# 汇总
if base_results and b1_results:
    base_total_cost = sum(r["total_cost"] for r in base_results)
    base_total_payout = sum(r["total_payout"] for r in base_results)
    b1_total_cost = sum(r["total_cost"] for r in b1_results)
    b1_total_payout = sum(r["total_payout"] for r in b1_results)

    base_profit = (base_total_payout - base_total_cost) / base_total_cost
    b1_profit = (b1_total_payout - b1_total_cost) / b1_total_cost

    base_positive = sum(1 for r in base_results if r["profit"] > 0)
    b1_positive = sum(1 for r in b1_results if r["profit"] > 0)

    b1_better = sum(1 for rb, r1 in zip(base_results, b1_results) if r1["profit"] > rb["profit"])

    print(f"\n【汇总】")
    print(f"  测试切分点：{len(cuts)} 个")
    print(f"  ")
    print(f"  基准（族模型）：")
    print(f"    正收益：{base_positive}/{len(base_results)}")
    print(f"    总成本：{base_total_cost:.0f} 元")
    print(f"    总派彩：{base_total_payout:.0f} 元")
    print(f"    总盈利率：{base_profit*100:+.2f}%")
    print(f"  ")
    print(f"  B1（直接预测比分）：")
    print(f"    正收益：{b1_positive}/{len(b1_results)}")
    print(f"    总成本：{b1_total_cost:.0f} 元")
    print(f"    总派彩：{b1_total_payout:.0f} 元")
    print(f"    总盈利率：{b1_profit*100:+.2f}%")
    print(f"  ")
    print(f"  B1 优于基准：{b1_better}/{len(cuts)} 个切分点")
