# -*- coding: utf-8 -*-
"""多场景模型 —— 滚动投注模拟（真实资金流）"""
import json
import sys
from collections import defaultdict
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path("engine/scripts/research")))

from multi_scene_model import MultiScenePredictor, TeamData
import score_family_model as sfm

print("=" * 100)
print("多场景模型 —— 滚动投注模拟")
print("假设：2025-12-01 起始资金 1000 元，派奖复购")
print("=" * 100)

INITIAL_CAPITAL = 1000.0
START_DATE = "2025-12-01"
UNIT = 2.0


def team_stats_to_team_data(ts) -> TeamData:
    return TeamData(
        n=ts.n, gf=ts.gf, ga=ts.ga, win=ts.win, gd=ts.gd,
        cs=ts.cs, becs=ts.becs, btts=ts.btts, over25=ts.over25,
        gf_home=ts.gf_side[0][0], gf_home_n=ts.gf_side[0][1],
        gf_away=ts.gf_side[1][0], gf_away_n=ts.gf_side[1][1],
        ga_home=ts.ga_side[0][0], ga_home_n=ts.ga_side[0][1],
        ga_away=ts.ga_side[1][0], ga_away_n=ts.ga_side[1][1],
        recent_gd=ts.recent_gd.copy(), recent=ts.recent.copy(),
    )


def load_hist_full():
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
            key = (str(m.get("date") or "")[:10], m.get("home"), m.get("away"))
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "date": str(m.get("date") or "")[:10],
                "home_zh": m.get("home"), "away_zh": m.get("away"),
                "actual": (h, a), "odds": odds,
            })
    out.sort(key=lambda x: x["date"])
    return out


# 加载数据
zh = {}
for tid, srcs in sfm.load_aliases().items():
    if srcs.get("zh"):
        zh[srcs["zh"]] = tid

tl = sfm.league_timeline()
hist = load_hist_full()

print(f"历史数据：{len(hist)} 场")

# 构建盲测数据（从2025-12-01开始）
blind = []
for m in hist:
    if m["date"] < START_DATE:
        continue
    hid, aid = zh.get(m["home_zh"]), zh.get(m["away_zh"])
    if hid and aid:
        blind.append({**m, "hid": hid, "aid": aid})

print(f"盲测样本（>={START_DATE}）：{len(blind)} 场")

# 合并时间线
merged = [("L", d, h, a, hg, ag, None) for d, h, a, hg, ag in tl]
merged += [("B", m["date"], m["hid"], m["aid"], m["actual"][0], m["actual"][1], i)
           for i, m in enumerate(blind)]
merged.sort(key=lambda r: (r[1], 0 if r[0] == "L" else 1))

# 滚动统计
stats = defaultdict(sfm.TeamStats)
predictor = MultiScenePredictor()
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
        prediction = predictor.predict(home_data, away_data)

        pred = prediction["predictions"]
        sorted_scores = sorted(pred.items(), key=lambda x: -x[1]["signal"])
        max_signal = sorted_scores[0][1]["signal"] if sorted_scores else 0

        match_packs.append({
            "date": m["date"],
            "actual": m["actual"],
            "odds": m["odds"],
            "sorted_scores": sorted_scores,
            "max_signal": max_signal,
            "home": m["home_zh"],
            "away": m["away_zh"],
        })

    if kind == "L":
        stats[h].add(hg, ag, True, a)
        stats[a].add(ag, hg, False, h)

print(f"有效预测包：{len(match_packs)} 场")

# 按日期分组
by_day = defaultdict(list)
for p in match_packs:
    by_day[p["date"]].append(p)

print(f"跨越 {len(by_day)} 天")


# ============================================================
# 滚动投注模拟
# ============================================================

print("\n" + "=" * 100)
print("滚动投注明细（2串1双选）")
print("=" * 100)

capital = INITIAL_CAPITAL
n_legs = 2
k_picks = 2

daily_log = []
monthly_summary = defaultdict(lambda: {"start": 0, "end": 0, "tickets": 0, "hits": 0, "cost": 0, "payout": 0})

print(f"\n{'日期':<12} {'票型':<15} {'成本':>8} {'派彩':>10} {'盈亏':>10} {'余额':>10} {'备注'}")
print("-" * 90)

prev_month = None

