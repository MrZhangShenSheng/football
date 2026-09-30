# -*- coding: utf-8 -*-
"""高赔率市场分析 —— 寻找alpha来源"""
import json
import sys
import numpy as np
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path("engine/scripts/research")))

import score_family_model as sfm
from ticket_dynamic_k_backtest import load_hist_full

print("=" * 100)
print("高赔率市场分析 —— 寻找alpha来源")
print("=" * 100)

# 加载数据
hist = load_hist_full()
print(f"历史数据：{len(hist)} 场")


# ========================================
# 1. 比分赔率分布分析
# ========================================

print("\n" + "=" * 100)
print("1. 比分赔率分布 —— 哪些比分是高赔率？")
print("=" * 100)

score_stats = defaultdict(lambda: {"count": 0, "odds_sum": 0, "odds_list": []})

for m in hist:
    actual = m["actual"]
    odds = m["odds"].get(actual)
    if odds:
        score_stats[actual]["count"] += 1
        score_stats[actual]["odds_sum"] += odds
        score_stats[actual]["odds_list"].append(odds)

print(f"\n{'比分':>10} {'出现次数':>10} {'平均赔率':>10} {'赔率范围':>15} {'类型':>10}")
print("-" * 65)

# 按出现次数排序
for score, stats in sorted(score_stats.items(), key=lambda x: -x[1]["count"])[:20]:
    avg_odds = stats["odds_sum"] / stats["count"]
    min_odds = min(stats["odds_list"])
    max_odds = max(stats["odds_list"])

    if avg_odds < 10:
        odds_type = "低赔"
    elif avg_odds < 20:
        odds_type = "中赔"
    else:
        odds_type = "高赔"

    print(f"{str(score):>10} {stats['count']:>10} {avg_odds:>10.1f} {min_odds:>6.1f}-{max_odds:>6.1f} {odds_type:>10}")


# ========================================
# 2. 冷门比分分析（高赔率比分）
# ========================================

print("\n" + "=" * 100)
print("2. 冷门比分分析 —— 高赔率比分的特征")
print("=" * 100)

# 高赔率场次（赔率>20）
high_odds_matches = []
for m in hist:
    actual = m["actual"]
    odds = m["odds"].get(actual)
    if odds and odds > 20:
        high_odds_matches.append({
            "score": actual,
            "odds": odds,
            "date": m["date"],
        })

print(f"\n高赔率场次（赔率>20）：{len(high_odds_matches)} 场 ({len(high_odds_matches)/len(hist)*100:.1f}%)")

# 高赔率比分分布
high_odds_scores = Counter(m["score"] for m in high_odds_matches)
print(f"\n高赔率比分 Top10：")
for score, cnt in high_odds_scores.most_common(10):
    avg_odds = np.mean([m["odds"] for m in high_odds_matches if m["score"] == score])
    print(f"  {str(score):>10}: {cnt:>4} 次, 平均赔率 {avg_odds:.1f}")


# ========================================
# 3. 半场比分分析（如果有数据）
# ========================================

print("\n" + "=" * 100)
print("3. 半场/全场组合分析")
print("=" * 100)

# 检查是否有半场数据
sample = hist[0]
print(f"样本字段：{list(sample.keys())}")

# 分析全场比分的上/下半场特征
# 假设：总进球数与半场分布有关

# 按总进球分组
by_total_goals = defaultdict(list)
for m in hist:
    total = m["actual"][0] + m["actual"][1]
    by_total_goals[total].append(m)

print(f"\n按总进球数分组：")
for total in sorted(by_total_goals.keys())[:8]:
    matches = by_total_goals[total]
    avg_odds = np.mean([m["odds"].get(m["actual"], 0) for m in matches if m["odds"].get(m["actual"])])
    print(f"  {total}球：{len(matches)} 场, 平均赔率 {avg_odds:.1f}")


# ========================================
# 4. 特定比分模式的预测难度
# ========================================

print("\n" + "=" * 100)
print("4. 特定比分模式的预测难度分析")
print("=" * 100)

# 加载模型预测
zh = {}
for tid, srcs in sfm.load_aliases().items():
    if srcs.get("zh"):
        zh[srcs["zh"]] = tid

tl = sfm.league_timeline()
cut = "2026-01-01"

# 构建预测
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
train_data = []
blind_data = []
tot_g = tot_n = 0

for r in merged:
    kind, date, h, a, hg, ag = r[0], r[1], r[2], r[3], r[4], r[5]
    lg_gf = (tot_g / tot_n) if tot_n >= 50 else 2.6
    fv_h = stats[h].vector(0, lg_gf)
    fv_a = stats[a].vector(1, lg_gf)

    if kind == "L" and date < cut and fv_h[12] >= sfm.MIN_HIST and fv_a[12] >= sfm.MIN_HIST:
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
        })

    if kind == "L":
        stats[h].add(hg, ag, True)
        stats[a].add(ag, hg, False)
        tot_g += hg + ag
        tot_n += 1

