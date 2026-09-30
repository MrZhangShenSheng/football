# -*- coding: utf-8 -*-
"""
生产版v4b验证脚本

确认生产版 engine/predictor_v4b.py 与研究脚本结果一致
期望结果：收益率+341.4%，命中39/253
"""
import json
import sys
from collections import defaultdict
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(".")))

from engine.predictor_v4b import PredictorV4b, TeamData, team_stats_to_team_data
import engine.scripts.research.score_family_model as sfm

print("=" * 80)
print("生产版v4b验证")
print("=" * 80)

INITIAL_CAPITAL = 1000.0
START_DATE = "2025-10-01"
BASE_UNIT = 2.0


# 数据加载
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

print(f"数据：{len(hist)}场")

# 构建盲测数据
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
predictor = PredictorV4b()
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
        pred = predictor.predict(home_data, away_data)
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


# 回测
capital = INITIAL_CAPITAL
total_cost = 0
total_payout = 0
n_tickets = 0
n_hits = 0
min_capital = capital
max_capital = capital

for day in sorted(by_day):
    day_packs = by_day[day]
    if len(day_packs) < 2:
        continue

    for p in day_packs:
        p["max_signal"] = p["prediction"][0][1]
    selected = sorted(day_packs, key=lambda x: -x["max_signal"])[:2]

    all_picks = []
    for p in selected:
        picks = []
        for score, sig in p["prediction"][:2]:
            odds = p["odds"].get(score, 999)
            picks.append((score, odds, p["actual"]))
        all_picks.append(picks)

    all_bets = list(product(*all_picks))
    cost = len(all_bets) * BASE_UNIT

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
            payout += BASE_UNIT * combo_odds

    capital += payout
    total_payout += payout
    if payout > 0:
        n_hits += 1

    min_capital = min(min_capital, capital)
    max_capital = max(max_capital, capital)

print()
print("=" * 80)
print("回测结果")
print("=" * 80)
print(f"最终资金: {capital:.1f}元")
print(f"收益率: {(capital - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100:+.1f}%")
print(f"命中: {n_hits}/{n_tickets} ({n_hits/n_tickets*100:.1f}%)")
print(f"总成本: {total_cost:.0f}元")
print(f"总派彩: {total_payout:.1f}元")
print(f"最大回撤: {(max_capital - min_capital) / max_capital * 100:.1f}%")

print()
print("=" * 80)
print("对比验证")
print("=" * 80)
expected_profit = 341.4
expected_hits = 39
expected_tickets = 253
actual_profit = (capital - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100

print(f"期望收益率: +{expected_profit}%")
print(f"实际收益率: {actual_profit:+.1f}%")
print(f"期望命中: {expected_hits}/{expected_tickets}")
print(f"实际命中: {n_hits}/{n_tickets}")

if abs(actual_profit - expected_profit) < 1 and n_hits == expected_hits:
    print("\n✅ 验证通过！生产版与研究脚本结果一致")
else:
    print("\n❌ 验证失败！结果不一致")