for day in sorted(by_day):
    day_packs = by_day[day]

    # 记录月初余额
    month = day[:7]
    if month != prev_month:
        if prev_month:
            monthly_summary[prev_month]["end"] = capital
        monthly_summary[month]["start"] = capital
        prev_month = month

    if len(day_packs) < n_legs:
        continue

    # 选场：按信号排序取前2场
    selected = sorted(day_packs, key=lambda p: -p["max_signal"])[:n_legs]

    # 每场选2个比分
    all_picks = []
    for p in selected:
        picks = []
        for score, info in p["sorted_scores"][:k_picks]:
            odds = p["odds"].get(score, 999)
            picks.append((score, odds, p["actual"]))
        all_picks.append(picks)

    # 生成所有投注组合
    all_bets = list(product(*all_picks))
    cost = len(all_bets) * UNIT

    # 检查资金是否足够
    if capital < cost:
        print(f"{day:<12} {'资金不足':<15} {cost:>8.0f} {'-':>10} {'-':>10} {capital:>10.1f} 跳过")
        continue

    # 扣除成本
    capital -= cost

    # 计算派彩
    payout = 0.0
    hit_scores = None
    for combo in all_bets:
        all_correct = all(score == actual for score, odds, actual in combo)
        if all_correct:
            combo_odds = 1.0
            for score, odds, actual in combo:
                combo_odds *= odds
            payout += UNIT * combo_odds
            hit_scores = [score for score, odds, actual in combo]

    # 加入派彩
    capital += payout
    profit = payout - cost

    # 记录
    is_hit = payout > 0
    ticket_desc = f"{n_legs}串1×{len(all_bets)}注"

    monthly_summary[month]["tickets"] += 1
    monthly_summary[month]["cost"] += cost
    monthly_summary[month]["payout"] += payout
    if is_hit:
        monthly_summary[month]["hits"] += 1

    if is_hit:
        note = f"✅ 中! {hit_scores}"
    else:
        note = ""

    daily_log.append({
        "date": day,
        "cost": cost,
        "payout": payout,
        "profit": profit,
        "capital": capital,
        "is_hit": is_hit,
        "matches": [(p["home"][:4], p["away"][:4], p["actual"]) for p in selected],
    })

    # 只打印中奖日和关键节点
    if is_hit or day.endswith("-01") or day.endswith("-15"):
        print(f"{day:<12} {ticket_desc:<15} {cost:>8.0f} {payout:>10.1f} {profit:>+10.1f} {capital:>10.1f} {note}")

# 记录最后一个月结束
if prev_month:
    monthly_summary[prev_month]["end"] = capital


# ============================================================
# 月度汇总
# ============================================================

print("\n" + "=" * 100)
print("月度汇总")
print("=" * 100)

print(f"\n{'月份':<10} {'月初余额':>10} {'票数':>6} {'命中':>6} {'成本':>10} {'派彩':>10} {'盈亏':>10} {'月末余额':>10} {'月收益率':>10}")
print("-" * 100)

for month in sorted(monthly_summary.keys()):
    m = monthly_summary[month]
    profit = m["payout"] - m["cost"]
    rate = profit / m["start"] * 100 if m["start"] > 0 else 0
    mark = "✅" if profit > 0 else ""
    print(f"{month:<10} {m['start']:>10.1f} {m['tickets']:>6} {m['hits']:>6} {m['cost']:>10.0f} {m['payout']:>10.1f} {profit:>+10.1f} {m['end']:>10.1f} {rate:>+9.1f}% {mark}")


# ============================================================
# 最终结果
# ============================================================

print("\n" + "=" * 100)
print("最终结果")
print("=" * 100)

total_tickets = sum(m["tickets"] for m in monthly_summary.values())
total_hits = sum(m["hits"] for m in monthly_summary.values())
total_cost = sum(m["cost"] for m in monthly_summary.values())
total_payout = sum(m["payout"] for m in monthly_summary.values())
total_profit = total_payout - total_cost

print(f"""
起始日期：{START_DATE}
起始资金：{INITIAL_CAPITAL:.0f} 元
结束资金：{capital:.1f} 元
总盈亏：  {capital - INITIAL_CAPITAL:+.1f} 元
总收益率：{(capital - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100:+.1f}%

总票数：  {total_tickets}
总命中：  {total_hits}
命中率：  {total_hits / total_tickets * 100:.1f}%

总投入：  {total_cost:.0f} 元
总派彩：  {total_payout:.1f} 元
净盈亏：  {total_profit:+.1f} 元（投入口径）
""")


# ============================================================
# 资金曲线关键节点
# ============================================================

print("\n" + "=" * 100)
print("资金曲线关键节点")
print("=" * 100)

# 找最高点和最低点
max_capital = max(d["capital"] for d in daily_log)
min_capital = min(d["capital"] for d in daily_log)
max_day = [d for d in daily_log if d["capital"] == max_capital][0]
min_day = [d for d in daily_log if d["capital"] == min_capital][0]

print(f"""
最高点：{max_day['date']} 余额 {max_capital:.1f} 元
最低点：{min_day['date']} 余额 {min_capital:.1f} 元
最大回撤：{(max_capital - min_capital) / max_capital * 100:.1f}%（从最高点算）

命中明细：
""")

for d in daily_log:
    if d["is_hit"]:
        print(f"  {d['date']}: 成本{d['cost']:.0f} 派彩{d['payout']:.1f} 盈利{d['profit']:+.1f} 余额{d['capital']:.1f}")
