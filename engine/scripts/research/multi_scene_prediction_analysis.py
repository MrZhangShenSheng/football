# -*- coding: utf-8 -*-
"""多场景模型 —— 比分预测结果分布分析"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path("engine/scripts/research")))

from multi_scene_model import MultiScenePredictor, TeamData, SCORE_GROUPS
import score_family_model as sfm

print("=" * 100)
print("多场景模型 —— 比分预测结果分布分析")
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

cut = "2026-01-01"

# 构建数据
blind = []
for m in hist:
    if m["date"] < cut:
        continue
    hid, aid = zh.get(m["home_zh"]), zh.get(m["away_zh"])
    if hid and aid:
        blind.append({**m, "hid": hid, "aid": aid})

merged = [("L", d, h, a, hg, ag, None) for d, h, a, hg, ag in tl]
merged += [("B", m["date"], m["hid"], m["aid"], m["actual"][0], m["actual"][1], i)
           for i, m in enumerate(blind)]
merged.sort(key=lambda r: (r[1], 0 if r[0] == "L" else 1))

stats = defaultdict(sfm.TeamStats)
predictor = MultiScenePredictor()
results = []

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

        results.append({
            "actual": m["actual"],
            "odds": m["odds"],
            "predictions": pred,
            "sorted_scores": sorted_scores,
            "top1": sorted_scores[0] if sorted_scores else None,
            "top2": sorted_scores[1] if len(sorted_scores) > 1 else None,
        })

    if kind == "L":
        stats[h].add(hg, ag, True, a)
        stats[a].add(ag, hg, False, h)

print(f"有效预测：{len(results)} 场")


# ============================================================
# 1. Top1 预测分布 vs 实际分布
# ============================================================

print("\n" + "=" * 100)
print("1. Top1 预测分布 vs 实际分布")
print("=" * 100)

top1_pred = Counter(r["top1"][0] for r in results if r["top1"])
actual_dist = Counter(r["actual"] for r in results)

# 所有出现过的比分
all_scores = set(top1_pred.keys()) | set(actual_dist.keys())
sorted_scores = sorted(all_scores, key=lambda s: (-actual_dist.get(s, 0), s))

print(f"\n{'比分':>10} {'实际出现':>10} {'实际占比':>10} {'Top1预测':>10} {'预测占比':>10} {'差异':>10}")
print("-" * 70)

for score in sorted_scores[:20]:
    actual_cnt = actual_dist.get(score, 0)
    actual_pct = actual_cnt / len(results) * 100
    pred_cnt = top1_pred.get(score, 0)
    pred_pct = pred_cnt / len(results) * 100
    diff = pred_pct - actual_pct

    mark = ""
    if diff > 5:
        mark = "过多预测"
    elif diff < -5:
        mark = "预测不足"

    print(f"{str(score):>10} {actual_cnt:>10} {actual_pct:>9.1f}% {pred_cnt:>10} {pred_pct:>9.1f}% {diff:>+9.1f}% {mark}")


# ============================================================
# 2. Top1/Top2 预测的命中情况
# ============================================================

print("\n" + "=" * 100)
print("2. 各比分的预测 vs 命中情况")
print("=" * 100)

# 每个比分被预测为Top1的次数 和 命中次数
score_pred_stats = defaultdict(lambda: {"pred_top1": 0, "pred_top2": 0, "actual": 0, "hit_top1": 0, "hit_top2": 0})

for r in results:
    actual = r["actual"]
    top1 = r["top1"][0] if r["top1"] else None
    top2 = r["top2"][0] if r["top2"] else None

    score_pred_stats[actual]["actual"] += 1

    if top1:
        score_pred_stats[top1]["pred_top1"] += 1
        if top1 == actual:
            score_pred_stats[top1]["hit_top1"] += 1

    if top2:
        score_pred_stats[top2]["pred_top2"] += 1
        if top2 == actual:
            score_pred_stats[top2]["hit_top2"] += 1

print(f"\n{'比分':>10} {'实际出现':>10} {'Top1预测':>10} {'Top1命中':>10} {'精确率':>10} {'召回率':>10}")
print("-" * 75)

for score in sorted_scores[:20]:
    s = score_pred_stats[score]
    precision = s["hit_top1"] / s["pred_top1"] * 100 if s["pred_top1"] > 0 else 0
    recall = s["hit_top1"] / s["actual"] * 100 if s["actual"] > 0 else 0

    print(f"{str(score):>10} {s['actual']:>10} {s['pred_top1']:>10} {s['hit_top1']:>10} {precision:>9.1f}% {recall:>9.1f}%")


# ============================================================
# 3. 信号强度分布
# ============================================================

print("\n" + "=" * 100)
print("3. 信号强度分布（所有比分）")
print("=" * 100)

all_signals = []
for r in results:
    for score, info in r["predictions"].items():
        all_signals.append((score, info["signal"]))

# 按信号范围统计
signal_bins = [(-1, -0.2), (-0.2, 0), (0, 0.1), (0.1, 0.2), (0.2, 0.3), (0.3, 0.5), (0.5, 1)]
print(f"\n{'信号范围':>15} {'样本数':>10} {'占比':>10}")
print("-" * 40)

for low, high in signal_bins:
    cnt = sum(1 for _, sig in all_signals if low <= sig < high)
    pct = cnt / len(all_signals) * 100
    print(f"{f'{low}~{high}':>15} {cnt:>10} {pct:>9.1f}%")


# ============================================================
# 4. Top1 信号强度与命中率的关系
# ============================================================

print("\n" + "=" * 100)
print("4. Top1 信号强度与命中率的关系")
print("=" * 100)

# 按Top1信号强度分bin
top1_by_signal = []
for r in results:
    if r["top1"]:
        sig = r["top1"][1]["signal"]
        is_hit = r["top1"][0] == r["actual"]
        top1_by_signal.append((sig, is_hit, r["actual"], r["odds"]))

signal_ranges = [(0.3, 1.0), (0.2, 0.3), (0.1, 0.2), (0.05, 0.1), (0, 0.05), (-1, 0)]

print(f"\n{'信号范围':>15} {'样本数':>10} {'命中数':>10} {'命中率':>10} {'平均赔率':>10}")
print("-" * 60)

for low, high in signal_ranges:
    bin_data = [(sig, hit, act, odds) for sig, hit, act, odds in top1_by_signal if low <= sig < high]
    if not bin_data:
        continue

    n_total = len(bin_data)
    n_hit = sum(1 for _, hit, _, _ in bin_data if hit)
    hit_rate = n_hit / n_total * 100 if n_total > 0 else 0

    # 平均赔率（Top1比分的赔率）
    avg_odds_list = []
    for sig, hit, act, odds in bin_data:
        # 找Top1比分
        for rr in results:
            if rr["top1"] and rr["top1"][1]["signal"] == sig:
                top1_score = rr["top1"][0]
                if top1_score in odds:
                    avg_odds_list.append(odds[top1_score])
                break
    avg_odds = sum(avg_odds_list) / len(avg_odds_list) if avg_odds_list else 0

    print(f"{f'{low}~{high}':>15} {n_total:>10} {n_hit:>10} {hit_rate:>9.1f}% {avg_odds:>9.1f}")


# ============================================================
# 5. 各比分组的信号分布
# ============================================================

print("\n" + "=" * 100)
print("5. 各比分组的Top1预测信号分布")
print("=" * 100)

for group_name, scores in SCORE_GROUPS.items():
    # 该组比分被预测为Top1时的信号
    group_signals = []
    for r in results:
        if r["top1"] and r["top1"][0] in scores:
            group_signals.append(r["top1"][1]["signal"])

    if not group_signals:
        print(f"\n【{group_name}】从未被预测为Top1")
        continue

    avg_sig = sum(group_signals) / len(group_signals)
    max_sig = max(group_signals)
    min_sig = min(group_signals)

    print(f"\n【{group_name}】预测{len(group_signals)}次为Top1")
    print(f"  信号均值: {avg_sig:.3f}, 最大: {max_sig:.3f}, 最小: {min_sig:.3f}")


# ============================================================
# 6. 预测正确 vs 预测错误的信号对比
# ============================================================

print("\n" + "=" * 100)
print("6. 预测正确 vs 预测错误的信号对比")
print("=" * 100)

correct_signals = [r["top1"][1]["signal"] for r in results if r["top1"] and r["top1"][0] == r["actual"]]
wrong_signals = [r["top1"][1]["signal"] for r in results if r["top1"] and r["top1"][0] != r["actual"]]

print(f"\n预测正确（{len(correct_signals)}场）：")
print(f"  信号均值: {sum(correct_signals)/len(correct_signals):.4f}")
print(f"  信号中位数: {sorted(correct_signals)[len(correct_signals)//2]:.4f}")
print(f"  信号范围: {min(correct_signals):.4f} ~ {max(correct_signals):.4f}")

print(f"\n预测错误（{len(wrong_signals)}场）：")
print(f"  信号均值: {sum(wrong_signals)/len(wrong_signals):.4f}")
print(f"  信号中位数: {sorted(wrong_signals)[len(wrong_signals)//2]:.4f}")
print(f"  信号范围: {min(wrong_signals):.4f} ~ {max(wrong_signals):.4f}")

# 分布对比
print(f"\n信号分布对比：")
print(f"{'信号范围':>15} {'正确占比':>12} {'错误占比':>12} {'差异':>10}")
print("-" * 55)

for low, high in [(0.2, 1), (0.1, 0.2), (0.05, 0.1), (0, 0.05), (-1, 0)]:
    correct_pct = sum(1 for s in correct_signals if low <= s < high) / len(correct_signals) * 100
    wrong_pct = sum(1 for s in wrong_signals if low <= s < high) / len(wrong_signals) * 100
    diff = correct_pct - wrong_pct
    print(f"{f'{low}~{high}':>15} {correct_pct:>11.1f}% {wrong_pct:>11.1f}% {diff:>+9.1f}%")


# ============================================================
# 7. 冷门比分的预测能力
# ============================================================

print("\n" + "=" * 100)
print("7. 冷门比分（高赔率）预测能力")
print("=" * 100)

# 高赔率比分：平均赔率 > 15
high_odds_scores = [(0,0), (2,2), (3,3), (3,0), (0,3), (3,1), (1,3), (3,2), (2,3), (4,0), (4,1)]

print(f"\n{'比分':>10} {'实际出现':>10} {'平均赔率':>10} {'Top5预测':>10} {'Top5命中':>10} {'Top5召回':>10}")
print("-" * 75)

for score in high_odds_scores:
    # 实际出现次数
    actual_cnt = sum(1 for r in results if r["actual"] == score)
    if actual_cnt == 0:
        continue

    # 平均赔率
    odds_list = [r["odds"].get(score, 0) for r in results if r["odds"].get(score)]
    avg_odds = sum(odds_list) / len(odds_list) if odds_list else 0

    # 被预测进Top5的次数
    pred_top5_cnt = 0
    hit_top5_cnt = 0
    for r in results:
        top5_scores = [s for s, _ in r["sorted_scores"][:5]]
        if score in top5_scores:
            pred_top5_cnt += 1
            if r["actual"] == score:
                hit_top5_cnt += 1

    recall = hit_top5_cnt / actual_cnt * 100 if actual_cnt > 0 else 0

    print(f"{str(score):>10} {actual_cnt:>10} {avg_odds:>9.1f} {pred_top5_cnt:>10} {hit_top5_cnt:>10} {recall:>9.1f}%")
