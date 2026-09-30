# -*- coding: utf-8 -*-
"""多场景模型 —— 比分+总进球数组合方案验证"""
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
print("多场景模型 —— 比分+总进球数组合方案验证")
print("=" * 100)

INITIAL_CAPITAL = 1000.0
START_DATE = "2025-12-01"
UNIT = 2.0


# ============================================================
# 数据加载
# ============================================================

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


zh = {}
for tid, srcs in sfm.load_aliases().items():
    if srcs.get("zh"):
        zh[srcs["zh"]] = tid

tl = sfm.league_timeline()
hist = load_hist_full()

print(f"数据：历史{len(hist)}场，联赛库{len(tl)}场")


# ============================================================
# TeamData
# ============================================================

@dataclass
class TeamData:
    n: int = 0
    gf: int = 0
    ga: int = 0
    win: int = 0
    gd: int = 0
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
    recent_gd: List[int] = field(default_factory=list)
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


def ts_to_td(ts) -> TeamData:
    return TeamData(
        n=ts.n, gf=ts.gf, ga=ts.ga, win=ts.win, gd=ts.gd,
        cs=ts.cs, becs=ts.becs, btts=ts.btts, over25=ts.over25,
        gf_home=ts.gf_side[0][0], gf_home_n=ts.gf_side[0][1],
        gf_away=ts.gf_side[1][0], gf_away_n=ts.gf_side[1][1],
        ga_home=ts.ga_side[0][0], ga_home_n=ts.ga_side[0][1],
        ga_away=ts.ga_side[1][0], ga_away_n=ts.ga_side[1][1],
        recent_gd=ts.recent_gd.copy(), recent=ts.recent.copy(),
    )


# ============================================================
# 历史频率
# ============================================================

HIST_FREQ = {
    (1, 1): 0.12, (2, 1): 0.09, (1, 0): 0.09, (1, 2): 0.08,
    (0, 1): 0.07, (0, 0): 0.07, (2, 0): 0.07, (2, 2): 0.05,
    (0, 2): 0.05, (3, 0): 0.04, (3, 1): 0.04, (3, 2): 0.03,
    (1, 3): 0.03, (2, 3): 0.02, (4, 0): 0.02, (4, 1): 0.02,
    (0, 3): 0.02, (3, 3): 0.01,
}

THRESHOLDS = {
    "gf_low": 1.0, "gf_high": 1.8,
    "ga_low": 0.8, "ga_high": 1.5,
    "cs_rate_high": 0.35, "becs_rate_high": 0.35,
    "btts_high": 0.55, "over25_high": 0.55,
    "form_good": 7, "form_bad": 3,
}


# ============================================================
# 组合预测器（比分 + 总进球数）
# ============================================================

