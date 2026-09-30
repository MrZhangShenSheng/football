# -*- coding: utf-8 -*-
"""多场景模型回测验证"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path("engine/scripts/research")))

from multi_scene_model import (
    MultiScenePredictor, TeamData, SCORE_GROUPS
)
import score_family_model as sfm

UNIT = 2.0

print("=" * 100)
print("多场景博弈模型 —— 回测验证")
print("=" * 100)


def team_stats_to_team_data(ts, side: int) -> TeamData:
    """将 TeamStats 转换为 TeamData"""
    td = TeamData(
        n=ts.n,
        gf=ts.gf,
        ga=ts.ga,
        win=ts.win,
        gd=ts.gd,
        cs=ts.cs,
        becs=ts.becs,
        btts=ts.btts,
        over25=ts.over25,
        gf_home=ts.gf_side[0][0],
        gf_home_n=ts.gf_side[0][1],
        gf_away=ts.gf_side[1][0],
        gf_away_n=ts.gf_side[1][1],
        ga_home=ts.ga_side[0][0],
        ga_home_n=ts.ga_side[0][1],
        ga_away=ts.ga_side[1][0],
        ga_away_n=ts.ga_side[1][1],
        recent_gd=ts.recent_gd.copy(),
        recent=ts.recent.copy(),
    )
    return td


def load_hist_full():
    """加载历史数据（去重版）"""
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
                "home_zh": m.get("home"),
                "away_zh": m.get("away"),
                "actual": (h, a),
                "odds": odds,
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

cut = "2026-01-01"

print(f"历史比赛：{len(hist)} 场")
print(f"联赛数据：{len(tl)} 场")

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

# 滚动统计
stats = defaultdict(sfm.TeamStats)
predictor = MultiScenePredictor()

results = []
tot_g = tot_n = 0

for r in merged:
    kind, date, h, a, hg, ag = r[0], r[1], r[2], r[3], r[4], r[5]

    if kind == "B":
        idx = r[6]
        m = blind[idx]

        # 检查历史数据量
        if stats[h].n < sfm.MIN_HIST or stats[a].n < sfm.MIN_HIST:
            continue

        # 转换为 TeamData
        home_data = team_stats_to_team_data(stats[h], 0)
        away_data = team_stats_to_team_data(stats[a], 1)

        # 预测
        prediction = predictor.predict(home_data, away_data)

        results.append({
            "date": m["date"],
            "actual": m["actual"],
            "odds": m["odds"],
            "prediction": prediction,
        })

    # 更新统计（联赛数据才更新）
    if kind == "L":
        stats[h].add(hg, ag, True, a)
        stats[a].add(ag, hg, False, h)
        tot_g += hg + ag
        tot_n += 1

print(f"有效预测：{len(results)} 场")


# ============================================================
# 分析预测效果
# ============================================================

print("\n" + "=" * 100)
print("1. 比分预测效果分析")
print("=" * 100)

# Top-K 命中率
for k in [1, 2, 3, 5]:
    hits = 0
    for r in results:
        # 按信号排序取 Top-K
        pred = r["prediction"]["predictions"]
        sorted_scores = sorted(pred.items(), key=lambda x: -x[1]["signal"])
        top_k_scores = [s for s, _ in sorted_scores[:k]]
        if r["actual"] in top_k_scores:
            hits += 1
    print(f"  Top{k} 命中率：{hits}/{len(results)} = {hits/len(results)*100:.1f}%")


# 按比分组分析
print("\n" + "=" * 100)
print("2. 按比分组分析信号强度与命中率")
print("=" * 100)

# 收集高信号场次的表现
signal_bins = [(0.6, 1.0, "高信号(>0.6)"), (0.4, 0.6, "中信号(0.4-0.6)"), (0.0, 0.4, "低信号(<0.4)")]

for group_name, scores in SCORE_GROUPS.items():
    print(f"\n【{group_name}】比分: {scores}")

    group_results = []
    for r in results:
        # 找该组在预测中的最高信号
        max_signal = 0
        for s in scores:
            if s in r["prediction"]["predictions"]:
                sig = r["prediction"]["predictions"][s]["signal"]
                max_signal = max(max_signal, sig)

        # 实际是否命中该组
        actual_in_group = r["actual"] in scores
        group_results.append((max_signal, actual_in_group, r["actual"], r["odds"]))

    # 按信号分bin统计
    for low, high, label in signal_bins:
        bin_results = [(sig, hit, act, odds) for sig, hit, act, odds in group_results if low <= sig < high]
        if not bin_results:
            continue

        n_total = len(bin_results)
        n_hit = sum(1 for _, hit, _, _ in bin_results if hit)
        hit_rate = n_hit / n_total * 100 if n_total > 0 else 0

        # 计算ROI（如果下注该组所有比分）
        total_cost = 0
        total_payout = 0
        for sig, hit, actual, odds in bin_results:
            # 假设下注该组所有比分各1注
            bet_cost = len(scores) * UNIT
            total_cost += bet_cost
            if hit:
                total_payout += UNIT * odds.get(actual, 0)

        roi = (total_payout - total_cost) / total_cost * 100 if total_cost > 0 else 0

        print(f"  {label}: {n_total}场, 命中{n_hit}({hit_rate:.1f}%), ROI={roi:+.1f}%")


# ============================================================
# 总进球数预测分析
# ============================================================

print("\n" + "=" * 100)
print("3. 总进球数预测分析")
print("=" * 100)

total_goal_ranges = [
    ("0-1球", lambda a: a[0] + a[1] <= 1),
    ("2-3球", lambda a: 2 <= a[0] + a[1] <= 3),
    ("4+球", lambda a: a[0] + a[1] >= 4),
]

for range_name, check_fn in total_goal_ranges:
    print(f"\n【{range_name}】")

    for low, high, label in signal_bins:
        # 找预测该区间信号在 [low, high) 的场次
        matches = []
        for r in results:
            tg_pred = r["prediction"]["total_goals"]

            # 找对应的信号
            if range_name == "0-1球":
                sig = tg_pred.get("0-1", {}).get("signal", 0)
            elif range_name == "2-3球":
                sig = tg_pred.get("2-3", {}).get("signal", 0)
            else:
                sig = tg_pred.get("4+", {}).get("signal", 0)

            if low <= sig < high:
                actual_in_range = check_fn(r["actual"])
                matches.append((sig, actual_in_range, r["actual"]))

        if not matches:
            continue

        n_total = len(matches)
        n_hit = sum(1 for _, hit, _ in matches if hit)
        hit_rate = n_hit / n_total * 100 if n_total > 0 else 0

        print(f"  {label}: {n_total}场, 命中{n_hit}({hit_rate:.1f}%)")


# ============================================================
# 0:0 专项分析
# ============================================================

print("\n" + "=" * 100)
print("4. 0:0 预测专项分析")
print("=" * 100)

zero_zero_results = []
for r in results:
    sig = r["prediction"]["predictions"].get((0, 0), {}).get("signal", 0)
    is_zero_zero = r["actual"] == (0, 0)
    odds = r["odds"].get((0, 0), 0)
    zero_zero_results.append((sig, is_zero_zero, odds))

# 按信号分段
print(f"\n0:0 实际出现：{sum(1 for _, hit, _ in zero_zero_results if hit)}/{len(zero_zero_results)} = {sum(1 for _, hit, _ in zero_zero_results if hit)/len(zero_zero_results)*100:.1f}%")

for low, high, label in [(0.7, 1.0, ">0.7"), (0.5, 0.7, "0.5-0.7"), (0.3, 0.5, "0.3-0.5"), (0.0, 0.3, "<0.3")]:
    bin_results = [(sig, hit, odds) for sig, hit, odds in zero_zero_results if low <= sig < high]
    if not bin_results:
        print(f"  {label}: 0场")
        continue

    n_total = len(bin_results)
    n_hit = sum(1 for _, hit, _ in bin_results if hit)
    hit_rate = n_hit / n_total * 100 if n_total > 0 else 0

    # ROI
    total_cost = n_total * UNIT
    total_payout = sum(UNIT * odds for _, hit, odds in bin_results if hit)
    roi = (total_payout - total_cost) / total_cost * 100 if total_cost > 0 else 0

    avg_odds = sum(odds for _, _, odds in bin_results) / n_total if n_total > 0 else 0

    print(f"  {label}: {n_total}场, 命中{n_hit}({hit_rate:.1f}%), 平均赔率{avg_odds:.1f}, ROI={roi:+.1f}%")


print("\n" + "=" * 100)
print("5. 场景判断分布")
print("=" * 100)

# 统计各场景的判断分布
scene_judgments = defaultdict(Counter)
for r in results:
    for scene_id, detail in r["prediction"]["scene_details"].items():
        scene_judgments[scene_id][detail["judgment"]] += 1

for scene_id in sorted(scene_judgments.keys()):
    print(f"\n【{scene_id}】")
    for judgment, count in scene_judgments[scene_id].most_common():
        pct = count / len(results) * 100
        print(f"  {judgment}: {count} ({pct:.1f}%)")
