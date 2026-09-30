# -*- coding: utf-8 -*-
"""
预测模型v4b优化 —— 状态加成排列组合测试

测试方案：
1. 单项加成：只用高命中组合 / 只用客队主导组合 / 只用双方差组合
2. 两两组合
3. 全部组合
4. 调优加成系数
"""
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import product, combinations
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, str(Path("engine/scripts/research")))

import score_family_model as sfm

print("=" * 100)
print("预测模型v4b优化 —— 状态加成排列组合测试")
print("=" * 100)

INITIAL_CAPITAL = 1000.0
START_DATE = "2025-12-01"
BASE_UNIT = 2.0


# ============================================================
# 数据加载
# ============================================================

def load_hist():
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
            score_odds = {}
            for kk, v in crs.items():
                if str(kk).startswith("other") or not v:
                    continue
                try:
                    hh, aa = (int(x) for x in str(kk).split(":")[:2])
                    score_odds[(hh, aa)] = float(v)
                except (ValueError, TypeError):
                    continue
            if len(score_odds) < 20:
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
                "score_odds": score_odds,
            })
    out.sort(key=lambda x: x["date"])
    return out


zh = {}
for tid, srcs in sfm.load_aliases().items():
    if srcs.get("zh"):
        zh[srcs["zh"]] = tid

tl = sfm.league_timeline()
hist = load_hist()

print(f"数据：历史{len(hist)}场")


# ============================================================
# TeamData
# ============================================================

@dataclass
class TeamData:
    n: int = 0
    gf: int = 0
    ga: int = 0
    cs: int = 0
    becs: int = 0
    btts: int = 0
    over25: int = 0
    gf_home: int = 0
    gf_home_n: int = 0
    gf_away: int = 0
    gf_away_n: int = 0
    ga_home: int = 0
    ga_home_n: int = 0
    ga_away: int = 0
    ga_away_n: int = 0
    recent: List[Tuple] = field(default_factory=list)

    @property
    def gf_avg(self): return self.gf / max(self.n, 1)
    @property
    def ga_avg(self): return self.ga / max(self.n, 1)
    @property
    def cs_rate(self): return self.cs / max(self.n, 1)
    @property
    def becs_rate(self): return self.becs / max(self.n, 1)
    @property
    def btts_rate(self): return self.btts / max(self.n, 1)
    @property
    def over25_rate(self): return self.over25 / max(self.n, 1)
    @property
    def gf_home_avg(self): return self.gf_home / max(self.gf_home_n, 1)
    @property
    def gf_away_avg(self): return self.gf_away / max(self.gf_away_n, 1)
    @property
    def ga_home_avg(self): return self.ga_home / max(self.ga_home_n, 1)
    @property
    def ga_away_avg(self): return self.ga_away / max(self.ga_away_n, 1)

    def form_score(self) -> int:
        recent3 = self.recent[-3:] if len(self.recent) >= 3 else self.recent
        score = 0
        for _, scored, conceded in recent3:
            if scored > conceded: score += 3
            elif scored == conceded: score += 1
        return score


def ts_to_td(ts):
    return TeamData(
        n=ts.n, gf=ts.gf, ga=ts.ga,
        cs=ts.cs, becs=ts.becs, btts=ts.btts, over25=ts.over25,
        gf_home=ts.gf_side[0][0], gf_home_n=ts.gf_side[0][1],
        gf_away=ts.gf_side[1][0], gf_away_n=ts.gf_side[1][1],
        ga_home=ts.ga_side[0][0], ga_home_n=ts.ga_side[0][1],
        ga_away=ts.ga_side[1][0], ga_away_n=ts.ga_side[1][1],
        recent=ts.recent.copy() if hasattr(ts, 'recent') else [],
    )


# ============================================================
# 基础配置
# ============================================================

