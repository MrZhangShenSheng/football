# -*- coding: utf-8 -*-
r"""混合过关回测 v4（2026-09-29）—— CRS + HAD + TTG。

hist_odds 数据有：CRS、HAD、TTG、halfScore
无：HAFU（需要用 halfScore 计算）

测试组合：
- 纯CRS / 纯HAD / 纯TTG（对照）
- CRS + HAD
- CRS + TTG
- HAD + TTG
- CRS + HAD + TTG

开发者 sszhang
"""
from __future__ import annotations

import json
import math
import random
from collections import defaultdict
from itertools import combinations, product
from pathlib import Path

random.seed(42)

HIST_ODDS_DIR = Path(__file__).resolve().parents[2] / "cache" / "hist_odds"


def load_data():
    """加载数据"""
    matches = []
    for p in sorted(HIST_ODDS_DIR.glob("*.json")):
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            for m in data.get("matches", []):
                crs = m.get("crs") or {}
                had = m.get("had") or {}
                ttg = m.get("ttg") or {}
                score = m.get("score")
                half_score = m.get("halfScore")

                if not score:
                    continue

                # 解析比分
                if isinstance(score, str) and ":" in score:
                    h, a = map(int, score.split(":"))
                elif isinstance(score, (list, tuple)):
                    h, a = score
                else:
                    continue

                # 解析半场比分（用于计算 HAFU 结果）
                hh, ha = None, None
                if half_score:
                    if isinstance(half_score, str) and ":" in half_score:
                        hh, ha = map(int, half_score.split(":"))
                    elif isinstance(half_score, (list, tuple)):
                        hh, ha = half_score

                # 解析赔率
                crs_odds = {k: float(v) for k, v in crs.items() if v}
                had_odds = {k.lower(): float(v) for k, v in had.items() if v}
                ttg_odds = {k: float(v) for k, v in ttg.items() if v}

                if len(crs_odds) < 10 or len(had_odds) < 3 or len(ttg_odds) < 5:
                    continue

                # 实际结果
                actual_crs = f"{h}:{a}"
                if actual_crs not in crs_odds:
                    if h > a:
                        actual_crs = "other_h"
                    elif h < a:
                        actual_crs = "other_a"
                    else:
                        actual_crs = "other_d"

                actual_had = 'h' if h > a else ('a' if h < a else 'd')
                actual_ttg = str(min(h + a, 7))

                # 计算 HAFU 结果（用半场+全场比分）
                actual_hafu = None
                if hh is not None and ha is not None:
                    half_r = 'h' if hh > ha else ('a' if hh < ha else 'd')
                    full_r = 'h' if h > a else ('a' if h < a else 'd')
                    actual_hafu = half_r + full_r

                matches.append({
                    "actual_crs": actual_crs,
                    "actual_had": actual_had,
                    "actual_ttg": actual_ttg,
                    "actual_hafu": actual_hafu,
                    "crs_odds": crs_odds,
                    "had_odds": had_odds,
                    "ttg_odds": ttg_odds,
                })
        except:
            continue
    return matches


def pick_top_k(odds_dict, k=1):
    """按赔率升序选 top-k"""
    if not odds_dict:
        return []
    sorted_opts = sorted(odds_dict.items(), key=lambda kv: kv[1])
    return sorted_opts[:k]


def check_hit(pool, pick, m):
    """检查命中"""
    if pool == "crs":
        return pick == m["actual_crs"]
    elif pool == "had":
        return pick == m["actual_had"]
    elif pool == "ttg":
        return pick == m["actual_ttg"]
    return False