class CombinedPredictor:
    """比分 + 总进球数组合预测器"""

    def __init__(self):
        self.th = THRESHOLDS
        self.calibration_strength = 0.5

    def predict(self, home: TeamData, away: TeamData) -> Dict:
        """预测比分和总进球数"""

        # 1. 场景分析
        scenes = self._analyze_scenes(home, away)

        # 2. 比分预测
        score_signals = self._calc_score_signals(scenes)
        score_calibrated = self._freq_calibrate(score_signals)
        sorted_scores = sorted(score_calibrated.items(), key=lambda x: -x[1])

        # 3. 总进球数预测
        total_goals = self._predict_total_goals(home, away, scenes)

        return {
            "score_predictions": score_calibrated,
            "sorted_scores": sorted_scores,
            "total_goals": total_goals,
            "scenes": scenes,
        }

    def _analyze_scenes(self, home: TeamData, away: TeamData) -> Dict:
        scenes = {}
        th = self.th

        # S1: 攻防
        s1 = {"support": defaultdict(float)}
        if home.gf_home_avg > th["gf_high"] and away.ga_away_avg > th["ga_high"]:
            s1["support"]["home_win"] = 0.4
            s1["support"]["high_score"] = 0.2
        elif away.gf_away_avg > th["gf_high"] and home.ga_home_avg > th["ga_high"]:
            s1["support"]["away_win"] = 0.3
            s1["support"]["high_score"] = 0.2
        elif home.gf_avg < th["gf_low"] and away.gf_avg < th["gf_low"]:
            s1["support"]["low_score"] = 0.4
            s1["support"]["draw"] = 0.3
        elif home.gf_avg > th["gf_high"] and away.gf_avg > th["gf_high"]:
            s1["support"]["high_score"] = 0.5
        else:
            s1["support"]["draw"] = 0.2
        scenes["S1"] = s1

        # S2: 状态
        s2 = {"support": defaultdict(float)}
        home_form = home.form_score()
        away_form = away.form_score()
        if home_form >= th["form_good"] and away_form <= th["form_bad"]:
            s2["support"]["home_win"] = 0.4
        elif away_form >= th["form_good"] and home_form <= th["form_bad"]:
            s2["support"]["away_win"] = 0.4
        elif home_form <= th["form_bad"] and away_form <= th["form_bad"]:
            s2["support"]["low_score"] = 0.3
            s2["support"]["draw"] = 0.3
        else:
            s2["support"]["draw"] = 0.2
        scenes["S2"] = s2

        # S3: 零封
        s3 = {"support": defaultdict(float)}
        if home.cs_rate > th["cs_rate_high"] and away.becs_rate > th["becs_rate_high"]:
            s3["support"]["home_clean"] = 0.4
            s3["support"]["low_score"] = 0.2
        if away.cs_rate > th["cs_rate_high"] and home.becs_rate > th["becs_rate_high"]:
            s3["support"]["away_clean"] = 0.4
            s3["support"]["low_score"] = 0.2
        if home.becs_rate > th["becs_rate_high"] and away.becs_rate > th["becs_rate_high"]:
            s3["support"]["low_score"] = 0.4
        scenes["S3"] = s3

        # S4: 大球
        s4 = {"support": defaultdict(float)}
        if home.over25_rate > th["over25_high"] and away.over25_rate > th["over25_high"]:
            s4["support"]["high_score"] = 0.5
        if home.btts_rate > th["btts_high"] and away.btts_rate > th["btts_high"]:
            s4["support"]["both_score"] = 0.4
            s4["support"]["high_score"] = 0.2
        scenes["S4"] = s4

        return scenes

    def _calc_score_signals(self, scenes: Dict) -> Dict[Tuple, float]:
        signals = defaultdict(float)
        for scene_id, scene in scenes.items():
            for effect, strength in scene["support"].items():
                if effect == "home_win":
                    signals[(1, 0)] += strength * 0.4
                    signals[(2, 0)] += strength * 0.3
                    signals[(2, 1)] += strength * 0.3
                elif effect == "away_win":
                    signals[(0, 1)] += strength * 0.4
                    signals[(0, 2)] += strength * 0.3
                    signals[(1, 2)] += strength * 0.3
                elif effect == "draw":
                    signals[(1, 1)] += strength * 0.5
                    signals[(0, 0)] += strength * 0.3
                    signals[(2, 2)] += strength * 0.2
                elif effect == "low_score":
                    signals[(0, 0)] += strength * 0.4
                    signals[(1, 0)] += strength * 0.2
                    signals[(0, 1)] += strength * 0.2
                    signals[(1, 1)] += strength * 0.2
                elif effect == "high_score":
                    signals[(2, 2)] += strength * 0.25
                    signals[(3, 1)] += strength * 0.2
                    signals[(2, 3)] += strength * 0.15
                    signals[(3, 2)] += strength * 0.15
                    signals[(2, 1)] += strength * 0.15
                    signals[(1, 2)] += strength * 0.1
                elif effect == "home_clean":
                    signals[(1, 0)] += strength * 0.4
                    signals[(2, 0)] += strength * 0.4
                    signals[(3, 0)] += strength * 0.2
                elif effect == "away_clean":
                    signals[(0, 1)] += strength * 0.4
                    signals[(0, 2)] += strength * 0.4
                    signals[(0, 3)] += strength * 0.2
                elif effect == "both_score":
                    signals[(1, 1)] += strength * 0.3
                    signals[(2, 1)] += strength * 0.2
                    signals[(1, 2)] += strength * 0.2
                    signals[(2, 2)] += strength * 0.2
        return dict(signals)

    def _freq_calibrate(self, raw_signals: Dict) -> Dict:
        calibrated = {}
        for score in HIST_FREQ:
            raw = raw_signals.get(score, 0)
            freq = HIST_FREQ[score]
            calibrated[score] = (1 - self.calibration_strength) * raw + self.calibration_strength * freq
        return calibrated

    def _predict_total_goals(self, home: TeamData, away: TeamData, scenes: Dict) -> Dict:
        """预测总进球数"""

        # 基础预期进球
        expected_home = (home.gf_home_avg + away.ga_away_avg) / 2
        expected_away = (away.gf_away_avg + home.ga_home_avg) / 2
        expected_total = expected_home + expected_away

        # 场景调整
        adjustment = 0
        for scene_id, scene in scenes.items():
            if "high_score" in scene["support"]:
                adjustment += scene["support"]["high_score"] * 0.5
            if "low_score" in scene["support"]:
                adjustment -= scene["support"]["low_score"] * 0.5

        adjusted_total = expected_total + adjustment

        # 计算各档概率
        # 简化模型：基于调整后的预期值分配概率
        signals = {}

        if adjusted_total < 1.8:
            signals["0-1"] = 0.45
            signals["2-3"] = 0.40
            signals["4+"] = 0.15
        elif adjusted_total < 2.5:
            signals["0-1"] = 0.30
            signals["2-3"] = 0.50
            signals["4+"] = 0.20
        elif adjusted_total < 3.2:
            signals["0-1"] = 0.20
            signals["2-3"] = 0.50
            signals["4+"] = 0.30
        else:
            signals["0-1"] = 0.15
            signals["2-3"] = 0.40
            signals["4+"] = 0.45

        # 找最高信号
        best = max(signals, key=signals.get)

        return {
            "expected_total": round(adjusted_total, 2),
            "signals": signals,
            "prediction": best,
            "confidence": signals[best],
        }


