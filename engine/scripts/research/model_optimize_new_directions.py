# -*- coding: utf-8 -*-
"""比分模型优化 —— 新方向探索"""
import json
import sys
import numpy as np
from collections import Counter, defaultdict
from itertools import product
from pathlib import Path
from math import exp, factorial

sys.path.insert(0, str(Path("engine/scripts/research")))

import score_family_model as sfm
from ticket_dynamic_k_backtest import load_hist_full

UNIT = 2.0

print("=" * 100)
print("比分模型优化 —— 新方向探索")
print("=" * 100)

# ========================================
# 数据准备
# ========================================

zh = {}
for tid, srcs in sfm.load_aliases().items():
    if srcs.get("zh"):
        zh[srcs["zh"]] = tid

tl = sfm.league_timeline()
hist = load_hist_full()

cut = "2026-01-01"

def prepare_data(cut_date):
    """准备训练和盲测数据"""
    blind = []
    for m in hist:
        if m["date"] < cut_date:
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

        if kind == "L" and date < cut_date and fv_h[12] >= sfm.MIN_HIST and fv_a[12] >= sfm.MIN_HIST:
            train_data.append({
                "X": sfm.feature_row((fv_h, fv_a)),
                "fv_h": fv_h,
                "fv_a": fv_a,
                "family": sfm.family_of(hg, ag),
                "score": (hg, ag),
                "home_goals": hg,
                "away_goals": ag,
            })
        elif kind == "B":
            blind_data.append({
                "X": sfm.feature_row((fv_h, fv_a)),
                "fv_h": fv_h,
                "fv_a": fv_a,
                "actual": blind[r[6]]["actual"],
                "odds": blind[r[6]]["odds"],
                "date": blind[r[6]]["date"],
            })

        stats[h].add(hg, ag, True)
        stats[a].add(ag, hg, False)
        tot_g += hg + ag
        tot_n += 1

    return train_data, blind_data

train_data, blind_data = prepare_data(cut)
print(f"训练样本：{len(train_data)}，盲测样本：{len(blind_data)}")

# 族内比分频率
fam_dist = defaultdict(Counter)
for d in train_data:
    fam_dist[d["family"]][d["score"]] += 1


def run_backtest(packs, gap_threshold=0.05):
    """回测"""
    by_day = defaultdict(list)
    for p in packs:
        by_day[p["date"]].append(p)

    total_cost = 0.0
    total_payout = 0.0
    n_tickets = 0
    n_hits = 0

    for day in sorted(by_day):
        day_packs = by_day[day]
        if len(day_packs) < 2:
            continue

        day_sorted = sorted(day_packs, key=lambda p: -p["gap"])[:2]
        p1, p2 = day_sorted[0], day_sorted[1]

        k1 = 1 if p1["gap"] > gap_threshold else 2
        k2 = 1 if p2["gap"] > gap_threshold else 2

        picks1 = [(s, p1["odds"].get(s, 999)) for s, prob in p1["model_sorted"][:k1]]
        picks2 = [(s, p2["odds"].get(s, 999)) for s, prob in p2["model_sorted"][:k2]]

        if not picks1 or not picks2:
            continue

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

    profit = (total_payout - total_cost) / total_cost if total_cost > 0 else -1
    return {
        "n_tickets": n_tickets,
        "n_hits": n_hits,
        "total_cost": total_cost,
        "total_payout": total_payout,
        "profit": profit,
    }


# ========================================
# 基准：原始族模型
# ========================================

print("\n" + "=" * 100)
print("基准：原始族模型")
print("=" * 100)

X_tr = np.array([d["X"] for d in train_data])
y_tr = np.array([sfm.CLASSES.index(d["family"]) for d in train_data])
X_bl = np.array([b["X"] for b in blind_data])

model_base = sfm.train_softmax(X_tr, y_tr, len(sfm.CLASSES))
P_base = sfm.predict_proba(model_base, X_bl)

packs_base = []
for i, b in enumerate(blind_data):
    model_probs = {}
    for ci, cname in enumerate(sfm.CLASSES):
        tot = sum(fam_dist.get(cname, {}).values()) or 1
        for s, c in fam_dist.get(cname, {}).items():
            model_probs[s] = model_probs.get(s, 0.0) + P_base[i][ci] * (c / tot)

    mdl_sorted = sorted(model_probs.items(), key=lambda kv: -kv[1])
    gap = mdl_sorted[0][1] - mdl_sorted[1][1] if len(mdl_sorted) >= 2 else 0
    packs_base.append({
        "date": b["date"], "actual": b["actual"], "odds": b["odds"],
        "model_sorted": mdl_sorted, "gap": gap,
    })

result_base = run_backtest(packs_base)
print(f"  盈利率：{result_base['profit']*100:+.2f}%")
print(f"  命中：{result_base['n_hits']}/{result_base['n_tickets']}")
print(f"  成本：{result_base['total_cost']:.0f}，派彩：{result_base['total_payout']:.0f}")


# ========================================
# 方向 D：泊松模型（经典方法）
# ========================================