def simulate_2c1(m1, m2, config):
    """
    模拟 2串1。
    config = {"crs": 2, "had": 1, "ttg": 0}  # 每个玩法选几个选项
    """
    def build_opts(m, cfg):
        opts = []
        for pool, k in cfg.items():
            if k <= 0:
                continue
            if pool == "crs":
                for pick, odds in pick_top_k(m["crs_odds"], k):
                    opts.append(("crs", pick, odds))
            elif pool == "had":
                for pick, odds in pick_top_k(m["had_odds"], k):
                    opts.append(("had", pick, odds))
            elif pool == "ttg":
                for pick, odds in pick_top_k(m["ttg_odds"], k):
                    opts.append(("ttg", pick, odds))
        return opts

    leg1 = build_opts(m1, config)
    leg2 = build_opts(m2, config)

    if not leg1 or not leg2:
        return None

    all_bets = list(product(leg1, leg2))
    n_bets = len(all_bets)
    cost = n_bets * 2
    payout = 0.0
    any_hit = False
    max_mult = 0

    for (p1, pick1, o1), (p2, pick2, o2) in all_bets:
        if check_hit(p1, pick1, m1) and check_hit(p2, pick2, m2):
            combo_odds = o1 * o2
            payout += 2 * combo_odds
            any_hit = True
            max_mult = max(max_mult, combo_odds)

    return {"cost": cost, "payout": payout, "hit": any_hit, "max_mult": max_mult}


def run_backtest(matches, n_sim=3000):
    """运行回测"""
    # 配置列表：{组合名: {crs: k, had: k, ttg: k}}
    configs = [
        # 纯单玩法
        ("纯CRS单选", {"crs": 1, "had": 0, "ttg": 0}),
        ("纯CRS双选", {"crs": 2, "had": 0, "ttg": 0}),
        ("纯HAD单选", {"crs": 0, "had": 1, "ttg": 0}),
        ("纯HAD双选", {"crs": 0, "had": 2, "ttg": 0}),
        ("纯TTG单选", {"crs": 0, "had": 0, "ttg": 1}),
        ("纯TTG双选", {"crs": 0, "had": 0, "ttg": 2}),

        # CRS + HAD
        ("CRS单+HAD单", {"crs": 1, "had": 1, "ttg": 0}),
        ("CRS单+HAD双", {"crs": 1, "had": 2, "ttg": 0}),
        ("CRS双+HAD单", {"crs": 2, "had": 1, "ttg": 0}),
        ("CRS双+HAD双", {"crs": 2, "had": 2, "ttg": 0}),

        # CRS + TTG
        ("CRS单+TTG单", {"crs": 1, "had": 0, "ttg": 1}),
        ("CRS单+TTG双", {"crs": 1, "had": 0, "ttg": 2}),
        ("CRS双+TTG单", {"crs": 2, "had": 0, "ttg": 1}),
        ("CRS双+TTG双", {"crs": 2, "had": 0, "ttg": 2}),

        # HAD + TTG
        ("HAD单+TTG单", {"crs": 0, "had": 1, "ttg": 1}),
        ("HAD单+TTG双", {"crs": 0, "had": 1, "ttg": 2}),
        ("HAD双+TTG单", {"crs": 0, "had": 2, "ttg": 1}),
        ("HAD双+TTG双", {"crs": 0, "had": 2, "ttg": 2}),

        # 三玩法
        ("CRS单+HAD单+TTG单", {"crs": 1, "had": 1, "ttg": 1}),
        ("CRS双+HAD单+TTG单", {"crs": 2, "had": 1, "ttg": 1}),
        ("CRS单+HAD双+TTG单", {"crs": 1, "had": 2, "ttg": 1}),
        ("CRS双+HAD双+TTG单", {"crs": 2, "had": 2, "ttg": 1}),
        ("CRS单+HAD单+TTG双", {"crs": 1, "had": 1, "ttg": 2}),
        ("CRS双+HAD单+TTG双", {"crs": 2, "had": 1, "ttg": 2}),
    ]

    results = []
    for name, cfg in configs:
        total_cost = 0
        total_payout = 0
        total_hits = 0
        total_tickets = 0
        max_mults = []

        for _ in range(n_sim):
            m1, m2 = random.sample(matches, 2)
            r = simulate_2c1(m1, m2, cfg)
            if r is None:
                continue
            total_cost += r["cost"]
            total_payout += r["payout"]
            total_tickets += 1
            if r["hit"]:
                total_hits += 1
                max_mults.append(r["max_mult"])

        if total_tickets == 0:
            continue

        hit_rate = total_hits / total_tickets
        recovery = total_payout / total_cost if total_cost > 0 else 0
        profit = recovery - 1
        avg_mult = sum(max_mults) / len(max_mults) if max_mults else 0
        max_max = max(max_mults) if max_mults else 0
        avg_cost = total_cost / total_tickets

        results.append({
            "name": name,
            "cost": avg_cost,
            "hit_rate": hit_rate,
            "recovery": recovery,
            "profit": profit,
            "avg_mult": avg_mult,
            "max_mult": max_max,
        })

    return results