# 训练模型
X_tr = np.array([d["X"] for d in train_data])
y_tr = np.array([sfm.CLASSES.index(d["family"]) for d in train_data])
X_bl = np.array([b["X"] for b in blind_data])

model = sfm.train_softmax(X_tr, y_tr, len(sfm.CLASSES))
P = sfm.predict_proba(model, X_bl)

fam_dist = defaultdict(Counter)
for d in train_data:
    fam_dist[d["family"]][d["score"]] += 1

# 分析每个比分的预测排名
print(f"\n各比分在模型中的预测排名（盲测 {len(blind_data)} 场）：")

score_rank_stats = defaultdict(lambda: {"ranks": [], "odds": []})

for i, b in enumerate(blind_data):
    # 计算模型概率
    model_probs = {}
    for ci, cname in enumerate(sfm.CLASSES):
        tot = sum(fam_dist.get(cname, {}).values()) or 1
        for s, c in fam_dist.get(cname, {}).items():
            model_probs[s] = model_probs.get(s, 0.0) + P[i][ci] * (c / tot)

    mdl_sorted = sorted(model_probs.items(), key=lambda kv: -kv[1])

    # 找实际比分的排名
    actual = b["actual"]
    for rank, (s, prob) in enumerate(mdl_sorted, 1):
        if s == actual:
            odds = b["odds"].get(actual, 0)
            score_rank_stats[actual]["ranks"].append(rank)
            score_rank_stats[actual]["odds"].append(odds)
            break

print(f"\n{'比分':>10} {'样本数':>8} {'平均排名':>10} {'Top3占比':>10} {'Top5占比':>10} {'平均赔率':>10}")
print("-" * 70)

for score in [(1,1), (1,0), (0,1), (2,1), (1,2), (0,0), (2,0), (2,2), (3,1), (3,0), (0,2), (3,2)]:
    if score in score_rank_stats:
        ranks = score_rank_stats[score]["ranks"]
        odds = score_rank_stats[score]["odds"]
        avg_rank = np.mean(ranks)
        top3_pct = sum(1 for r in ranks if r <= 3) / len(ranks) * 100
        top5_pct = sum(1 for r in ranks if r <= 5) / len(ranks) * 100
        avg_odds = np.mean([o for o in odds if o > 0])
        print(f"{str(score):>10} {len(ranks):>8} {avg_rank:>10.1f} {top3_pct:>9.1f}% {top5_pct:>9.1f}% {avg_odds:>10.1f}")


# ========================================
# 5. 高赔率事件的模型预测能力
# ========================================

print("\n" + "=" * 100)
print("5. 高赔率事件的模型预测能力")
print("=" * 100)

# 分析：当模型预测某冷门比分排名较高时，实际命中率如何？

# 统计每个比分被预测为 top5 时的命中率
top5_pred_stats = defaultdict(lambda: {"pred": 0, "hit": 0})

for i, b in enumerate(blind_data):
    model_probs = {}
    for ci, cname in enumerate(sfm.CLASSES):
        tot = sum(fam_dist.get(cname, {}).values()) or 1
        for s, c in fam_dist.get(cname, {}).items():
            model_probs[s] = model_probs.get(s, 0.0) + P[i][ci] * (c / tot)

    mdl_sorted = sorted(model_probs.items(), key=lambda kv: -kv[1])[:5]

    for s, prob in mdl_sorted:
        top5_pred_stats[s]["pred"] += 1
        if s == b["actual"]:
            top5_pred_stats[s]["hit"] += 1

print(f"\n模型 Top5 预测中各比分的命中情况：")
print(f"{'比分':>10} {'预测次数':>10} {'命中次数':>10} {'命中率':>10} {'平均赔率':>10}")
print("-" * 55)

for score, stats in sorted(top5_pred_stats.items(), key=lambda x: -x[1]["pred"])[:15]:
    hit_rate = stats["hit"] / stats["pred"] * 100 if stats["pred"] > 0 else 0
    avg_odds = np.mean(score_rank_stats[score]["odds"]) if score in score_rank_stats else 0
    print(f"{str(score):>10} {stats['pred']:>10} {stats['hit']:>10} {hit_rate:>9.1f}% {avg_odds:>10.1f}")


# ========================================
# 6. 总结：哪些高赔率市场有机会？
# ========================================

print("\n" + "=" * 100)
print("6. 总结：哪些高赔率市场可能有机会？")
print("=" * 100)

print("""
【当前数据限制】
- 只有全场比分和赔率
- 没有半场比分、进球时间、角球等数据

【潜在高赔率市场】
1. 冷门比分（0:0, 2:2, 3:1 等）—— 赔率高但预测难
2. 大比分（4:0, 3:2 等）—— 需要识别"进攻强+防守弱"配对
3. 半场胜平负 —— 需要额外数据源

【关键问题】
- 冷门比分出现时，模型能否提前"感知"？
- 当前模型对冷门比分的预测排名普遍 >5，无法直接使用
- 需要针对特定高赔率事件建立专门模型
""")
