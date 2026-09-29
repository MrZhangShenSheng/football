# -*- coding: utf-8 -*-
"""特征影响力分析 —— 计算每个特征对模型预测的贡献"""
import json
import sys
import numpy as np
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path("engine/scripts/research")))

import score_family_model as sfm
from ticket_dynamic_k_backtest import load_hist_full

print("=" * 90)
print("特征影响力分析")
print("=" * 90)

# 特征名称（26维 v2 特征）
FEATURE_NAMES = [
    # 主队特征 (0-12)
    "h_gf_mean",      # 0: 主队场均进球
    "h_ga_mean",      # 1: 主队场均失球
    "h_gf_var",       # 2: 主队进球方差
    "h_ga_var",       # 3: 主队失球方差
    "h_gf_home",      # 4: 主队主场进球
    "h_ga_home",      # 5: 主队主场失球
    "h_gf_away",      # 6: 主队客场进球
    "h_ga_away",      # 7: 主队客场失球
    "h_clean_pct",    # 8: 主队零封率
    "h_fail_pct",     # 9: 主队被零封率
    "h_multi_pct",    # 10: 主队多球率
    "h_lg_gf_ratio",  # 11: 主队进球/联赛均值
    "h_n_matches",    # 12: 主队历史场次
    # 客队特征 (13-25)
    "a_gf_mean",      # 13: 客队场均进球
    "a_ga_mean",      # 14: 客队场均失球
    "a_gf_var",       # 15: 客队进球方差
    "a_ga_var",       # 16: 客队失球方差
    "a_gf_home",      # 17: 客队主场进球
    "a_ga_home",      # 18: 客队主场失球
    "a_gf_away",      # 19: 客队客场进球
    "a_ga_away",      # 20: 客队客场失球
    "a_clean_pct",    # 21: 客队零封率
    "a_fail_pct",     # 22: 客队被零封率
    "a_multi_pct",    # 23: 客队多球率
    "a_lg_gf_ratio",  # 24: 客队进球/联赛均值
    "a_n_matches",    # 25: 客队历史场次
]

# 加载数据
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

X_tr = np.array(X_tr)
y_tr = np.array(y_tr)
X_bl = np.array(X_bl)

print(f"训练样本：{len(X_tr)}，盲测样本：{len(X_bl)}")
print(f"特征维度：{X_tr.shape[1]}")

# ========================================
# 1. 模型权重分析（直接看 softmax 系数）
# ========================================
print("\n" + "=" * 90)
print("1. 模型权重分析（Softmax 系数）")
print("=" * 90)

model = sfm.train_softmax(X_tr, y_tr, len(sfm.CLASSES))
W = model["W"][:-1, :]  # 权重矩阵 (n_features, n_classes)，去掉偏置行
b = model["W"][-1, :]   # 偏置

print(f"\n权重矩阵形状: {W.shape}")
print(f"族类别: {sfm.CLASSES}")

# 每个特征对每个族的权重
print(f"\n{'特征':<20} ", end="")
for cls in sfm.CLASSES:
    print(f"{cls:>12}", end="")
print(f"{'|绝对值和|':>12}")
print("-" * 100)

feature_importance = []
for i, fname in enumerate(FEATURE_NAMES):
    print(f"{fname:<20} ", end="")
    abs_sum = 0
    for j, cls in enumerate(sfm.CLASSES):
        w = W[i, j]
        abs_sum += abs(w)
        # 高亮显著权重
        if abs(w) > 0.3:
            print(f"{w:>+12.3f}*", end="")
        else:
            print(f"{w:>+12.3f}", end="")
    print(f"{abs_sum:>12.3f}")
    feature_importance.append((fname, abs_sum))

# 按重要性排序
print("\n【特征重要性排序（按权重绝对值和）】")
for fname, imp in sorted(feature_importance, key=lambda x: -x[1])[:15]:
    bar = "█" * int(imp * 10)
    print(f"  {fname:<20} {imp:>6.3f} {bar}")

# ========================================
# 2. 置换重要性（Permutation Importance）
# ========================================
print("\n" + "=" * 90)
print("2. 置换重要性分析（打乱特征后准确率下降）")
print("=" * 90)

# 基准准确率
P_base = sfm.predict_proba(model, X_bl)
y_pred_base = P_base.argmax(axis=1)
y_true = np.array([sfm.CLASSES.index(sfm.family_of(m["actual"][0], m["actual"][1])) for m in meta])
base_acc = (y_pred_base == y_true).mean()
print(f"\n基准族准确率: {base_acc*100:.2f}%")

perm_importance = []
np.random.seed(42)

