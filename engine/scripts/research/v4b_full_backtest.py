# -*- coding: utf-8 -*-
"""
v4b最佳方案 —— 完整大范围历史回测

配置：A+C(平局+主队)加成，系数1.25
数据范围：2025-10-01 ~ 2026-09-28（全部历史赔率数据）

验证要点：
1. 使用真实历史比分
2. 使用真实历史赔率
3. 完整的月度明细
4. 资金曲线和回撤分析
"""
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import product
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, str(Path("engine/scripts/research")))

import score_family_model as sfm

print("=" * 100)
print("v4b最佳方案 —— 完整大范围历史回测")
print("=" * 100)

INITIAL_CAPITAL = 1000.0
START_DATE = "2025-10-01"  # 从最早的数据开始
BASE_UNIT = 2.0


# ============================================================
# 数据加载（完整版，带详细信息）
# ============================================================

def load_hist_complete():
    """加载完整历史数据，保留所有原始信息"""
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

            # 解析比分赔率
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
                "actual_str": sc,
                "score_odds": score_odds,
                "league": m.get("league", ""),
            })

    out.sort(key=lambda x: x["date"])
    return out


# 加载别名映射
zh = {}
for tid, srcs in sfm.load_aliases().items():
    if srcs.get("zh"):
        zh[srcs["zh"]] = tid

tl = sfm.league_timeline()
hist = load_hist_complete()

print(f"历史赔率数据：{len(hist)}场")
print(f"日期范围：{hist[0]['date']} ~ {hist[-1]['date']}")
print(f"联赛时间线数据：{len(tl)}场")


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
# v4b最佳预测器配置
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

# A类：平局加成（高命中率状态组合）
DRAW_BOOST_COMBOS = {
    (3, 5): 0.4, (5, 2): 0.4, (5, 5): 0.35, (2, 2): 0.3, (2, 1): 0.3,
    (4, 5): 0.25, (2, 5): 0.25, (7, 7): 0.2, (5, 4): 0.2, (1, 3): 0.2,
    (4, 2): 0.15, (5, 3): 0.15, (1, 2): 0.15, (4, 4): 0.15, (5, 1): 0.15,
    (4, 1): 0.15, (1, 1): 0.15,
}

# C类：主队主导加成
HOME_BOOST_COMBOS = {
    (9, 0): 0.2, (9, 1): 0.15, (9, 2): 0.15, (9, 3): 0.1,
    (7, 0): 0.15, (7, 1): 0.1, (7, 2): 0.1, (7, 3): 0.1,
}

BOOST_FACTOR = 1.25  # 最佳加成系数