# ============================================================
# 准备数据
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
            "odds": m["odds"],
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
# 滚动投注模拟 —— 多种组合策略
# ============================================================

def run_combo_simulation(strategy_name, score_weight, total_weight, score_unit, total_unit):
    """运行组合策略模拟"""

    predictor = CombinedPredictor()
    capital = INITIAL_CAPITAL

    total_score_cost = 0
    total_score_payout = 0
    total_goals_cost = 0
    total_goals_payout = 0

    n_score_tickets = 0
    n_score_hits = 0
    n_total_tickets = 0
    n_total_hits = 0

    monthly_data = defaultdict(lambda: {"score_cost": 0, "score_payout": 0,
                                        "total_cost": 0, "total_payout": 0,
                                        "score_hits": 0, "total_hits": 0})

    capital_history = []
    min_capital = capital

    for day in sorted(by_day):
        day_packs = by_day[day]
        month = day[:7]

        if len(day_packs) < 2:
            continue

        # 预测
        for p in day_packs:
            pred = predictor.predict(p["home_data"], p["away_data"])
            p["prediction"] = pred
            p["max_signal"] = pred["sorted_scores"][0][1] if pred["sorted_scores"] else 0

        # === 比分投注（2串1双选）===
        if score_weight > 0:
            selected = sorted(day_packs, key=lambda p: -p["max_signal"])[:2]

            all_picks = []
            for p in selected:
                picks = []
                for score, sig in p["prediction"]["sorted_scores"][:2]:
                    odds = p["odds"].get(score, 999)
                    picks.append((score, odds, p["actual"]))
                all_picks.append(picks)

            all_bets = list(product(*all_picks))
            score_cost = len(all_bets) * score_unit

            if capital >= score_cost:
                capital -= score_cost
                total_score_cost += score_cost
                monthly_data[month]["score_cost"] += score_cost
                n_score_tickets += 1

                score_payout = 0
                for combo in all_bets:
                    all_correct = all(s == a for s, o, a in combo)
                    if all_correct:
                        combo_odds = 1.0
                        for s, o, a in combo:
                            combo_odds *= o
                        score_payout += score_unit * combo_odds

                if score_payout > 0:
                    n_score_hits += 1
                    monthly_data[month]["score_hits"] += 1

                capital += score_payout
                total_score_payout += score_payout
                monthly_data[month]["score_payout"] += score_payout

        # === 总进球数投注（单场）===
        if total_weight > 0:
            for p in day_packs:
                pred = p["prediction"]["total_goals"]
                actual_total = p["actual"][0] + p["actual"][1]

                # 只投高信心的
                if pred["confidence"] < 0.4:
                    continue

                total_cost = total_unit
                if capital < total_cost:
                    continue

                capital -= total_cost
                total_goals_cost += total_cost
                monthly_data[month]["total_cost"] += total_cost
                n_total_tickets += 1

                # 判断是否命中
                hit = False
                if pred["prediction"] == "0-1" and actual_total <= 1:
                    hit = True
                elif pred["prediction"] == "2-3" and 2 <= actual_total <= 3:
                    hit = True
                elif pred["prediction"] == "4+" and actual_total >= 4:
                    hit = True

                total_payout = 0
                if hit:
                    # 假设赔率：0-1约2.5，2-3约2.0，4+约2.8
                    odds_map = {"0-1": 2.5, "2-3": 2.0, "4+": 2.8}
                    total_payout = total_unit * odds_map.get(pred["prediction"], 2.0)
                    n_total_hits += 1
                    monthly_data[month]["total_hits"] += 1

                capital += total_payout
                total_goals_payout += total_payout
                monthly_data[month]["total_payout"] += total_payout

        capital_history.append((day, capital))
        min_capital = min(min_capital, capital)

    max_capital = max(c for _, c in capital_history) if capital_history else capital
    max_drawdown = (max_capital - min_capital) / max_capital * 100 if max_capital > 0 else 0

    return {
        "final_capital": capital,
        "profit_rate": (capital - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100,
        "score_cost": total_score_cost,
        "score_payout": total_score_payout,
        "score_tickets": n_score_tickets,
        "score_hits": n_score_hits,
        "total_cost": total_goals_cost,
        "total_payout": total_goals_payout,
        "total_tickets": n_total_tickets,
        "total_hits": n_total_hits,
        "max_drawdown": max_drawdown,
        "monthly": dict(monthly_data),
    }


# ============================================================
# 运行多种组合策略
# ============================================================

print("\n" + "=" * 100)
print("组合策略对比实验")
print("=" * 100)

strategies = [
    # (名称, 比分权重, 总进球权重, 比分单注, 总进球单注)
    ("纯比分2串1", 1, 0, 2, 0),
    ("纯总进球", 0, 1, 0, 2),
    ("比分+总进球(1:1)", 1, 1, 2, 2),
    ("比分+总进球(1:2)", 1, 2, 2, 2),
    ("比分+总进球(2:1)", 2, 1, 2, 2),
    ("比分重+总进球轻", 1, 1, 4, 1),
    ("总进球重+比分轻", 1, 1, 1, 4),
]

results = []
for name, sw, tw, su, tu in strategies:
    print(f"  运行 {name}...", end=" ")
    r = run_combo_simulation(name, sw, tw, su, tu)
    r["name"] = name
    results.append(r)
    print(f"完成. 最终{r['final_capital']:.1f}元, 收益{r['profit_rate']:+.1f}%")

# 排序
results.sort(key=lambda x: -x["final_capital"])

print("\n" + "=" * 100)
print("实验结果汇总（按最终资金排序）")
print("=" * 100)

print(f"\n{'策略名称':<25} {'最终资金':>10} {'收益率':>10} {'比分命中':>10} {'总进球命中':>12} {'回撤':>8}")
print("-" * 90)

for r in results:
    score_hit_str = f"{r['score_hits']}/{r['score_tickets']}" if r['score_tickets'] > 0 else "-"
    total_hit_str = f"{r['total_hits']}/{r['total_tickets']}" if r['total_tickets'] > 0 else "-"
    print(f"{r['name']:<25} {r['final_capital']:>10.1f} {r['profit_rate']:>+9.1f}% {score_hit_str:>10} {total_hit_str:>12} {r['max_drawdown']:>7.1f}%")


# ============================================================
# 最佳方案详细分析
# ============================================================

best = results[0]
print("\n" + "=" * 100)
print(f"最佳方案【{best['name']}】详细分析")
print("=" * 100)

print(f"\n最终资金：{best['final_capital']:.1f} 元")
print(f"总收益率：{best['profit_rate']:+.1f}%")
print(f"最大回撤：{best['max_drawdown']:.1f}%")

print(f"\n【比分投注】")
print(f"  总成本：{best['score_cost']:.0f} 元")
print(f"  总派彩：{best['score_payout']:.1f} 元")
print(f"  净盈亏：{best['score_payout'] - best['score_cost']:+.1f} 元")
print(f"  命中率：{best['score_hits']}/{best['score_tickets']} = {best['score_hits']/max(best['score_tickets'],1)*100:.1f}%")

print(f"\n【总进球数投注】")
print(f"  总成本：{best['total_cost']:.0f} 元")
print(f"  总派彩：{best['total_payout']:.1f} 元")
print(f"  净盈亏：{best['total_payout'] - best['total_cost']:+.1f} 元")
print(f"  命中率：{best['total_hits']}/{best['total_tickets']} = {best['total_hits']/max(best['total_tickets'],1)*100:.1f}%")

print("\n【月度明细】")
print(f"{'月份':<10} {'比分盈亏':>12} {'总进球盈亏':>12} {'合计盈亏':>12}")
print("-" * 50)

for month in sorted(best["monthly"].keys()):
    m = best["monthly"][month]
    score_profit = m["score_payout"] - m["score_cost"]
    total_profit = m["total_payout"] - m["total_cost"]
    combined = score_profit + total_profit
    print(f"{month:<10} {score_profit:>+11.1f} {total_profit:>+11.1f} {combined:>+11.1f}")