print("\n" + "=" * 100)
print("方向 D：泊松模型")
print("=" * 100)

def poisson_prob(lam, k):
    """泊松分布概率 P(X=k)"""
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return (lam ** k) * exp(-lam) / factorial(k)

def poisson_score_probs(lambda_home, lambda_away, max_goals=6):
    """用泊松分布计算比分概率"""
    probs = {}
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            probs[(h, a)] = poisson_prob(lambda_home, h) * poisson_prob(lambda_away, a)
    return probs

# 训练进球预测模型
y_home = np.array([d["home_goals"] for d in train_data])
y_away = np.array([d["away_goals"] for d in train_data])

# 用线性回归预测期望进球
from scipy.optimize import minimize

def train_goal_model(X, y):
    """训练进球预测模型（线性回归）"""
    n, d = X.shape
    Xb = np.hstack([X, np.ones((n, 1))])

    def loss(w):
        pred = Xb @ w
        pred = np.maximum(pred, 0.1)  # 确保正数
        return ((pred - y) ** 2).sum() + 0.1 * (w ** 2).sum()

    w0 = np.zeros(d + 1)
    res = minimize(loss, w0, method="L-BFGS-B")
    return res.x

w_home = train_goal_model(X_tr, y_home)
w_away = train_goal_model(X_tr, y_away)

# 预测
Xb_bl = np.hstack([X_bl, np.ones((len(X_bl), 1))])
pred_home = np.maximum(Xb_bl @ w_home, 0.5)
pred_away = np.maximum(Xb_bl @ w_away, 0.5)

print(f"  主队进球预测：均值 {pred_home.mean():.2f}（实际 {np.mean([b['actual'][0] for b in blind_data]):.2f}）")
print(f"  客队进球预测：均值 {pred_away.mean():.2f}（实际 {np.mean([b['actual'][1] for b in blind_data]):.2f}）")

# 构建预测包
packs_poisson = []
for i, b in enumerate(blind_data):
    model_probs = poisson_score_probs(pred_home[i], pred_away[i])
    mdl_sorted = sorted(model_probs.items(), key=lambda kv: -kv[1])
    gap = mdl_sorted[0][1] - mdl_sorted[1][1] if len(mdl_sorted) >= 2 else 0
    packs_poisson.append({
        "date": b["date"], "actual": b["actual"], "odds": b["odds"],
        "model_sorted": mdl_sorted, "gap": gap,
    })

result_poisson = run_backtest(packs_poisson)
print(f"  盈利率：{result_poisson['profit']*100:+.2f}%")
print(f"  命中：{result_poisson['n_hits']}/{result_poisson['n_tickets']}")


# ========================================
# 方向 E：混合模型（族模型 + 泊松加权）
# ========================================

print("\n" + "=" * 100)
print("方向 E：混合模型（族模型 + 泊松）")
print("=" * 100)

for alpha in [0.9, 0.8, 0.7, 0.5]:
    packs_mix = []
    for i, b in enumerate(blind_data):
        # 族模型概率
        fam_probs = {}
        for ci, cname in enumerate(sfm.CLASSES):
            tot = sum(fam_dist.get(cname, {}).values()) or 1
            for s, c in fam_dist.get(cname, {}).items():
                fam_probs[s] = fam_probs.get(s, 0.0) + P_base[i][ci] * (c / tot)

        # 泊松概率
        poi_probs = poisson_score_probs(pred_home[i], pred_away[i])

        # 混合
        all_scores = set(fam_probs.keys()) | set(poi_probs.keys())
        model_probs = {}
        for s in all_scores:
            fp = fam_probs.get(s, 0.001)
            pp = poi_probs.get(s, 0.001)
            model_probs[s] = alpha * fp + (1 - alpha) * pp

        # 归一化
        total = sum(model_probs.values())
        model_probs = {s: p/total for s, p in model_probs.items()}

        mdl_sorted = sorted(model_probs.items(), key=lambda kv: -kv[1])
        gap = mdl_sorted[0][1] - mdl_sorted[1][1] if len(mdl_sorted) >= 2 else 0
        packs_mix.append({
            "date": b["date"], "actual": b["actual"], "odds": b["odds"],
            "model_sorted": mdl_sorted, "gap": gap,
        })

    result_mix = run_backtest(packs_mix)
    mark = "✅" if result_mix["profit"] > result_base["profit"] else ""
    print(f"  alpha={alpha}: 盈利率 {result_mix['profit']*100:+.2f}%, 命中 {result_mix['n_hits']} {mark}")


# ========================================
# 方向 F：改进族内分配（用特征调整）
# ========================================

print("\n" + "=" * 100)
print("方向 F：改进族内分配")
print("=" * 100)

# F1: 按主队强度调整族内分配
# 强队更可能大比分获胜 (2:0, 3:0)，弱队更可能小比分 (1:0)

