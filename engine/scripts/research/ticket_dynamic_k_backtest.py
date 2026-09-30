# -*- coding: utf-8 -*-
r"""动态选腿回测（2026-09-29）—— 根据 gap 决定每场选几个比分。

核心思路：
- gap 大（模型很确定）→ 选少一点（1个）→ 成本低、赔率高
- gap 小（模型不确定）→ 选多一点（2-3个）→ 命中率高

用法：
  python ticket_dynamic_k_backtest.py                    # 2026-01-01 起（大样本）
  python ticket_dynamic_k_backtest.py --cut 2026-07-01   # 原始小样本
  python ticket_dynamic_k_backtest.py --detail           # 打印逐票明细

开发者 sszhang
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from itertools import product
from pathlib import Path

random.seed(42)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import score_family_model as sfm

ROOT = sfm.ROOT
UNIT = 2.0
DEFAULT_CUT = "2026-01-01"


def load_hist_full():
    """加载 hist_odds 完整数据"""
    out = []
    for p in sorted((ROOT / "engine/cache/hist_odds").glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        for m in d.get("matches", []):
            sc = str(m.get("score") or "")
            crs = m.get("crs") or {}
            if ":" not in sc or len(crs) < 20:
                continue
            try:
                h, a = (int(x) for x in sc.split(":")[:2])
            except ValueError:
                continue
            odds = {}
            for kk, v in crs.items():
                if str(kk).startswith("other") or not v:
                    continue
                try:
                    hh, aa = (int(x) for x in str(kk).split(":")[:2])
                    odds[(hh, aa)] = float(v)
                except (ValueError, TypeError):
                    continue
            if len(odds) < 20:
                continue
            out.append({
                "date": str(m.get("date") or "")[:10],
                "home_zh": m.get("home"),
                "away_zh": m.get("away"),
                "actual": (h, a),
                "odds": odds,
            })
    out.sort(key=lambda x: x["date"])
    return out


def build_model_packs(cut_date=DEFAULT_CUT):
    """构建模型预测包（用 v2 26维特征）"""
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

    from common import strict_merged   # 2026-09-30 修自泄漏：原排序让被预测场赛果先入统计
    merged = strict_merged(tl, [(m["date"], m["hid"], m["aid"], m["actual"][0], m["actual"][1], i)
                                for i, m in enumerate(blind)])

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

    print(f"训练 v2 模型：{len(X_tr)} 训练样本，{len(X_bl)} 盲测样本（cut={cut_date}）")
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

    return packs


def dynamic_k(gap, strategy="dynamic_1_2", thresholds=(0.03, 0.015)):
    """根据 gap 决定选几个比分"""
    if strategy == "fixed_1":
        return 1
    elif strategy == "fixed_2":
        return 2
    elif strategy == "fixed_3":
        return 3
    elif strategy == "dynamic_1_2":
        return 1 if gap > thresholds[0] else 2
    elif strategy == "dynamic_1_3":
        if gap > thresholds[0]:
            return 1
        elif gap > thresholds[1]:
            return 2
        else:
            return 3
    elif strategy == "dynamic_2_3":
        return 2 if gap > thresholds[0] else 3
    return 2


def simulate_2c1_dynamic(p1, p2, strategy, thresholds):
    """模拟动态选腿的 2串1"""
    k1 = dynamic_k(p1["gap"], strategy, thresholds)
    k2 = dynamic_k(p2["gap"], strategy, thresholds)

    picks1 = [(s, p1["odds"].get(s, 999)) for s, prob in p1["model_sorted"][:k1]]
    picks2 = [(s, p2["odds"].get(s, 999)) for s, prob in p2["model_sorted"][:k2]]

    if not picks1 or not picks2:
        return None

    all_bets = list(product(picks1, picks2))
    cost = len(all_bets) * UNIT
    payout = 0.0
    any_hit = False
    max_mult = 0

    for (s1, o1), (s2, o2) in all_bets:
        if s1 == p1["actual"] and s2 == p2["actual"]:
            combo_odds = o1 * o2
            payout += UNIT * combo_odds
            any_hit = True
            max_mult = max(max_mult, combo_odds)

    return {
        "cost": cost,
        "payout": payout,
        "hit": any_hit,
        "max_mult": max_mult,
        "k1": k1,
        "k2": k2,
    }


def run_dynamic_backtest(packs):
    """动态选腿回测"""
    by_day = defaultdict(list)
    for p in packs:
        by_day[p["date"]].append(p)

    strategies = [
        ("固定1选", "fixed_1", (0.03, 0.015)),
        ("固定2选", "fixed_2", (0.03, 0.015)),
        ("固定3选", "fixed_3", (0.03, 0.015)),
        ("动态1-2选(gap>0.03选1)", "dynamic_1_2", (0.03, 0.015)),
        ("动态1-2选(gap>0.05选1)", "dynamic_1_2", (0.05, 0.015)),
        ("动态1-2选(gap>0.08选1)", "dynamic_1_2", (0.08, 0.015)),
        ("动态1-3选(0.05/0.02)", "dynamic_1_3", (0.05, 0.02)),
        ("动态1-3选(0.08/0.03)", "dynamic_1_3", (0.08, 0.03)),
        ("动态2-3选(gap>0.03选2)", "dynamic_2_3", (0.03, 0.015)),
    ]

    results = []

    for name, strategy, thresholds in strategies:
        total_cost = 0.0
        total_payout = 0.0
        total_hits = 0
        total_tickets = 0
        max_mult_seen = 0

        for day in sorted(by_day):
            day_packs = by_day[day]
            if len(day_packs) < 2:
                continue

            day_sorted = sorted(day_packs, key=lambda p: -p["gap"])
            p1, p2 = day_sorted[0], day_sorted[1]

            result = simulate_2c1_dynamic(p1, p2, strategy, thresholds)
            if result is None:
                continue

            total_cost += result["cost"]
            total_payout += result["payout"]
            total_tickets += 1
            if result["hit"]:
                total_hits += 1
            max_mult_seen = max(max_mult_seen, result["max_mult"])

        if total_cost == 0 or total_tickets == 0:
            continue

        hit_rate = total_hits / total_tickets
        recovery = total_payout / total_cost
        profit = recovery - 1
        avg_cost = total_cost / total_tickets

        results.append({
            "name": name,
            "n_tickets": total_tickets,
            "total_cost": total_cost,
            "total_payout": total_payout,
            "n_hits": total_hits,
            "hit_rate": hit_rate,
            "profit": profit,
            "recovery": recovery,
            "avg_cost": avg_cost,
            "max_mult": max_mult_seen,
        })

    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cut", default=DEFAULT_CUT, help="盲测起点（默认2026-01-01）")
    ap.add_argument("--detail", action="store_true", help="打印逐票明细")
    args = ap.parse_args()

    print("=" * 90)
    print("动态选腿回测 —— 根据 gap 决定每场选几个比分")
    print("=" * 90)

    print("\n构建模型预测包...")
    packs = build_model_packs(cut_date=args.cut)

    by_day = defaultdict(list)
    for p in packs:
        by_day[p["date"]].append(p)
    print(f"共 {len(packs)} 场 / {len(by_day)} 天\n")

    # gap 分布统计
    gaps = [p["gap"] for p in packs]
    print("Gap 分布统计：")
    print(f"  min={min(gaps):.4f}, max={max(gaps):.4f}, mean={sum(gaps)/len(gaps):.4f}")
    print(f"  >0.08: {sum(1 for g in gaps if g > 0.08)} 场 ({sum(1 for g in gaps if g > 0.08)/len(gaps)*100:.1f}%)")
    print(f"  >0.05: {sum(1 for g in gaps if g > 0.05)} 场 ({sum(1 for g in gaps if g > 0.05)/len(gaps)*100:.1f}%)")
    print(f"  >0.03: {sum(1 for g in gaps if g > 0.03)} 场 ({sum(1 for g in gaps if g > 0.03)/len(gaps)*100:.1f}%)")

    # 跑所有策略
    results = run_dynamic_backtest(packs)

    print("\n" + "=" * 90)
    print("所有策略对比（按盈利率排序）")
    print("=" * 90)

    print(f"\n{'策略':<30} {'票数':>6} {'成本':>10} {'派彩':>10} {'命中':>6} {'盈利率':>10} {'最大倍数':>10}")
    print("-" * 90)

    for r in sorted(results, key=lambda x: -x["profit"]):
        mark = " ✅" if r["profit"] > 0 else ""
        print(f"{r['name']:<30} {r['n_tickets']:>6} {r['total_cost']:>10.0f} {r['total_payout']:>10.0f} "
              f"{r['n_hits']:>6} {r['profit']*100:>9.1f}%{mark} {r['max_mult']:>10.1f}x")

    print("-" * 90)

    # 最优策略详情
    best = max(results, key=lambda x: x["profit"])
    print(f"\n【最优策略】{best['name']}")
    print(f"  总票数：{best['n_tickets']} 票")
    print(f"  总成本：{best['total_cost']:.0f} 元")
    print(f"  总派彩：{best['total_payout']:.0f} 元")
    print(f"  净利润：{best['total_payout'] - best['total_cost']:.0f} 元")
    print(f"  命中票：{best['n_hits']} 票 ({best['hit_rate']*100:.1f}%)")
    print(f"  盈利率：{best['profit']*100:+.1f}%")


if __name__ == "__main__":
    main()
