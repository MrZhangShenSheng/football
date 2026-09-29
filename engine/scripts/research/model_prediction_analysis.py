# -*- coding: utf-8 -*-
"""模型预测比分分析"""
import sys
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path("engine/scripts/research")))
from ticket_dynamic_k_backtest import build_model_packs

packs = build_model_packs(cut_date="2026-01-01")

print("=" * 70)
print("模型预测比分分析")
print("=" * 70)

# Top1 预测分布
pred_top1 = Counter()
pred_top2 = Counter()
actual_dist = Counter()

for p in packs:
    pred_top1[p["model_sorted"][0][0]] += 1
    pred_top2[p["model_sorted"][1][0]] += 1
    actual_dist[p["actual"]] += 1

print(f"\n【模型 Top1 预测分布】（共 {len(packs)} 场）")
print(f"{'比分':>10} {'预测次数':>10} {'占比':>10} {'实际出现':>10} {'实际占比':>10}")
print("-" * 60)
for score, cnt in pred_top1.most_common(15):
    pct = cnt / len(packs) * 100
    actual_cnt = actual_dist.get(score, 0)
    actual_pct = actual_cnt / len(packs) * 100
    diff = pct - actual_pct
    mark = "⚠️高估" if diff > 10 else ("低估" if diff < -3 else "")
    print(f"{str(score):>10} {cnt:>10} {pct:>9.1f}% {actual_cnt:>10} {actual_pct:>9.1f}% {mark}")

print(f"\n【模型 Top2 预测分布】")
print(f"{'比分':>10} {'预测次数':>10} {'占比':>10}")
print("-" * 35)
for score, cnt in pred_top2.most_common(10):
    pct = cnt / len(packs) * 100
    print(f"{str(score):>10} {cnt:>10} {pct:>9.1f}%")

print(f"\n【实际比分分布 Top15】")
print(f"{'比分':>10} {'实际次数':>10} {'占比':>10} {'模型Top1预测':>12}")
print("-" * 50)
for score, cnt in actual_dist.most_common(15):
    pct = cnt / len(packs) * 100
    pred_cnt = pred_top1.get(score, 0)
    print(f"{str(score):>10} {cnt:>10} {pct:>9.1f}% {pred_cnt:>12}")

# 预测集中度
print(f"\n【预测集中度】")
top3_pred = sum(c for s, c in pred_top1.most_common(3))
print(f"  Top1 集中在前3种比分：{top3_pred}/{len(packs)} = {top3_pred/len(packs)*100:.1f}%")

unique_pred = len(pred_top1)
unique_actual = len(actual_dist)
print(f"  Top1 预测种类数：{unique_pred}")
print(f"  实际比分种类数：{unique_actual}")

# Top1 命中分析
print(f"\n【Top1 命中情况】")
hit_by_score = Counter()
miss_by_score = Counter()
for p in packs:
    pred = p["model_sorted"][0][0]
    actual = p["actual"]
    if pred == actual:
        hit_by_score[pred] += 1
    else:
        miss_by_score[pred] += 1

print(f"{'比分':>10} {'预测次数':>10} {'命中次数':>10} {'命中率':>10}")
print("-" * 45)
for score, cnt in pred_top1.most_common(10):
    hit = hit_by_score.get(score, 0)
    rate = hit / cnt * 100 if cnt > 0 else 0
    print(f"{str(score):>10} {cnt:>10} {hit:>10} {rate:>9.1f}%")

# 从未预测但经常出现的比分
print(f"\n【漏预测的高频比分】（实际出现多但模型很少预测为Top1）")
print(f"{'比分':>10} {'实际出现':>10} {'模型预测':>10} {'差距':>10}")
print("-" * 45)
for score, actual_cnt in actual_dist.most_common(20):
    pred_cnt = pred_top1.get(score, 0)
    if actual_cnt >= 50 and pred_cnt < actual_cnt * 0.3:
        print(f"{str(score):>10} {actual_cnt:>10} {pred_cnt:>10} {actual_cnt - pred_cnt:>10}")
