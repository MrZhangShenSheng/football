# -*- coding: utf-8 -*-
"""
生产版预测器v3 完整回测验证

确认与研究脚本结果一致：
- 固定1倍：收益+133.5%
- 高价值2倍：收益+269.8%
"""
import json
import sys
from collections import defaultdict
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(".")))

from engine.multi_scene_predictor_v3 import (
    MultiScenePredictorV3,
    TeamData,
    team_stats_to_team_data,
    calc_parlay_multiplier,
)
import engine.scripts.research.score_family_model as sfm

print("=" * 100)
print("生产版预测器v3 完整回测验证")
print("=" * 100)

INITIAL_CAPITAL = 1000.0
START_DATE = "2025-12-01"
BASE_UNIT = 2.0


# ============================================================
# 数据加载
# ============================================================

def load_hist():
    ROOT = Path(".")
    out = []
    seen = set()
    for p in sorted((ROOT / "engine/cache/hist_odds").glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        for m in d.get("matches", []):
            sc = str(m.get("score") or "")
            crs = m.get("crs") or {}
            if ":" not in sc or not crs:
                continue
            try:
                h, a = (int(x) for x in sc.split(":")[:2])
            except ValueError:
                continue
            score_odds = {}
            for kk, v in crs.items():
                if str(kk).startswith("other") or not v:
                    continue
                try:
                    hh, aa = (int(x) for x in str(kk).split(":")[:2])
                    score_odds[(hh, aa)] = float(v)
                except (ValueError, TypeError):
                    continue
            if len(score_odds) < 20:
                continue
            key = (str(m.get("date") or "")[:10], m.get("home"), m.get("away"))
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "date": str(m.get("date") or "")[:10],
                "home_zh": m.get("home"),
                "away_zh": m.get("away"),
                "actual": (h, a),
                "score_odds": score_odds,
            })
    out.sort(key=lambda x: x["date"])
    return out


zh = {}
for tid, srcs in sfm.load_aliases().items():
    if srcs.get("zh"):
        zh[srcs["zh"]] = tid

tl = sfm.league_timeline()
hist = load_hist()

print(f"数据：历史{len(hist)}场")


# ============================================================
# 构建预测包
# ============================================================

blind = []
for m in hist:
    if m["date"] < START_DATE:
        continue
    hid, aid = zh.get(m["home_zh"]), zh.get(m["away_zh"])
    if hid and aid:
        blind.append({**m, "hid": hid, "aid": aid})

merged = [("L", d, h, a, hg, ag, None) for d, h, a, hg, ag in tl]
merged += [("B", m["date"], m["hid"], m["aid"], m["actual"][0], m["actual"][1], i)
           for i, m in enumerate(blind)]
merged.sort(key=lambda r: (r[1], 0 if r[0] == "L" else 1))

stats = defaultdict(sfm.TeamStats)
predictor = MultiScenePredictorV3()
match_packs = []

for r in merged:
    kind, date, h, a, hg, ag = r[0], r[1], r[2], r[3], r[4], r[5]
    if kind == "B":
        idx = r[6]
        m = blind[idx]
        if stats[h].n < sfm.MIN_HIST or stats[a].n < sfm.MIN_HIST:
            continue

        home_data = team_stats_to_team_data(stats[h])
        away_data = team_stats_to_team_data(stats[a])

        # 使用生产版预测器
        pred = predictor.predict(home_data, away_data, m["score_odds"])

        match_packs.append({
            "date": m["date"],
            "actual": m["actual"],
            "odds": m["score_odds"],
            "prediction": pred,
        })
    if kind == "L":
        stats[h].add(hg, ag, True, a)
        stats[a].add(ag, hg, False, h)

by_day = defaultdict(list)
for p in match_packs:
    by_day[p["date"]].append(p)

print(f"有效预测包：{len(match_packs)}场，跨越{len(by_day)}天")


# ============================================================
# 回测函数
# ============================================================

