# -*- coding: utf-8 -*-
"""多场景模型 —— 实票串关模拟回测"""
import json
import sys
from collections import Counter, defaultdict
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path("engine/scripts/research")))

from multi_scene_model import MultiScenePredictor, TeamData
import score_family_model as sfm

UNIT = 2.0

print("=" * 100)
print("多场景博弈模型 —— 实票串关模拟回测")
print("=" * 100)


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

cut = "2026-01-01"

# 构建盲测数据
blind = []
for m in hist:
    if m["date"] < cut:
        continue
    hid, aid = zh.get(m["home_zh"]), zh.get(m["away_zh"])
    if hid and aid:
        blind.append({**m, "hid": hid, "aid": aid})

print(f"盲测样本（>={cut}）：{len(blind)} 场")

# 合并时间线
merged = [("L", d, h, a, hg, ag, None) for d, h, a, hg, ag in tl]
merged += [("B", m["date"], m["hid"], m["aid"], m["actual"][0], m["actual"][1], i)
           for i, m in enumerate(blind)]
merged.sort(key=lambda r: (r[1], 0 if r[0] == "L" else 1))

# 滚动统计并生成预测
stats = defaultdict(sfm.TeamStats)
predictor = MultiScenePredictor()

match_packs = []  # 每场的预测包

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

        # 按信号排序取 Top 比分
        pred = prediction["predictions"]
        sorted_scores = sorted(pred.items(), key=lambda x: -x[1]["signal"])

        # 计算最大信号（用于选场）
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
# 串关模拟函数
# ============================================================

def simulate_parlay(packs, n_legs, k_picks, gap_threshold=0.05):
    """
    模拟串关投注

    packs: 当日预测包列表
    n_legs: 串关腿数（2/3/4）
    k_picks: 每场选几个比分（1=单选，2=双选）
    gap_threshold: 动态选腿阈值（信号>阈值用单选，否则双选）

    返回: (cost, payout, is_hit, details)
    """
    if len(packs) < n_legs:
        return None

    # 选场：按最大信号排序取前 n_legs 场
    selected = sorted(packs, key=lambda p: -p["max_signal"])[:n_legs]

    # 每场选比分
    all_picks = []
    for p in selected:
        # 动态选腿：信号高选单选，否则双选
        if isinstance(k_picks, str) and k_picks == "dynamic":
            k = 1 if p["max_signal"] > gap_threshold else 2
        else:
            k = k_picks

        picks = []
        for score, info in p["sorted_scores"][:k]:
            odds = p["odds"].get(score, 999)
            picks.append((score, odds, p["actual"]))
        all_picks.append(picks)

    # 生成所有投注组合
    all_bets = list(product(*all_picks))
    cost = len(all_bets) * UNIT

    # 计算派彩
    payout = 0.0
    hit_combo = None
    for combo in all_bets:
        all_correct = all(score == actual for score, odds, actual in combo)
        if all_correct:
            combo_odds = 1.0
            for score, odds, actual in combo:
                combo_odds *= odds
            payout += UNIT * combo_odds
            hit_combo = combo

    return {
        "cost": cost,
        "payout": payout,
        "is_hit": payout > 0,
        "n_bets": len(all_bets),
        "selected": [(p["home"], p["away"], p["actual"], p["max_signal"]) for p in selected],
        "hit_combo": hit_combo,
    }


# ============================================================
# 运行各种串关策略
# ============================================================

print("\n" + "=" * 100)
print("串关策略回测结果")
print("=" * 100)

strategies = [
    # (名称, 腿数, 选比分数, gap阈值)
    ("2串1-单选", 2, 1, None),
    ("2串1-双选", 2, 2, None),
    ("2串1-动态", 2, "dynamic", 0.05),
    ("2串1-动态0.1", 2, "dynamic", 0.10),
    ("3串1-单选", 3, 1, None),
    ("3串1-双选", 3, 2, None),
    ("3串1-动态", 3, "dynamic", 0.05),
    ("4串1-单选", 4, 1, None),
    ("4串1-双选", 4, 2, None),
    ("4串1-动态", 4, "dynamic", 0.05),
]

print(f"\n{'策略':<15} {'票数':>8} {'命中':>8} {'总成本':>12} {'总派彩':>12} {'盈利率':>10}")
print("-" * 75)

all_results = {}

for name, n_legs, k_picks, gap_th in strategies:
    total_cost = 0.0
    total_payout = 0.0
    n_tickets = 0
    n_hits = 0
    hit_details = []

    for day, day_packs in sorted(by_day.items()):
        result = simulate_parlay(day_packs, n_legs, k_picks, gap_th or 0.05)
        if result is None:
            continue

        total_cost += result["cost"]
        total_payout += result["payout"]
        n_tickets += 1
        if result["is_hit"]:
            n_hits += 1
            hit_details.append({
                "date": day,
                "payout": result["payout"],
                "cost": result["cost"],
                "combo": result["hit_combo"],
            })

    profit_rate = (total_payout - total_cost) / total_cost * 100 if total_cost > 0 else 0
    mark = "✅" if profit_rate > 0 else ""

    print(f"{name:<15} {n_tickets:>8} {n_hits:>8} {total_cost:>12.0f} {total_payout:>12.0f} {profit_rate:>+9.1f}% {mark}")

    all_results[name] = {
        "n_tickets": n_tickets,
        "n_hits": n_hits,
        "total_cost": total_cost,
        "total_payout": total_payout,
        "profit_rate": profit_rate,
        "hit_details": hit_details,
    }

