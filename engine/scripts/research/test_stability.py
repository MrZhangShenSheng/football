# -*- coding: utf-8 -*-
"""多切分点稳定性测试"""
import sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path("engine/scripts/research")))

from ticket_dynamic_k_backtest import build_model_packs, run_dynamic_backtest

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

print("=" * 90)
print("多切分点稳定性测试 —— 动态1-2选(gap>0.05选1)")
print("=" * 90)
print(f"\n{'切分点':<12} {'训练样本':>10} {'盲测场':>10} {'票数':>8} {'成本':>10} {'派彩':>10} {'盈利率':>10}")
print("-" * 90)

results_all = []

for cut in cuts:
    packs = build_model_packs(cut_date=cut)
    results = run_dynamic_backtest(packs)

    # 找最优策略
    best = [r for r in results if r["name"] == "动态1-2选(gap>0.05选1)"]
    if best:
        r = best[0]
        mark = " ✅" if r["profit"] > 0 else ""
        print(f"{cut:<12} {r.get('n_train', '?'):>10} {len(packs):>10} {r['n_tickets']:>8} "
              f"{r['total_cost']:>10.0f} {r['total_payout']:>10.0f} {r['profit']*100:>9.1f}%{mark}")
        results_all.append({
            "cut": cut,
            "n_packs": len(packs),
            "n_tickets": r["n_tickets"],
            "profit": r["profit"],
        })

print("-" * 90)

# 汇总
if results_all:
    avg_profit = sum(r["profit"] for r in results_all) / len(results_all)
    positive = sum(1 for r in results_all if r["profit"] > 0)
    print(f"\n汇总：")
    print(f"  测试切分点：{len(results_all)} 个")
    print(f"  正收益切分点：{positive} 个 ({positive/len(results_all)*100:.0f}%)")
    print(f"  平均盈利率：{avg_profit*100:.1f}%")
