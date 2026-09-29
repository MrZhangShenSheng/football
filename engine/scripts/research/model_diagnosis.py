# -*- coding: utf-8 -*-
"""比分模型诊断分析 —— 找改进方向"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path("engine/scripts/research")))

import score_family_model as sfm
from ticket_dynamic_k_backtest import load_hist_full, build_model_packs

print("=" * 90)
print("比分模型诊断分析")
print("=" * 90)

# 1. 加载数据
packs = build_model_packs(cut_date="2026-01-01")
print(f"\n共 {len(packs)} 场盲测")

# 2. 模型预测 vs 实际命中分析
top1_hit = 0
top2_hit = 0
top3_hit = 0
topk_hit = [0] * 10

actual_dist = Counter()
pred_dist = Counter()

for p in packs:
    actual = p["actual"]
    actual_dist[actual] += 1

    ranked = p["model_sorted"]
    pred_dist[ranked[0][0]] += 1

    for k in range(min(10, len(ranked))):
        if ranked[k][0] == actual:
            topk_hit[k] += 1
            break

print("\n【模型命中率分析】")
print(f"  Top1 命中：{sum(topk_hit[:1])} / {len(packs)} = {sum(topk_hit[:1])/len(packs)*100:.1f}%")
print(f"  Top2 命中：{sum(topk_hit[:2])} / {len(packs)} = {sum(topk_hit[:2])/len(packs)*100:.1f}%")
print(f"  Top3 命中：{sum(topk_hit[:3])} / {len(packs)} = {sum(topk_hit[:3])/len(packs)*100:.1f}%")
print(f"  Top5 命中：{sum(topk_hit[:5])} / {len(packs)} = {sum(topk_hit[:5])/len(packs)*100:.1f}%")
print(f"  Top10命中：{sum(topk_hit[:10])} / {len(packs)} = {sum(topk_hit[:10])/len(packs)*100:.1f}%")

# 3. 预测概率 vs 实际频率（校准度）
print("\n【模型校准度分析】")
# 按预测概率分桶
prob_buckets = defaultdict(lambda: {"n": 0, "hit": 0})
for p in packs:
    for score, prob in p["model_sorted"][:10]:
        bucket = int(prob * 100) // 2 * 2  # 2% 一桶
        prob_buckets[bucket]["n"] += 1
        if score == p["actual"]:
            prob_buckets[bucket]["hit"] += 1

print(f"  {'预测概率':>10} {'样本数':>10} {'命中数':>10} {'实际命中率':>12} {'偏差':>10}")
print("-" * 60)
for bucket in sorted(prob_buckets.keys(), reverse=True)[:10]:
    b = prob_buckets[bucket]
    if b["n"] >= 10:
        actual_rate = b["hit"] / b["n"] * 100
        expected = bucket + 1  # 桶中心
        bias = actual_rate - expected
        mark = "⚠️" if abs(bias) > 3 else ""
        print(f"  {bucket:>8}-{bucket+2}% {b['n']:>10} {b['hit']:>10} {actual_rate:>11.1f}% {bias:>+9.1f}% {mark}")

# 4. 哪些比分被低估/高估
print("\n【比分预测偏差】")
print("  实际频率 vs 模型预测频率（top1）")

actual_freq = {s: c/len(packs)*100 for s, c in actual_dist.most_common(15)}
pred_freq = {s: c/len(packs)*100 for s, c in pred_dist.most_common(15)}

all_scores = set(actual_freq.keys()) | set(pred_freq.keys())
print(f"  {'比分':>10} {'实际频率':>10} {'预测频率':>10} {'偏差':>10}")
print("-" * 50)
for s in sorted(all_scores, key=lambda x: actual_freq.get(x, 0), reverse=True)[:12]:
    af = actual_freq.get(s, 0)
    pf = pred_freq.get(s, 0)
    bias = pf - af
    mark = "⚠️高估" if bias > 3 else ("⚠️低估" if bias < -3 else "")
    print(f"  {str(s):>10} {af:>9.1f}% {pf:>9.1f}% {bias:>+9.1f}% {mark}")

# 5. Gap 与命中率的关系
print("\n【Gap 与命中率】")
gap_buckets = defaultdict(lambda: {"n": 0, "hit": 0})
for p in packs:
    gap = p["gap"]
    bucket = int(gap * 100) // 2 * 2  # 2% 一桶
    gap_buckets[bucket]["n"] += 1
    if p["model_sorted"][0][0] == p["actual"] or p["model_sorted"][1][0] == p["actual"]:
        gap_buckets[bucket]["hit"] += 1

print(f"  {'Gap范围':>10} {'样本数':>10} {'Top2命中':>10} {'命中率':>10}")
print("-" * 50)
for bucket in sorted(gap_buckets.keys(), reverse=True)[:8]:
    b = gap_buckets[bucket]
    if b["n"] >= 5:
        rate = b["hit"] / b["n"] * 100
        print(f"  {bucket:>8}-{bucket+2}% {b['n']:>10} {b['hit']:>10} {rate:>9.1f}%")

# 6. 族预测准确率
print("\n【族预测准确率】")
# 需要重新跑模型拿族概率
zh = {}
for tid, srcs in sfm.load_aliases().items():
    if srcs.get("zh"):
        zh[srcs["zh"]] = tid

tl = sfm.league_timeline()
hist = load_hist_full()

cut = "2026-01-01"
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
X_tr, y_tr, X_bl, meta = [], [], [], []
tot_g = tot_n = 0

for r in merged:
    kind, date, h, a, hg, ag = r[0], r[1], r[2], r[3], r[4], r[5]
    lg_gf = (tot_g / tot_n) if tot_n >= 50 else 2.6
    fv_h = stats[h].vector(0, lg_gf)
    fv_a = stats[a].vector(1, lg_gf)

    if kind == "L" and date < cut and fv_h[12] >= sfm.MIN_HIST and fv_a[12] >= sfm.MIN_HIST:
        X_tr.append(sfm.feature_row((fv_h, fv_a)))
        y_tr.append(sfm.CLASSES.index(sfm.family_of(hg, ag)))
    elif kind == "B":
        meta.append(blind[r[6]])
        X_bl.append(sfm.feature_row((fv_h, fv_a)))

    stats[h].add(hg, ag, True)
    stats[a].add(ag, hg, False)
    tot_g += hg + ag
    tot_n += 1

model = sfm.train_softmax(X_tr, y_tr, len(sfm.CLASSES))
P = sfm.predict_proba(model, X_bl)

# 族命中统计
fam_hits = defaultdict(lambda: {"n": 0, "hit": 0})
for i, m in enumerate(meta):
    actual_fam = sfm.family_of(m["actual"][0], m["actual"][1])
    pred_fam = sfm.CLASSES[P[i].argmax()]

    fam_hits[actual_fam]["n"] += 1
    if pred_fam == actual_fam:
        fam_hits[actual_fam]["hit"] += 1

print(f"  {'族':>15} {'样本数':>10} {'命中数':>10} {'命中率':>10}")
print("-" * 50)
total_fam_hit = 0
for fam in sfm.CLASSES:
    b = fam_hits[fam]
    if b["n"] > 0:
        rate = b["hit"] / b["n"] * 100
        total_fam_hit += b["hit"]
        print(f"  {fam:>15} {b['n']:>10} {b['hit']:>10} {rate:>9.1f}%")

print(f"  {'总计':>15} {len(meta):>10} {total_fam_hit:>10} {total_fam_hit/len(meta)*100:>9.1f}%")