HIST_FREQ = {
    (1, 1): 0.12, (2, 1): 0.09, (1, 0): 0.09, (1, 2): 0.08,
    (0, 1): 0.07, (0, 0): 0.07, (2, 0): 0.07, (2, 2): 0.05,
    (0, 2): 0.05, (3, 0): 0.04, (3, 1): 0.04, (3, 2): 0.03,
    (1, 3): 0.03, (2, 3): 0.02, (4, 0): 0.02, (4, 1): 0.02,
    (0, 3): 0.02, (3, 3): 0.01,
}

TH = {
    "gf_low": 1.0, "gf_high": 1.8,
    "ga_low": 0.8, "ga_high": 1.5,
    "cs_rate_high": 0.35, "becs_rate_high": 0.35,
    "btts_high": 0.55, "over25_high": 0.55,
    "form_good": 7, "form_bad": 3,
}

# 三类状态加成组合
# A类：高命中平局组合（双方状态接近中等偏下）
HIGH_HIT_DRAW_COMBOS = {
    (3, 5): 0.4, (5, 2): 0.4, (5, 5): 0.35, (2, 2): 0.3, (2, 1): 0.3,
    (4, 5): 0.25, (2, 5): 0.25, (7, 7): 0.2, (5, 4): 0.2, (1, 3): 0.2,
    (4, 2): 0.15, (5, 3): 0.15, (1, 2): 0.15, (4, 4): 0.15, (5, 1): 0.15,
    (4, 1): 0.15, (1, 1): 0.15,
}

# B类：客队主导组合（客队状态好，主队状态差）
AWAY_DOMINANT_COMBOS = {
    (0, 9): 0.3, (1, 9): 0.2, (2, 9): 0.2, (3, 9): 0.15,
    (0, 7): 0.2, (1, 7): 0.15, (2, 7): 0.15, (3, 7): 0.1,
}

# C类：主队主导组合（主队状态好，客队状态差）
HOME_DOMINANT_COMBOS = {
    (9, 0): 0.2, (9, 1): 0.15, (9, 2): 0.15, (9, 3): 0.1,
    (7, 0): 0.15, (7, 1): 0.1, (7, 2): 0.1, (7, 3): 0.1,
}


# ============================================================
# 预测器
# ============================================================