print("-" * 75)


# ============================================================
# 命中明细
# ============================================================

print("\n" + "=" * 100)
print("命中明细（2串1-动态）")
print("=" * 100)

result = all_results.get("2串1-动态", {})
if result.get("hit_details"):
    print(f"\n共命中 {len(result['hit_details'])} 票")
    print(f"\n{'日期':<12} {'成本':>8} {'派彩':>10} {'赔率':>8}")
    print("-" * 45)
    for h in result["hit_details"][:20]:  # 只显示前20条
        combo_odds = h["payout"] / UNIT
        print(f"{h['date']:<12} {h['cost']:>8.0f} {h['payout']:>10.1f} {combo_odds:>8.1f}")


# ============================================================
# 与族模型对比
# ============================================================

print("\n" + "=" * 100)
print("与族模型基准对比")
print("=" * 100)

# 族模型2串1动态选腿回测
def run_family_model_backtest():
    """族模型回测（作为对比基准）"""
    from ticket_dynamic_k_backtest import build_model_packs

    packs = build_model_packs(cut_date=cut)

    by_day_fam = defaultdict(list)
    for p in packs:
        by_day_fam[p["date"]].append(p)

    total_cost = 0.0
    total_payout = 0.0
    n_tickets = 0
    n_hits = 0

    GAP_TH = 0.05

    for day in sorted(by_day_fam):
        day_packs = by_day_fam[day]
        if len(day_packs) < 2:
            continue

        selected = sorted(day_packs, key=lambda p: -p["gap"])[:2]
        p1, p2 = selected[0], selected[1]

        k1 = 1 if p1["gap"] > GAP_TH else 2
        k2 = 1 if p2["gap"] > GAP_TH else 2

        picks1 = [(s, p1["odds"].get(s, 999)) for s, prob in p1["model_sorted"][:k1]]
        picks2 = [(s, p2["odds"].get(s, 999)) for s, prob in p2["model_sorted"][:k2]]

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

    profit_rate = (total_payout - total_cost) / total_cost * 100 if total_cost > 0 else 0
    return {
        "n_tickets": n_tickets,
        "n_hits": n_hits,
        "total_cost": total_cost,
        "total_payout": total_payout,
        "profit_rate": profit_rate,
    }

try:
    fam_result = run_family_model_backtest()
    ms_result = all_results.get("2串1-动态", {})

    print(f"\n{'模型':<20} {'票数':>8} {'命中':>8} {'成本':>10} {'派彩':>10} {'盈利率':>10}")
    print("-" * 70)
    print(f"{'族模型(基准)':<20} {fam_result['n_tickets']:>8} {fam_result['n_hits']:>8} "
          f"{fam_result['total_cost']:>10.0f} {fam_result['total_payout']:>10.0f} "
          f"{fam_result['profit_rate']:>+9.1f}%")
    print(f"{'多场景模型':<20} {ms_result['n_tickets']:>8} {ms_result['n_hits']:>8} "
          f"{ms_result['total_cost']:>10.0f} {ms_result['total_payout']:>10.0f} "
          f"{ms_result['profit_rate']:>+9.1f}%")
except Exception as e:
    print(f"族模型对比失败: {e}")


# ============================================================
# 按月统计
# ============================================================

print("\n" + "=" * 100)
print("按月统计（2串1-动态）")
print("=" * 100)

result = all_results.get("2串1-动态", {})
monthly_stats = defaultdict(lambda: {"cost": 0, "payout": 0, "tickets": 0, "hits": 0})

# 重新按月统计
for day, day_packs in sorted(by_day.items()):
    month = day[:7]
    r = simulate_parlay(day_packs, 2, "dynamic", 0.05)
    if r is None:
        continue
    monthly_stats[month]["cost"] += r["cost"]
    monthly_stats[month]["payout"] += r["payout"]
    monthly_stats[month]["tickets"] += 1
    if r["is_hit"]:
        monthly_stats[month]["hits"] += 1

print(f"\n{'月份':<10} {'票数':>8} {'命中':>8} {'成本':>10} {'派彩':>10} {'盈利率':>10}")
print("-" * 60)

for month in sorted(monthly_stats.keys()):
    s = monthly_stats[month]
    profit = (s["payout"] - s["cost"]) / s["cost"] * 100 if s["cost"] > 0 else 0
    mark = "✅" if profit > 0 else ""
    print(f"{month:<10} {s['tickets']:>8} {s['hits']:>8} {s['cost']:>10.0f} {s['payout']:>10.0f} {profit:>+9.1f}% {mark}")
