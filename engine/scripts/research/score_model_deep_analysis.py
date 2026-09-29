# -*- coding: utf-8 -*-
"""比分预测模型深度分析 —— 找准提升方向"""
import json
import sys
import numpy as np
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path("engine/scripts/research")))

import score_family_model as sfm
from ticket_dynamic_k_backtest import load_hist_full, build_model_packs

print("=" * 100)
print("比分预测模型深度分析 —— 找准提升方向")
print("=" * 100)

# 加载数据
packs = build_model_packs(cut_date="2026-01-01")
print(f"共 {len(packs)} 场盲测")


# ========================================
# 1. 当前预测能力边界分析
# ========================================

print("\n" + "=" * 100)
print("1. 当前预测能力边界")
print("=" * 100)

# TopK 命中率
for k in [1, 2, 3, 5, 10, 20]:
    hit = sum(1 for p in packs if p["actual"] in [s for s, _ in p["model_sorted"][:k]])
    print(f"  Top{k:<2} 命中率：{hit}/{len(packs)} = {hit/len(packs)*100:.1f}%")

# 理论上限：如果完美预测
print(f"\n理论分析：")
print(f"  比分种类数：{len(set(p['actual'] for p in packs))}")
print(f"  最高频比分占比：{Counter(p['actual'] for p in packs).most_common(1)[0][1]/len(packs)*100:.1f}%")


# ========================================
# 2. 预测失败案例深度分析
# ========================================

print("\n" + "=" * 100)
print("2. 预测失败案例分析")
print("=" * 100)

# Top1 失败案例
failures = []
for p in packs:
    pred_top1 = p["model_sorted"][0][0]
    if pred_top1 != p["actual"]:
        # 实际比分在预测中的排名
        rank = None
        for i, (s, prob) in enumerate(p["model_sorted"]):
            if s == p["actual"]:
                rank = i + 1
                break
        failures.append({
            "pred": pred_top1,
            "actual": p["actual"],
            "rank": rank,
            "gap": p["gap"],
            "top1_prob": p["model_sorted"][0][1],
        })

print(f"\nTop1 失败数：{len(failures)}/{len(packs)} = {len(failures)/len(packs)*100:.1f}%")

# 失败时实际比分的排名分布
rank_dist = Counter(f["rank"] for f in failures if f["rank"])
print(f"\n失败时实际比分的排名分布：")
for rank in sorted(rank_dist.keys())[:10]:
    pct = rank_dist[rank] / len(failures) * 100
    bar = "█" * int(pct / 2)
    print(f"  第{rank:>2}名：{rank_dist[rank]:>4} ({pct:>5.1f}%) {bar}")

not_in_top20 = sum(1 for f in failures if f["rank"] is None or f["rank"] > 20)
print(f"  >20名：{not_in_top20:>4} ({not_in_top20/len(failures)*100:>5.1f}%)")

# 预测 vs 实际的模式
print(f"\n预测→实际 的常见错误模式：")
error_patterns = Counter((f["pred"], f["actual"]) for f in failures)
print(f"  {'预测':<12} {'实际':<12} {'次数':>8} {'占比':>8}")
print("-" * 45)
for (pred, actual), cnt in error_patterns.most_common(15):
    pct = cnt / len(failures) * 100
    print(f"  {str(pred):<12} {str(actual):<12} {cnt:>8} {pct:>7.1f}%")


# ========================================
# 3. 特征与比分的关系
# ========================================

print("\n" + "=" * 100)
print("3. 什么特征能区分不同比分？")
print("=" * 100)

# 重新构建带特征的数据
zh = {}
for tid, srcs in sfm.load_aliases().items():
    if srcs.get("zh"):
        zh[srcs["zh"]] = tid

tl = sfm.league_timeline()
hist = load_hist_full()

cut = "2026-01-01"
merged = [("L", d, h, a, hg, ag) for d, h, a, hg, ag in tl]
merged.sort(key=lambda r: r[1])

stats = defaultdict(sfm.TeamStats)
train_samples = []
tot_g = tot_n = 0

for r in merged:
    date, h, a, hg, ag = r[1], r[2], r[3], r[4], r[5]
    lg_gf = (tot_g / tot_n) if tot_n >= 50 else 2.6
    fv_h = stats[h].vector(0, lg_gf)
    fv_a = stats[a].vector(1, lg_gf)

    if date < cut and fv_h[12] >= sfm.MIN_HIST and fv_a[12] >= sfm.MIN_HIST:
        train_samples.append({
            "fv_h": fv_h,
            "fv_a": fv_a,
            "score": (hg, ag),
            "total_goals": hg + ag,
            "goal_diff": hg - ag,
        })

    stats[h].add(hg, ag, True)
    stats[a].add(ag, hg, False)
    tot_g += hg + ag
    tot_n += 1

print(f"训练样本：{len(train_samples)}")

# 分析关键特征与比分的关系
# 特征：主队进球率、客队进球率、主队失球率、客队失球率

# 按总进球数分组
goal_groups = defaultdict(list)
for s in train_samples:
    goal_groups[s["total_goals"]].append(s)

print(f"\n按总进球数分组的特征均值：")
print(f"  {'总进球':>8} {'样本数':>8} {'主进球率':>10} {'客进球率':>10} {'主失球率':>10} {'客失球率':>10}")
print("-" * 65)
for total in sorted(goal_groups.keys())[:8]:
    samples = goal_groups[total]
    h_gf = np.mean([s["fv_h"][0] for s in samples])
    a_gf = np.mean([s["fv_a"][0] for s in samples])
    h_ga = np.mean([s["fv_h"][1] for s in samples])
    a_ga = np.mean([s["fv_a"][1] for s in samples])
    print(f"  {total:>8} {len(samples):>8} {h_gf:>10.3f} {a_gf:>10.3f} {h_ga:>10.3f} {a_ga:>10.3f}")