def run_backtest(by_day, use_dynamic_multiplier=False):
    """回测2串1双选"""
    capital = INITIAL_CAPITAL
    total_cost = 0
    total_payout = 0
    n_tickets = 0
    n_hits = 0
    min_capital = capital
    multiplier_dist = defaultdict(int)

    for day in sorted(by_day):
        day_packs = by_day[day]
        if len(day_packs) < 2:
            continue

        for p in day_packs:
            p["max_signal"] = p["prediction"]["sorted_scores"][0][1]
        selected = sorted(day_packs, key=lambda x: -x["max_signal"])[:2]

        # 计算倍率
        if use_dynamic_multiplier:
            multiplier = calc_parlay_multiplier([p["prediction"] for p in selected])
        else:
            multiplier = 1

        multiplier_dist[multiplier] += 1
        unit = BASE_UNIT * multiplier

        all_picks = []
        for p in selected:
            picks = []
            for score, sig in p["prediction"]["sorted_scores"][:2]:
                odds = p["odds"].get(score, 999)
                picks.append((score, odds, p["actual"]))
            all_picks.append(picks)

        all_bets = list(product(*all_picks))
        cost = len(all_bets) * unit

        if capital < cost:
            continue

        capital -= cost
        total_cost += cost
        n_tickets += 1

        payout = 0
        for combo in all_bets:
            if all(score == actual for score, odds, actual in combo):
                combo_odds = 1.0
                for score, odds, actual in combo:
                    combo_odds *= odds
                payout += unit * combo_odds

        capital += payout
        total_payout += payout
        if payout > 0:
            n_hits += 1

        min_capital = min(min_capital, capital)

    return {
        "final": capital,
        "profit": (capital - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100,
        "n_tickets": n_tickets,
        "n_hits": n_hits,
        "cost": total_cost,
        "payout": total_payout,
        "max_drawdown": (INITIAL_CAPITAL - min_capital) / INITIAL_CAPITAL * 100,
        "multiplier_dist": dict(multiplier_dist),
    }


# ============================================================
# 运行回测
# ============================================================

print("\n" + "=" * 100)
print("回测结果")
print("=" * 100)

# 固定1倍
result_fixed = run_backtest(by_day, use_dynamic_multiplier=False)
print(f"\n【固定1倍】")
print(f"  最终资金：{result_fixed['final']:.1f} 元")
print(f"  收益率：{result_fixed['profit']:+.1f}%")
print(f"  命中：{result_fixed['n_hits']}/{result_fixed['n_tickets']}")
print(f"  回撤：{result_fixed['max_drawdown']:.1f}%")

# 动态倍率（高价值2倍）
result_dynamic = run_backtest(by_day, use_dynamic_multiplier=True)
print(f"\n【高价值2倍（动态倍率）】")
print(f"  最终资金：{result_dynamic['final']:.1f} 元")
print(f"  收益率：{result_dynamic['profit']:+.1f}%")
print(f"  命中：{result_dynamic['n_hits']}/{result_dynamic['n_tickets']}")
print(f"  回撤：{result_dynamic['max_drawdown']:.1f}%")
print(f"  倍率分布：{result_dynamic['multiplier_dist']}")

# 对比
print("\n" + "=" * 100)
print("对比验证")
print("=" * 100)
print(f"\n{'策略':<20} {'研究脚本结果':>15} {'生产版结果':>15} {'一致性':>10}")
print("-" * 70)
print(f"{'固定1倍':<20} {'+133.5%':>15} {result_fixed['profit']:>+14.1f}% {'✅' if abs(result_fixed['profit'] - 133.5) < 5 else '❌':>10}")
print(f"{'高价值2倍':<20} {'+269.8%':>15} {result_dynamic['profit']:>+14.1f}% {'✅' if abs(result_dynamic['profit'] - 269.8) < 5 else '❌':>10}")

print("\n" + "=" * 100)
print("验证完成")
print("=" * 100)