def main():
    print("加载历史数据...")
    matches = load_data()
    print(f"共 {len(matches)} 场有效数据（CRS+HAD+TTG）\n")

    if len(matches) < 100:
        print("数据不足")
        return

    print("运行回测（24 种配置，每种 3000 次模拟）...\n")
    results = run_backtest(matches, 3000)

    # 按盈利率排序
    results_sorted = sorted(results, key=lambda x: -x["profit"])

    print("=" * 110)
    print("混合过关回测结果（2串1，按盈利率排序）")
    print("=" * 110)
    print(f"{'组合':<25} {'成本':>8} {'命中率':>10} {'回收率':>10} {'盈利率':>10} {'命中倍数':>10} {'最大倍数':>10}")
    print("-" * 110)

    for r in results_sorted:
        print(f"{r['name']:<25} {r['cost']:>8.1f} {r['hit_rate']*100:>9.2f}% "
              f"{r['recovery']*100:>9.2f}% {r['profit']*100:>9.2f}% "
              f"{r['avg_mult']:>10.1f} {r['max_mult']:>10.1f}")

    print("-" * 110)

    # 关键对比
    print("\n" + "=" * 80)
    print("关键发现")
    print("=" * 80)

    # 命中率 Top 5
    print("\n【命中率 Top 5】")
    for r in sorted(results, key=lambda x: -x["hit_rate"])[:5]:
        print(f"  {r['name']:<25} 命中率 {r['hit_rate']*100:>6.2f}%  盈利率 {r['profit']*100:>7.2f}%")

    # 盈利率 Top 5
    print("\n【盈利率 Top 5】（越接近0越好）")
    for r in results_sorted[:5]:
        print(f"  {r['name']:<25} 盈利率 {r['profit']*100:>7.2f}%  命中率 {r['hit_rate']*100:>6.2f}%")

    # 命中倍数 Top 5
    print("\n【命中倍数 Top 5】（右尾肥度）")
    for r in sorted(results, key=lambda x: -x["avg_mult"])[:5]:
        print(f"  {r['name']:<25} 命中倍数 {r['avg_mult']:>7.1f}x  命中率 {r['hit_rate']*100:>6.2f}%")

    # 正盈利组合
    positive = [r for r in results if r["profit"] > 0]
    if positive:
        print(f"\n【正盈利组合】共 {len(positive)} 个")
        for r in positive:
            print(f"  {r['name']:<25} 盈利率 +{r['profit']*100:.2f}%")
    else:
        print("\n【无正盈利组合】- 所有组合盈利率均为负")

    # 单玩法 vs 混合
    print("\n【单玩法 vs 混合对比】")
    pure_crs = next((r for r in results if r["name"] == "纯CRS双选"), None)
    pure_had = next((r for r in results if r["name"] == "纯HAD双选"), None)
    mixed = next((r for r in results if r["name"] == "CRS双+HAD双"), None)
    if pure_crs and pure_had and mixed:
        print(f"  纯CRS双选:   命中率 {pure_crs['hit_rate']*100:>6.2f}%  盈利率 {pure_crs['profit']*100:>7.2f}%  倍数 {pure_crs['avg_mult']:>6.1f}x")
        print(f"  纯HAD双选:   命中率 {pure_had['hit_rate']*100:>6.2f}%  盈利率 {pure_had['profit']*100:>7.2f}%  倍数 {pure_had['avg_mult']:>6.1f}x")
        print(f"  CRS双+HAD双: 命中率 {mixed['hit_rate']*100:>6.2f}%  盈利率 {mixed['profit']*100:>7.2f}%  倍数 {mixed['avg_mult']:>6.1f}x")


if __name__ == "__main__":
    main()