for i, fname in enumerate(FEATURE_NAMES):
    # 打乱第 i 个特征
    X_perm = X_bl.copy()
    np.random.shuffle(X_perm[:, i])

    # 重新预测
    P_perm = sfm.predict_proba(model, X_perm)
    y_pred_perm = P_perm.argmax(axis=1)
    perm_acc = (y_pred_perm == y_true).mean()

    # 重要性 = 准确率下降
    importance = base_acc - perm_acc
    perm_importance.append((fname, importance, perm_acc))

print(f"\n{'特征':<20} {'准确率下降':>12} {'打乱后准确率':>15} {'影响方向':>12}")
print("-" * 70)
for fname, imp, acc in sorted(perm_importance, key=lambda x: -x[1]):
    direction = "正向关键" if imp > 0.01 else ("负向有害" if imp < -0.005 else "中性")
    bar = "+" * int(imp * 500) if imp > 0 else "-" * int(-imp * 500)
    print(f"  {fname:<20} {imp*100:>+11.2f}% {acc*100:>14.2f}% {direction:>12} {bar}")

# ========================================
# 3. 特征与族命中的相关性
# ========================================
print("\n" + "=" * 90)
print("3. 特征与族命中的相关性")
print("=" * 90)

# 对于每个特征，计算其在不同族的均值
print(f"\n{'特征':<20} ", end="")
for cls in sfm.CLASSES:
    print(f"{cls:>12}", end="")
print()
print("-" * 100)

for i, fname in enumerate(FEATURE_NAMES[:13]):  # 只看主队特征
    print(f"{fname:<20} ", end="")
    for j, cls in enumerate(sfm.CLASSES):
        # 该族的样本
        mask = y_tr == j
        mean_val = X_tr[mask, i].mean() if mask.sum() > 0 else 0
        print(f"{mean_val:>12.3f}", end="")
    print()

# ========================================
# 4. 问题特征诊断
# ========================================
print("\n" + "=" * 90)
print("4. 问题特征诊断")
print("=" * 90)

# multi 族为什么不命中？
print("\n【multi 族预测失败分析】")
multi_idx = [sfm.CLASSES.index("home_multi"), sfm.CLASSES.index("away_multi")]

# 看 multi 族的权重特点
print("home_multi 族的关键权重：")
w_hm = W[:, sfm.CLASSES.index("home_multi")]
for i in np.argsort(-np.abs(w_hm))[:5]:
    print(f"  {FEATURE_NAMES[i]}: {w_hm[i]:+.3f}")

print("\naway_multi 族的关键权重：")
w_am = W[:, sfm.CLASSES.index("away_multi")]
for i in np.argsort(-np.abs(w_am))[:5]:
    print(f"  {FEATURE_NAMES[i]}: {w_am[i]:+.3f}")

# 对比 multi 族和 clean 族的特征差异
print("\n【multi vs clean 族特征对比】")
hm_mask = y_tr == sfm.CLASSES.index("home_multi")
hc_mask = y_tr == sfm.CLASSES.index("home_clean")

print(f"  特征                 home_multi    home_clean    差异")
print("-" * 60)
for i in [0, 1, 10, 8]:  # gf_mean, ga_mean, multi_pct, clean_pct
    hm_mean = X_tr[hm_mask, i].mean()
    hc_mean = X_tr[hc_mask, i].mean()
    diff = hm_mean - hc_mean
    print(f"  {FEATURE_NAMES[i]:<20} {hm_mean:>10.3f} {hc_mean:>10.3f} {diff:>+10.3f}")

# ========================================
# 5. 改进建议
# ========================================
print("\n" + "=" * 90)
print("5. 改进建议")
print("=" * 90)

# 找出最有害的特征
harmful = [x for x in perm_importance if x[1] < -0.005]
neutral = [x for x in perm_importance if -0.005 <= x[1] <= 0.005]
helpful = [x for x in perm_importance if x[1] > 0.005]

print(f"\n有害特征（打乱后准确率提升）：{len(harmful)} 个")
for fname, imp, _ in harmful:
    print(f"  - {fname}: {imp*100:+.2f}% → 建议移除或重新设计")

print(f"\n中性特征（影响不大）：{len(neutral)} 个")
for fname, imp, _ in neutral[:5]:
    print(f"  - {fname}: {imp*100:+.2f}%")

print(f"\n有用特征（打乱后准确率下降）：{len(helpful)} 个")
for fname, imp, _ in sorted(helpful, key=lambda x: -x[1])[:5]:
    print(f"  + {fname}: {imp*100:+.2f}% → 核心特征")