class PredictorWithBoost:
    """带状态加成的预测器"""

    def __init__(self, use_draw=False, use_away=False, use_home=False, boost_factor=1.0):
        self.use_draw = use_draw
        self.use_away = use_away
        self.use_home = use_home
        self.boost_factor = boost_factor

    def predict(self, home: TeamData, away: TeamData) -> List[Tuple]:
        # 1. 场景分析
        scenes = self._analyze_scenes(home, away)

        # 2. 原始信号
        raw = self._calc_signals(scenes)

        # 3. 频率校准
        calibrated = {s: 0.5*raw.get(s,0)+0.5*HIST_FREQ.get(s,0)
                      for s in set(raw)|set(HIST_FREQ)}

        # 4. 状态加成
        boosted = self._apply_boost(calibrated, home, away)

        return sorted(boosted.items(), key=lambda x: -x[1])

    def _analyze_scenes(self, h: TeamData, a: TeamData) -> Dict:
        scenes = {}

        s1 = {"support": defaultdict(float)}
        if h.gf_home_avg > TH["gf_high"] and a.ga_away_avg > TH["ga_high"]:
            s1["support"]["home_win"] = 0.4
        elif a.gf_away_avg > TH["gf_high"] and h.ga_home_avg > TH["ga_high"]:
            s1["support"]["away_win"] = 0.3
        elif h.gf_avg < TH["gf_low"] and a.gf_avg < TH["gf_low"]:
            s1["support"]["low_score"] = 0.4
            s1["support"]["draw"] = 0.3
        elif h.gf_avg > TH["gf_high"] and a.gf_avg > TH["gf_high"]:
            s1["support"]["high_score"] = 0.4
        else:
            s1["support"]["draw"] = 0.2
        scenes["S1"] = s1

        s2 = {"support": defaultdict(float)}
        hf, af = h.form_score(), a.form_score()
        if hf >= TH["form_good"] and af <= TH["form_bad"]:
            s2["support"]["home_win"] = 0.4
        elif af >= TH["form_good"] and hf <= TH["form_bad"]:
            s2["support"]["away_win"] = 0.4
        elif hf <= TH["form_bad"] and af <= TH["form_bad"]:
            s2["support"]["low_score"] = 0.3
            s2["support"]["draw"] = 0.3
        else:
            s2["support"]["draw"] = 0.2
        scenes["S2"] = s2

        s3 = {"support": defaultdict(float)}
        if h.cs_rate > TH["cs_rate_high"] and a.becs_rate > TH["becs_rate_high"]:
            s3["support"]["home_clean"] = 0.4
        if a.cs_rate > TH["cs_rate_high"] and h.becs_rate > TH["becs_rate_high"]:
            s3["support"]["away_clean"] = 0.4
        if h.becs_rate > TH["becs_rate_high"] and a.becs_rate > TH["becs_rate_high"]:
            s3["support"]["low_score"] = 0.3
        scenes["S3"] = s3

        s4 = {"support": defaultdict(float)}
        if h.over25_rate > TH["over25_high"] and a.over25_rate > TH["over25_high"]:
            s4["support"]["high_score"] = 0.4
        if h.btts_rate > TH["btts_high"] and a.btts_rate > TH["btts_high"]:
            s4["support"]["both_score"] = 0.3
        scenes["S4"] = s4

        return scenes

    def _calc_signals(self, scenes: Dict) -> Dict[Tuple, float]:
        signals = defaultdict(float)
        mapping = {
            "home_win": {(1,0):0.4, (2,0):0.3, (2,1):0.3},
            "away_win": {(0,1):0.4, (0,2):0.3, (1,2):0.3},
            "draw": {(1,1):0.5, (0,0):0.3, (2,2):0.2},
            "low_score": {(0,0):0.4, (1,0):0.2, (0,1):0.2, (1,1):0.2},
            "high_score": {(2,2):0.3, (3,1):0.2, (2,3):0.2, (3,2):0.2, (1,3):0.1},
            "home_clean": {(1,0):0.4, (2,0):0.4, (3,0):0.2},
            "away_clean": {(0,1):0.4, (0,2):0.4, (0,3):0.2},
            "both_score": {(1,1):0.3, (2,1):0.2, (1,2):0.2, (2,2):0.2, (3,2):0.1},
        }
        for scene in scenes.values():
            for effect, strength in scene["support"].items():
                if effect in mapping:
                    for score, weight in mapping[effect].items():
                        signals[score] += strength * weight
        return dict(signals)

    def _apply_boost(self, signals: Dict, h: TeamData, a: TeamData) -> Dict:
        hf, af = h.form_score(), a.form_score()
        form_combo = (hf, af)
        boosted = signals.copy()
        bf = self.boost_factor

        # A类：平局加成
        if self.use_draw and form_combo in HIGH_HIT_DRAW_COMBOS:
            boost = HIGH_HIT_DRAW_COMBOS[form_combo] * bf
            for score in [(1,1), (0,0), (2,2)]:
                if score in boosted:
                    boosted[score] *= (1 + boost)

        # B类：客队主导加成
        if self.use_away and form_combo in AWAY_DOMINANT_COMBOS:
            boost = AWAY_DOMINANT_COMBOS[form_combo] * bf
            for score in [(0,1), (1,2), (0,2)]:
                if score in boosted:
                    boosted[score] *= (1 + boost)

        # C类：主队主导加成
        if self.use_home and form_combo in HOME_DOMINANT_COMBOS:
            boost = HOME_DOMINANT_COMBOS[form_combo] * bf
            for score in [(1,0), (2,0), (2,1)]:
                if score in boosted:
                    boosted[score] *= (1 + boost)

        return boosted


# ============================================================
# 数据准备
# ============================================================

blind = []
for m in hist:
    if m["date"] < START_DATE:
        continue
    hid, aid = zh.get(m["home_zh"]), zh.get(m["away_zh"])
    if hid and aid:
        blind.append({**m, "hid": hid, "aid": aid})