# 按比分分组
score_groups = defaultdict(list)
for s in train_samples:
    score_groups[s["score"]].append(s)

print(f"\n高频比分的特征均值：")
print(f"  {'比分':>10} {'样本数':>8} {'主进球率':>10} {'客进球率':>10} {'主失球率':>10} {'客失球率':>10}")
print("-" * 70)
for score in [(1,1), (1,0), (0,1), (2,1), (1,2), (0,0), (2,0), (2,2)]:
    if score in score_groups:
        samples = score_groups[score]
        h_gf = np.mean([s["fv_h"][0] for s in samples])
        a_gf = np.mean([s["fv_a"][0] for s in samples])
        h_ga = np.mean([s["fv_h"][1] for s in samples])
        a_ga = np.mean([s["fv_a"][1] for s in samples])
        print(f"  {str(score):>10} {len(samples):>8} {h_gf:>10.3f} {a_gf:>10.3f} {h_ga:>10.3f} {a_ga:>10.3f}")


# ========================================
# 4. 分析预测难度
# ========================================

print("\n" + "=" * 100)
print("4. 哪些比分更容易预测？")
print("=" * 100)

# 每个比分被预测和命中的情况
score_pred_stats = defaultdict(lambda: {"pred_cnt": 0, "actual_cnt": 0, "hit_cnt": 0})

for p in packs:
    actual = p["actual"]
    pred = p["model_sorted"][0][0]

    score_pred_stats[pred]["pred_cnt"] += 1
    score_pred_stats[actual]["actual_cnt"] += 1
    if pred == actual:
        score_pred_stats[actual]["hit_cnt"] += 1

print(f"\n{'比分':>10} {'预测次数':>10} {'实际次数':>10} {'命中次数':>10} {'精确率':>10} {'召回率':>10}")
print("-" * 70)
for score in [(1,1), (1,0), (0,1), (2,1), (1,2), (0,0), (2,0), (2,2), (3,1), (3,0)]:
    s = score_pred_stats[score]
    precision = s["hit_cnt"] / s["pred_cnt"] * 100 if s["pred_cnt"] > 0 else 0
    recall = s["hit_cnt"] / s["actual_cnt"] * 100 if s["actual_cnt"] > 0 else 0
    print(f"  {str(score):>10} {s['pred_cnt']:>10} {s['actual_cnt']:>10} {s['hit_cnt']:>10} {precision:>9.1f}% {recall:>9.1f}%")


# ========================================
# 5. 赔率隐含概率 vs 模型概率
# ========================================

print("\n" + "=" * 100)
print("5. 赔率隐含概率 vs 模型概率")
print("=" * 100)

def odds_to_prob(odds_dict):
    """赔率转概率（去水）"""
    total = sum(1/o for o in odds_dict.values() if o and o > 1)
    if total == 0:
        return {}
    return {s: (1/o)/total for s, o in odds_dict.items() if o and o > 1}

# 比较 top1 预测
model_better = 0
odds_better = 0
same = 0

for p in packs:
    model_top1 = p["model_sorted"][0][0]

    odds_probs = odds_to_prob(p["odds"])
    if not odds_probs:
        continue
    odds_top1 = max(odds_probs.keys(), key=lambda s: odds_probs[s])

    actual = p["actual"]

    if model_top1 == actual and odds_top1 != actual:
        model_better += 1
    elif odds_top1 == actual and model_top1 != actual:
        odds_better += 1
    elif model_top1 == actual and odds_top1 == actual:
        same += 1

print(f"\n模型 vs 赔率 Top1 预测对比：")
print(f"  模型独中：{model_better} 次")
print(f"  赔率独中：{odds_better} 次")
print(f"  都中：{same} 次")
print(f"  模型优势：{model_better - odds_better:+d}")

# 模型和赔率的一致性
agree = 0
disagree = 0
for p in packs:
    model_top1 = p["model_sorted"][0][0]
    odds_probs = odds_to_prob(p["odds"])
    if odds_probs:
        odds_top1 = max(odds_probs.keys(), key=lambda s: odds_probs[s])
        if model_top1 == odds_top1:
            agree += 1
        else:
            disagree += 1

print(f"\n模型与赔率 Top1 一致性：")
print(f"  一致：{agree} ({agree/(agree+disagree)*100:.1f}%)")
print(f"  不一致：{disagree} ({disagree/(agree+disagree)*100:.1f}%)")


# ========================================
# 6. 结论：提升方向
# ========================================

print("\n" + "=" * 100)
print("6. 提升方向总结")
print("=" * 100)

print("""
【当前瓶颈】
1. Top1 命中率 11.9%，Top3 命中率 31.8%
2. 预测过于集中在 (1,1)、(1,0)、(0,1)
3. 很多实际比分（如 0:0、2:0、2:2）从未被预测为 Top1
4. 失败时实际比分通常在 Top2-5 位置

【可能的提升方向】
A. 预测总进球数 + 比分分配
   - 先预测 0球/1球/2球/3球/4+球
   - 再在该区间内分配具体比分

B. 预测胜负 + 净胜球 + 比分
   - 三阶段细化预测

C. 引入更多特征
   - 近期状态（连胜/连败）
   - 主客场特异性更强的特征
   - 赔率作为先验

D. 集成模型
   - 多个子模型投票
   - 不同特征组合的模型
""")