class PredictorV4bBest:
    """v4b最佳配置预测器"""

    def predict(self, home: TeamData, away: TeamData) -> List[Tuple]:
        # 1. 场景分析
        scenes = self._scenes(home, away)

        # 2. 计算原始信号
        raw = self._signals(scenes)

        # 3. 频率校准
        calibrated = {s: 0.5*raw.get(s,0)+0.5*HIST_FREQ.get(s,0)
                      for s in set(raw)|set(HIST_FREQ)}

        # 4. 状态加成
        boosted = self._apply_boost(calibrated, home, away)

        return sorted(boosted.items(), key=lambda x: -x[1])

    def _scenes(self, h: TeamData, a: TeamData) -> Dict:
        sc = {}

        # S1: 攻防
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
        sc["S1"] = s1

        # S2: 状态
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
        sc["S2"] = s2

        # S3: 零封
        s3 = {"support": defaultdict(float)}
        if h.cs_rate > TH["cs_rate_high"] and a.becs_rate > TH["becs_rate_high"]:
            s3["support"]["home_clean"] = 0.4
        if a.cs_rate > TH["cs_rate_high"] and h.becs_rate > TH["becs_rate_high"]:
            s3["support"]["away_clean"] = 0.4
        if h.becs_rate > TH["becs_rate_high"] and a.becs_rate > TH["becs_rate_high"]:
            s3["support"]["low_score"] = 0.3
        sc["S3"] = s3

        # S4: 大球
        s4 = {"support": defaultdict(float)}
        if h.over25_rate > TH["over25_high"] and a.over25_rate > TH["over25_high"]:
            s4["support"]["high_score"] = 0.4
        if h.btts_rate > TH["btts_high"] and a.btts_rate > TH["btts_high"]:
            s4["support"]["both_score"] = 0.3
        sc["S4"] = s4

        return sc

    def _signals(self, scenes: Dict) -> Dict[Tuple, float]:
        signals = defaultdict(float)
        effect_map = {
            "home_win": {(1,0): 0.4, (2,0): 0.3, (2,1): 0.3},
            "away_win": {(0,1): 0.4, (0,2): 0.3, (1,2): 0.3},
            "draw": {(1,1): 0.5, (0,0): 0.3, (2,2): 0.2},
            "low_score": {(0,0): 0.4, (1,0): 0.2, (0,1): 0.2, (1,1): 0.2},
            "high_score": {(2,2): 0.3, (3,1): 0.2, (2,3): 0.2, (3,2): 0.2, (1,2): 0.1},
            "home_clean": {(1,0): 0.4, (2,0): 0.4, (3,0): 0.2},
            "away_clean": {(0,1): 0.4, (0,2): 0.4, (0,3): 0.2},
            "both_score": {(1,1): 0.3, (2,1): 0.2, (1,2): 0.2, (2,2): 0.2, (3,2): 0.1},
        }
        for sc in scenes.values():
            for eff, strength in sc["support"].items():
                if eff in effect_map:
                    for score, weight in effect_map[eff].items():
                        signals[score] += strength * weight
        return dict(signals)

    def _apply_boost(self, signals: Dict, h: TeamData, a: TeamData) -> Dict:
        """应用A+C加成"""
        hf, af = h.form_score(), a.form_score()
        form_combo = (hf, af)
        boosted = signals.copy()

        # A类：平局加成
        if form_combo in DRAW_BOOST_COMBOS:
            boost = DRAW_BOOST_COMBOS[form_combo] * BOOST_FACTOR
            for score in [(1,1), (0,0), (2,2)]:
                if score in boosted:
                    boosted[score] *= (1 + boost)

        # C类：主队主导加成
        if form_combo in HOME_BOOST_COMBOS:
            boost = HOME_BOOST_COMBOS[form_combo] * BOOST_FACTOR
            for score in [(1,0), (2,0), (2,1), (3,0), (3,1)]:
                if score in boosted:
                    boosted[score] *= (1 + boost)

        return boosted


# ============================================================
# 数据准备
# ============================================================

# 构建盲测数据
blind = []
for m in hist:
    if m["date"] < START_DATE:
        continue
    hid, aid = zh.get(m["home_zh"]), zh.get(m["away_zh"])
    if hid and aid:
        blind.append({**m, "hid": hid, "aid": aid})

print(f"盲测样本：{len(blind)}场")

# 合并时间线
merged = [("L", d, h, a, hg, ag, None) for d, h, a, hg, ag in tl]
merged += [("B", m["date"], m["hid"], m["aid"], m["actual"][0], m["actual"][1], i)
           for i, m in enumerate(blind)]
merged.sort(key=lambda r: (r[1], 0 if r[0] == "L" else 1))

# 滚动统计
stats = defaultdict(sfm.TeamStats)
predictor = PredictorV4bBest()
match_packs = []

for r in merged:
    kind, date, h, a, hg, ag = r[0], r[1], r[2], r[3], r[4], r[5]

    if kind == "B":
        idx = r[6]
        m = blind[idx]

        if stats[h].n < sfm.MIN_HIST or stats[a].n < sfm.MIN_HIST:
            continue

        home_data = ts_to_td(stats[h])
        away_data = ts_to_td(stats[a])
        pred = predictor.predict(home_data, away_data)

        match_packs.append({
            "date": m["date"],
            "home_zh": m["home_zh"],
            "away_zh": m["away_zh"],
            "actual": m["actual"],
            "actual_str": m["actual_str"],
            "odds": m["score_odds"],
            "prediction": pred,
            "home_form": home_data.form_score(),
            "away_form": away_data.form_score(),
        })

    if kind == "L":
        stats[h].add(hg, ag, True, a)
        stats[a].add(ag, hg, False, h)

# 按日期分组
by_day = defaultdict(list)
for p in match_packs:
    by_day[p["date"]].append(p)

print(f"有效预测包：{len(match_packs)}场，跨越{len(by_day)}天")


# ============================================================
# 完整回测
# ============================================================

print("\n" + "=" * 100)
print("完整回测（2串1双选）")
print("=" * 100)

capital = INITIAL_CAPITAL
monthly = defaultdict(lambda: {"cost": 0, "payout": 0, "hits": 0, "tickets": 0})