merged = [("L", d, h, a, hg, ag, None) for d, h, a, hg, ag in tl]
merged += [("B", m["date"], m["hid"], m["aid"], m["actual"][0], m["actual"][1], i)
           for i, m in enumerate(blind)]
merged.sort(key=lambda r: (r[1], 0 if r[0] == "L" else 1))

stats = defaultdict(sfm.TeamStats)
match_packs = []

for r in merged:
    kind, date, h, a, hg, ag = r[0], r[1], r[2], r[3], r[4], r[5]
    if kind == "B":
        idx = r[6]
        m = blind[idx]
        if stats[h].n < sfm.MIN_HIST or stats[a].n < sfm.MIN_HIST:
            continue
        match_packs.append({
            "date": m["date"],
            "actual": m["actual"],
            "odds": m["score_odds"],
            "home_data": ts_to_td(stats[h]),
            "away_data": ts_to_td(stats[a]),
        })
    if kind == "L":
        stats[h].add(hg, ag, True, a)
        stats[a].add(ag, hg, False, h)

by_day = defaultdict(list)
for p in match_packs:
    by_day[p["date"]].append(p)

print(f"有效预测包：{len(match_packs)}场，跨越{len(by_day)}天")


# ============================================================
# 回测函数
# ============================================================

def run_backtest(predictor):
    capital = INITIAL_CAPITAL
    total_cost = 0
    total_payout = 0
    n_tickets = 0
    n_hits = 0
    max_capital = capital
    min_capital = capital

    for day in sorted(by_day):
        day_packs = by_day[day]
        if len(day_packs) < 2:
            continue

        for p in day_packs:
            pred = predictor.predict(p["home_data"], p["away_data"])
            p["pred"] = pred
            p["max_signal"] = pred[0][1] if pred else 0

        selected = sorted(day_packs, key=lambda x: -x["max_signal"])[:2]

        all_picks = []
        for p in selected:
            picks = []
            for score, sig in p["pred"][:2]:
                odds = p["odds"].get(score, 999)
                picks.append((score, odds, p["actual"]))
            all_picks.append(picks)

        all_bets = list(product(*all_picks))
        cost = len(all_bets) * BASE_UNIT

        if capital < cost:
            continue

        capital -= cost
        total_cost += cost
        n_tickets += 1

        payout = 0
        for combo in all_bets:
            if all(score == actual for score, odds, actual in combo):
                combo_odds = 1.0
                for score, odds, actual in combo:
                    combo_odds *= odds
                payout += BASE_UNIT * combo_odds

        capital += payout
        total_payout += payout
        if payout > 0:
            n_hits += 1

        max_capital = max(max_capital, capital)
        min_capital = min(min_capital, capital)

    drawdown = (max_capital - min_capital) / max_capital * 100 if max_capital > 0 else 0

    return {
        "final": capital,
        "profit": (capital - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100,
        "n_hits": n_hits,
        "n_tickets": n_tickets,
        "drawdown": drawdown,
    }


# ============================================================
# 排列组合测试
# ============================================================

print("\n" + "=" * 100)
print("1. 单项加成测试")
print("=" * 100)

results = []

# Baseline（无加成）
p = PredictorWithBoost(use_draw=False, use_away=False, use_home=False)
r = run_backtest(p)
results.append(("Baseline", r))
print(f"Baseline: 最终{r['final']:.1f}元, 收益{r['profit']:+.1f}%, 命中{r['n_hits']}/{r['n_tickets']}, 回撤{r['drawdown']:.1f}%")

# A: 只用平局加成
p = PredictorWithBoost(use_draw=True, use_away=False, use_home=False)
r = run_backtest(p)
results.append(("A:平局加成", r))
print(f"A:平局加成: 最终{r['final']:.1f}元, 收益{r['profit']:+.1f}%, 命中{r['n_hits']}/{r['n_tickets']}, 回撤{r['drawdown']:.1f}%")

# B: 只用客队主导加成
p = PredictorWithBoost(use_draw=False, use_away=True, use_home=False)
r = run_backtest(p)
results.append(("B:客队主导", r))
print(f"B:客队主导: 最终{r['final']:.1f}元, 收益{r['profit']:+.1f}%, 命中{r['n_hits']}/{r['n_tickets']}, 回撤{r['drawdown']:.1f}%")

# C: 只用主队主导加成
p = PredictorWithBoost(use_draw=False, use_away=False, use_home=True)
r = run_backtest(p)
results.append(("C:主队主导", r))
print(f"C:主队主导: 最终{r['final']:.1f}元, 收益{r['profit']:+.1f}%, 命中{r['n_hits']}/{r['n_tickets']}, 回撤{r['drawdown']:.1f}%")

print("\n" + "=" * 100)
print("2. 两两组合测试")
print("=" * 100)

# A+B
p = PredictorWithBoost(use_draw=True, use_away=True, use_home=False)
r = run_backtest(p)
results.append(("A+B:平局+客队", r))
print(f"A+B:平局+客队: 最终{r['final']:.1f}元, 收益{r['profit']:+.1f}%, 命中{r['n_hits']}/{r['n_tickets']}, 回撤{r['drawdown']:.1f}%")

# A+C
p = PredictorWithBoost(use_draw=True, use_away=False, use_home=True)
r = run_backtest(p)
results.append(("A+C:平局+主队", r))
print(f"A+C:平局+主队: 最终{r['final']:.1f}元, 收益{r['profit']:+.1f}%, 命中{r['n_hits']}/{r['n_tickets']}, 回撤{r['drawdown']:.1f}%")

# B+C
p = PredictorWithBoost(use_draw=False, use_away=True, use_home=True)
r = run_backtest(p)
results.append(("B+C:客队+主队", r))
print(f"B+C:客队+主队: 最终{r['final']:.1f}元, 收益{r['profit']:+.1f}%, 命中{r['n_hits']}/{r['n_tickets']}, 回撤{r['drawdown']:.1f}%")

print("\n" + "=" * 100)
print("3. 全部组合测试")
print("=" * 100)

# A+B+C
p = PredictorWithBoost(use_draw=True, use_away=True, use_home=True)
r = run_backtest(p)
results.append(("A+B+C:全部", r))
print(f"A+B+C:全部: 最终{r['final']:.1f}元, 收益{r['profit']:+.1f}%, 命中{r['n_hits']}/{r['n_tickets']}, 回撤{r['drawdown']:.1f}%")

print("\n" + "=" * 100)
print("4. 加成系数调优（基于最佳组合）")
print("=" * 100)

# 找出最佳组合
best = max(results, key=lambda x: x[1]["profit"])
print(f"最佳组合：{best[0]}")

# 测试不同的boost_factor
for bf in [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]:
    # 根据最佳组合设置
    if "A" in best[0]:
        use_draw = True
    else:
        use_draw = False
    if "B" in best[0]:
        use_away = True
    else:
        use_away = False
    if "C" in best[0]:
        use_home = True
    else:
        use_home = False

    p = PredictorWithBoost(use_draw=use_draw, use_away=use_away, use_home=use_home, boost_factor=bf)
    r = run_backtest(p)
    print(f"系数{bf}: 最终{r['final']:.1f}元, 收益{r['profit']:+.1f}%, 命中{r['n_hits']}/{r['n_tickets']}, 回撤{r['drawdown']:.1f}%")

print("\n" + "=" * 100)
print("5. 结果汇总（按收益排序）")
print("=" * 100)

results_sorted = sorted(results, key=lambda x: -x[1]["profit"])
print(f"\n{'方案':<20} {'最终资金':>12} {'收益率':>10} {'命中':>10} {'回撤':>8}")
print("-" * 70)
for name, r in results_sorted:
    print(f"{name:<20} {r['final']:>12.1f} {r['profit']:>+9.1f}% {r['n_hits']:>4}/{r['n_tickets']:<4} {r['drawdown']:>7.1f}%")

print("\n" + "=" * 100)
print("完成")
print("=" * 100)