def adjusted_fam_dist(fam, fv_h, fv_a):
    """根据特征调整族内比分分布"""
    base = dict(fam_dist.get(fam, {}))
    if not base:
        return base

    # 主队攻击力（场均进球）
    h_attack = fv_h[0] if len(fv_h) > 0 else 1.3
    a_attack = fv_a[0] if len(fv_a) > 0 else 1.3

    # 调整权重
    adjusted = {}
    for (hg, ag), cnt in base.items():
        # 主队强 → 更可能高进球
        factor = 1.0
        if fam in ["home_clean", "home_multi"]:
            if hg >= 2 and h_attack > 1.5:
                factor = 1.3
            elif hg == 1 and h_attack < 1.0:
                factor = 1.2
        elif fam in ["away_clean", "away_multi"]:
            if ag >= 2 and a_attack > 1.5:
                factor = 1.3
            elif ag == 1 and a_attack < 1.0:
                factor = 1.2
        elif fam == "draw":
            total = hg + ag
            if total == 0 and h_attack < 1.2 and a_attack < 1.2:
                factor = 1.3  # 弱队更可能 0:0
            elif total >= 4:
                factor = 1.2 if h_attack > 1.5 and a_attack > 1.5 else 0.8

        adjusted[(hg, ag)] = cnt * factor

    # 归一化
    total = sum(adjusted.values())
    return {s: c/total for s, c in adjusted.items()}

packs_F1 = []
for i, b in enumerate(blind_data):
    model_probs = {}
    for ci, cname in enumerate(sfm.CLASSES):
        adj_dist = adjusted_fam_dist(cname, b["fv_h"], b["fv_a"])
        for s, p in adj_dist.items():
            model_probs[s] = model_probs.get(s, 0.0) + P_base[i][ci] * p

    mdl_sorted = sorted(model_probs.items(), key=lambda kv: -kv[1])
    gap = mdl_sorted[0][1] - mdl_sorted[1][1] if len(mdl_sorted) >= 2 else 0
    packs_F1.append({
        "date": b["date"], "actual": b["actual"], "odds": b["odds"],
        "model_sorted": mdl_sorted, "gap": gap,
    })

result_F1 = run_backtest(packs_F1)
print(f"  F1（特征调整族内分配）：盈利率 {result_F1['profit']*100:+.2f}%, 命中 {result_F1['n_hits']}")


# ========================================
# 方向 G：用赔率作为先验
# ========================================

print("\n" + "=" * 100)
print("方向 G：赔率先验 + 模型调整")
print("=" * 100)

def odds_to_prob(odds_dict):
    """赔率转概率（去水）"""
    if not odds_dict:
        return {}
    total = sum(1/o for o in odds_dict.values() if o and o > 1)
    if total == 0:
        return {}
    return {s: (1/o)/total for s, o in odds_dict.items() if o and o > 1}

# G1: 赔率为主，模型微调
for alpha in [0.3, 0.5, 0.7]:
    packs_G = []
    for i, b in enumerate(blind_data):
        # 模型概率
        fam_probs = {}
        for ci, cname in enumerate(sfm.CLASSES):
            tot = sum(fam_dist.get(cname, {}).values()) or 1
            for s, c in fam_dist.get(cname, {}).items():
                fam_probs[s] = fam_probs.get(s, 0.0) + P_base[i][ci] * (c / tot)

        # 赔率概率
        odds_probs = odds_to_prob(b["odds"])

        # 混合
        all_scores = set(fam_probs.keys()) | set(odds_probs.keys())
        model_probs = {}
        for s in all_scores:
            mp = fam_probs.get(s, 0.001)
            op = odds_probs.get(s, 0.001)
            model_probs[s] = alpha * mp + (1 - alpha) * op

        total = sum(model_probs.values())
        model_probs = {s: p/total for s, p in model_probs.items()}

        mdl_sorted = sorted(model_probs.items(), key=lambda kv: -kv[1])
        gap = mdl_sorted[0][1] - mdl_sorted[1][1] if len(mdl_sorted) >= 2 else 0
        packs_G.append({
            "date": b["date"], "actual": b["actual"], "odds": b["odds"],
            "model_sorted": mdl_sorted, "gap": gap,
        })

    result_G = run_backtest(packs_G)
    mark = "✅" if result_G["profit"] > result_base["profit"] else ""
    print(f"  alpha={alpha}（模型{int(alpha*100)}%+赔率{int((1-alpha)*100)}%）：盈利率 {result_G['profit']*100:+.2f}%, 命中 {result_G['n_hits']} {mark}")


# ========================================
# 汇总
# ========================================

print("\n" + "=" * 100)
print("汇总对比")
print("=" * 100)

print(f"\n{'方法':<35} {'盈利率':>12} {'命中':>8} {'成本':>10} {'派彩':>10}")
print("-" * 80)

all_results = [
    ("基准（族模型）", result_base),
    ("D: 泊松模型", result_poisson),
    ("F1: 特征调整族内分配", result_F1),
]

for name, r in sorted(all_results, key=lambda x: -x[1]["profit"]):
    mark = "✅" if r["profit"] > 0 else ""
    print(f"{name:<35} {r['profit']*100:>+11.2f}% {r['n_hits']:>8} {r['total_cost']:>10.0f} {r['total_payout']:>10.0f} {mark}")