total_cost = 0
total_payout = 0
n_tickets = 0
n_hits = 0
hit_details = []
capital_history = [(START_DATE, capital)]
min_capital = capital
max_capital = capital

for day in sorted(by_day):
    day_packs = by_day[day]
    month = day[:7]

    if len(day_packs) < 2:
        continue

    # 选场：按最大信号排序
    for p in day_packs:
        p["max_signal"] = p["prediction"][0][1] if p["prediction"] else 0
    selected = sorted(day_packs, key=lambda p: -p["max_signal"])[:2]

    # 每场选Top2比分
    all_picks = []
    for p in selected:
        picks = []
        for score, sig in p["prediction"][:2]:
            odds = p["odds"].get(score, 0)
            if odds > 0:
                picks.append((score, odds, p["actual"], p["home_zh"], p["away_zh"]))
        if not picks:
            break
        all_picks.append(picks)

    if len(all_picks) < 2:
        continue

    # 生成投注组合
    all_bets = list(product(*all_picks))
    cost = len(all_bets) * BASE_UNIT

    # 检查资金
    if capital < cost:
        continue

    capital -= cost
    total_cost += cost
    monthly[month]["cost"] += cost
    monthly[month]["tickets"] += 1
    n_tickets += 1

    # 计算派彩
    payout = 0.0
    hit_combo = None
    for combo in all_bets:
        all_correct = all(score == actual for score, odds, actual, home, away in combo)
        if all_correct:
            combo_odds = 1.0
            for score, odds, actual, home, away in combo:
                combo_odds *= odds
            payout += BASE_UNIT * combo_odds
            hit_combo = combo

    capital += payout
    total_payout += payout
    monthly[month]["payout"] += payout

    if payout > 0:
        n_hits += 1
        monthly[month]["hits"] += 1
        hit_details.append({
            "date": day,
            "cost": cost,
            "payout": payout,
            "combo": [(f"{home} vs {away}: {score[0]}:{score[1]}", odds)
                      for score, odds, actual, home, away in hit_combo],
            "actual": [(f"{home} vs {away}: {actual[0]}:{actual[1]}")
                       for score, odds, actual, home, away in hit_combo],
        })

    capital_history.append((day, capital))
    min_capital = min(min_capital, capital)
    max_capital = max(max_capital, capital)

# 计算最大回撤
max_drawdown = 0
peak = INITIAL_CAPITAL
for date, cap in capital_history:
    if cap > peak:
        peak = cap
    dd = (peak - cap) / peak
    if dd > max_drawdown:
        max_drawdown = dd


# ============================================================
# 输出结果
# ============================================================

print(f"\n【总体结果】")
print(f"  起始资金: {INITIAL_CAPITAL:.0f}元")
print(f"  结束资金: {capital:.1f}元")
print(f"  净盈亏: {capital - INITIAL_CAPITAL:+.1f}元")
print(f"  收益率: {(capital - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100:+.1f}%")
print(f"  总票数: {n_tickets}")
print(f"  命中票数: {n_hits}")
print(f"  命中率: {n_hits / n_tickets * 100:.1f}%")
print(f"  总成本: {total_cost:.0f}元")
print(f"  总派彩: {total_payout:.1f}元")
print(f"  最低资金: {min_capital:.1f}元")
print(f"  最高资金: {max_capital:.1f}元")
print(f"  最大回撤: {max_drawdown * 100:.1f}%")

print(f"\n【月度明细】")
print(f"{'月份':<10} {'票数':>6} {'命中':>6} {'成本':>10} {'派彩':>12} {'盈亏':>12} {'累计资金':>12}")
print("-" * 80)

cumulative = INITIAL_CAPITAL
for month in sorted(monthly.keys()):
    m = monthly[month]
    profit = m["payout"] - m["cost"]
    cumulative += profit
    print(f"{month:<10} {m['tickets']:>6} {m['hits']:>6} {m['cost']:>10.0f} {m['payout']:>12.1f} {profit:>+12.1f} {cumulative:>12.1f}")

print(f"\n【命中明细】(共{n_hits}票)")
print("-" * 100)
for i, h in enumerate(hit_details, 1):
    print(f"\n{i}. {h['date']}: 成本{h['cost']:.0f}元 派彩{h['payout']:.1f}元")
    for j, (bet, actual) in enumerate(zip(h['combo'], h['actual'])):
        print(f"   场{j+1}: 预测{bet[0]} 赔率{bet[1]:.1f} | 实际{actual}")

print("\n" + "=" * 100)
print("回测完成")
print("=" * 100)
