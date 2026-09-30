# -*- coding: utf-8 -*-
"""多场景模型优化 —— 新场景、新市场、动态选场"""
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
print("多场景模型优化 —— 方向4/5/6探索")
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
                "league": m.get("league", ""),
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
# TeamData（扩展版，增加H2H支持）
# ============================================================

@dataclass
class TeamDataV3:
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
    # 新增：H2H记录
    h2h: Dict[str, List[Tuple]] = field(default_factory=dict)  # {对手ID: [(进球,失球,是否主场),...]}

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

    def add_h2h(self, opponent_id: str, scored: int, conceded: int, at_home: bool):
        if opponent_id not in self.h2h:
            self.h2h[opponent_id] = []
        self.h2h[opponent_id].append((scored, conceded, at_home))
        if len(self.h2h[opponent_id]) > 10:
            self.h2h[opponent_id].pop(0)

    def get_h2h_stats(self, opponent_id: str) -> Dict:
        """获取对特定对手的历史战绩"""
        records = self.h2h.get(opponent_id, [])
        if not records:
            return {"n": 0}

        wins = sum(1 for s, c, _ in records if s > c)
        draws = sum(1 for s, c, _ in records if s == c)
        losses = sum(1 for s, c, _ in records if s < c)
        total_gf = sum(s for s, c, _ in records)
        total_ga = sum(c for s, c, _ in records)

        return {
            "n": len(records),
            "wins": wins,
            "draws": draws,
            "losses": losses,
            "win_rate": wins / len(records),
            "avg_gf": total_gf / len(records),
            "avg_ga": total_ga / len(records),
        }


class TeamStatsV3:
    """扩展版TeamStats，支持H2H"""
    __slots__ = ("n", "gf", "ga", "win", "gd", "cs", "becs", "btts", "over25",
                 "gf_side", "ga_side", "recent_gd", "recent", "h2h")

    def __init__(self):
        self.n = 0
        self.gf = self.ga = self.win = self.gd = 0
        self.cs = self.becs = self.btts = self.over25 = 0
        self.gf_side = [[0, 0], [0, 0]]
        self.ga_side = [[0, 0], [0, 0]]
        self.recent_gd = []
        self.recent = []
        self.h2h = {}

    def add(self, scored: int, conceded: int, at_home: bool, opp=None):
        i = 0 if at_home else 1
        self.n += 1
        self.gf += scored
        self.ga += conceded
        self.gd += scored - conceded
        self.win += int(scored > conceded)
        self.cs += int(conceded == 0)
        self.becs += int(scored == 0)
        self.btts += int(scored > 0 and conceded > 0)
        self.over25 += int(scored + conceded > 2)
        self.gf_side[i][0] += scored
        self.gf_side[i][1] += 1
        self.ga_side[i][0] += conceded
        self.ga_side[i][1] += 1
        self.recent_gd.append(scored - conceded)
        if len(self.recent_gd) > 3:
            self.recent_gd.pop(0)
        if opp is not None:
            self.recent.append((opp, scored, conceded))
            if len(self.recent) > 10:
                self.recent.pop(0)
            # H2H
            if opp not in self.h2h:
                self.h2h[opp] = []
            self.h2h[opp].append((scored, conceded, at_home))
            if len(self.h2h[opp]) > 10:
                self.h2h[opp].pop(0)


def ts_to_td_v3(ts: TeamStatsV3) -> TeamDataV3:
    return TeamDataV3(
        n=ts.n, gf=ts.gf, ga=ts.ga, win=ts.win, gd=ts.gd,
        cs=ts.cs, becs=ts.becs, btts=ts.btts, over25=ts.over25,
        gf_home=ts.gf_side[0][0], gf_home_n=ts.gf_side[0][1],
        gf_away=ts.gf_side[1][0], gf_away_n=ts.gf_side[1][1],
        ga_home=ts.ga_side[0][0], ga_home_n=ts.ga_side[0][1],
        ga_away=ts.ga_side[1][0], ga_away_n=ts.ga_side[1][1],
        recent_gd=ts.recent_gd.copy(), recent=ts.recent.copy(),
        h2h={k: v.copy() for k, v in ts.h2h.items()},
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

# 总进球数历史频率
TOTAL_GOALS_FREQ = {
    0: 0.07, 1: 0.16, 2: 0.23, 3: 0.23, 4: 0.15, 5: 0.09, 6: 0.04, 7: 0.02,
}


# ============================================================
# 预测器V3（新场景 + 新市场 + 动态选场）
# ============================================================

class PredictorV3:
    """预测器V3：
    - 方向4：新场景（H2H、联赛特性）
    - 方向5：新市场（总进球数）
    - 方向6：动态选场（信号强度阈值）
    """

    def __init__(self, config: Dict = None):
        config = config or {}
        self.th = config.get("thresholds", {
            "gf_low": 1.0, "gf_high": 1.8,
            "ga_low": 0.8, "ga_high": 1.5,
            "cs_rate_high": 0.35, "becs_rate_high": 0.35,
            "btts_high": 0.55, "over25_high": 0.55,
            "form_good": 7, "form_bad": 3,
        })
        self.calib_strength = config.get("calibration_strength", 0.5)
        # 新增：动态选场阈值
        self.signal_threshold = config.get("signal_threshold", 0.0)  # 信号>阈值才选
        # 新增：是否使用H2H场景
        self.use_h2h = config.get("use_h2h", True)

    def predict(self, home: TeamDataV3, away: TeamDataV3, away_id: str = None) -> Dict:
        """预测比分和总进球数"""

        # 场景分析（包含新场景）
        scenes = self._analyze_scenes(home, away, away_id)

        # 比分信号
        score_signals = self._calc_score_signals(scenes)
        score_calibrated = self._freq_calibrate(score_signals, HIST_FREQ)

        # 总进球数信号（方向5：新市场）
        total_goals_signals = self._calc_total_goals_signals(scenes)
        total_goals_calibrated = self._freq_calibrate_total(total_goals_signals)

        # 排序
        sorted_scores = sorted(score_calibrated.items(), key=lambda x: -x[1])
        sorted_total = sorted(total_goals_calibrated.items(), key=lambda x: -x[1])

        # 计算最大信号（用于动态选场）
        max_signal = sorted_scores[0][1] if sorted_scores else 0

        return {
            "score_predictions": score_calibrated,
            "sorted_scores": sorted_scores,
            "total_goals": total_goals_calibrated,
            "sorted_total": sorted_total,
            "max_signal": max_signal,
            "scenes": scenes,
        }

    def _analyze_scenes(self, home: TeamDataV3, away: TeamDataV3, away_id: str = None) -> Dict:
        """分析场景（含新场景）"""
        scenes = {}
        th = self.th

        # S1: 攻防对比（原有）
        s1 = {"support": defaultdict(float)}
        if home.gf_home_avg > th["gf_high"] and away.ga_away_avg > th["ga_high"]:
            s1["support"]["home_win"] = 0.4
            s1["support"]["home_goals_high"] = 0.3
        elif away.gf_away_avg > th["gf_high"] and home.ga_home_avg > th["ga_high"]:
            s1["support"]["away_win"] = 0.3
            s1["support"]["away_goals_high"] = 0.3
        elif home.gf_avg < th["gf_low"] and away.gf_avg < th["gf_low"]:
            s1["support"]["low_score"] = 0.4
            s1["support"]["total_low"] = 0.4
        elif home.gf_avg > th["gf_high"] and away.gf_avg > th["gf_high"]:
            s1["support"]["high_score"] = 0.4
            s1["support"]["total_high"] = 0.4
        else:
            s1["support"]["draw"] = 0.2
        scenes["S1"] = s1

        # S2: 近期状态（原有）
        s2 = {"support": defaultdict(float)}
        home_form = home.form_score()
        away_form = away.form_score()
        if home_form >= th["form_good"] and away_form <= th["form_bad"]:
            s2["support"]["home_win"] = 0.4
        elif away_form >= th["form_good"] and home_form <= th["form_bad"]:
            s2["support"]["away_win"] = 0.4
        elif home_form <= th["form_bad"] and away_form <= th["form_bad"]:
            s2["support"]["low_score"] = 0.3
            s2["support"]["total_low"] = 0.3
        else:
            s2["support"]["draw"] = 0.2
        scenes["S2"] = s2

        # S3: 零封能力（原有）
        s3 = {"support": defaultdict(float)}
        if home.cs_rate > th["cs_rate_high"] and away.becs_rate > th["becs_rate_high"]:
            s3["support"]["home_clean"] = 0.4
            s3["support"]["away_goals_zero"] = 0.3
        if away.cs_rate > th["cs_rate_high"] and home.becs_rate > th["becs_rate_high"]:
            s3["support"]["away_clean"] = 0.4
            s3["support"]["home_goals_zero"] = 0.3
        if home.becs_rate > th["becs_rate_high"] and away.becs_rate > th["becs_rate_high"]:
            s3["support"]["low_score"] = 0.3
            s3["support"]["total_low"] = 0.3
        scenes["S3"] = s3

        # S4: 大球倾向（原有）
        s4 = {"support": defaultdict(float)}
        if home.over25_rate > th["over25_high"] and away.over25_rate > th["over25_high"]:
            s4["support"]["high_score"] = 0.4
            s4["support"]["total_high"] = 0.4
        if home.btts_rate > th["btts_high"] and away.btts_rate > th["btts_high"]:
            s4["support"]["both_score"] = 0.3
            s4["support"]["total_mid"] = 0.2
        scenes["S4"] = s4

        # S5: H2H历史交锋（方向4：新场景）
        if self.use_h2h and away_id and away_id in home.h2h:
            s5 = {"support": defaultdict(float)}
            h2h_records = home.h2h[away_id]
            if len(h2h_records) >= 2:
                wins = sum(1 for s, c, _ in h2h_records if s > c)
                draws = sum(1 for s, c, _ in h2h_records if s == c)
                total_gf = sum(s for s, c, _ in h2h_records)
                total_ga = sum(c for s, c, _ in h2h_records)
                n = len(h2h_records)

                win_rate = wins / n
                avg_gf = total_gf / n
                avg_ga = total_ga / n
                avg_total = avg_gf + avg_ga

                # H2H规律
                if win_rate > 0.6:
                    s5["support"]["home_win"] = 0.3
                elif win_rate < 0.3:
                    s5["support"]["away_win"] = 0.3

                if avg_total > 3.0:
                    s5["support"]["total_high"] = 0.3
                    s5["support"]["high_score"] = 0.2
                elif avg_total < 2.0:
                    s5["support"]["total_low"] = 0.3
                    s5["support"]["low_score"] = 0.2

                if draws / n > 0.4:
                    s5["support"]["draw"] = 0.3

            scenes["S5_H2H"] = s5

        return scenes

    def _calc_score_signals(self, scenes: Dict) -> Dict[Tuple, float]:
        """计算比分信号"""
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
                    signals[(2, 2)] += strength * 0.3
                    signals[(3, 1)] += strength * 0.2
                    signals[(2, 3)] += strength * 0.2
                    signals[(3, 2)] += strength * 0.2
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

    def _calc_total_goals_signals(self, scenes: Dict) -> Dict[int, float]:
        """计算总进球数信号（方向5）"""
        signals = defaultdict(float)

        for scene_id, scene in scenes.items():
            for effect, strength in scene["support"].items():
                if effect == "total_low":
                    signals[0] += strength * 0.3
                    signals[1] += strength * 0.4
                    signals[2] += strength * 0.3
                elif effect == "total_mid":
                    signals[2] += strength * 0.4
                    signals[3] += strength * 0.4
                    signals[4] += strength * 0.2
                elif effect == "total_high":
                    signals[4] += strength * 0.3
                    signals[5] += strength * 0.3
                    signals[6] += strength * 0.2
                elif effect in ["home_goals_high", "away_goals_high"]:
                    signals[3] += strength * 0.3
                    signals[4] += strength * 0.3
                elif effect in ["home_goals_zero", "away_goals_zero"]:
                    signals[1] += strength * 0.3
                    signals[2] += strength * 0.3

        return dict(signals)

    def _freq_calibrate(self, signals: Dict, freq: Dict) -> Dict:
        """频率校准"""
        alpha = self.calib_strength
        calibrated = {}
        all_scores = set(signals.keys()) | set(freq.keys())

        for score in all_scores:
            raw = signals.get(score, 0.0)
            f = freq.get(score, 0.01)
            calibrated[score] = (1 - alpha) * raw + alpha * f

        return calibrated

    def _freq_calibrate_total(self, signals: Dict) -> Dict:
        """总进球数频率校准"""
        alpha = self.calib_strength
        calibrated = {}

        for goals in range(8):
            raw = signals.get(goals, 0.0)
            f = TOTAL_GOALS_FREQ.get(goals, 0.01)
            calibrated[goals] = (1 - alpha) * raw + alpha * f

        return calibrated


# ============================================================
# 滚动投注模拟（支持新市场和动态选场）
# ============================================================

def run_simulation_v3(predictor, match_packs, by_day, config):
    """V3版滚动投注模拟"""
    capital = INITIAL_CAPITAL

    # 配置
    use_total_goals = config.get("use_total_goals", False)  # 方向5：是否投注总进球
    dynamic_selection = config.get("dynamic_selection", False)  # 方向6：动态选场
    signal_threshold = config.get("signal_threshold", 0.15)  # 信号阈值
    min_legs = config.get("min_legs", 2)
    max_legs = config.get("max_legs", 2)

    total_cost = 0
    total_payout = 0
    n_tickets = 0
    n_hits = 0
    hit_dates = []

    capital_history = [(START_DATE, capital)]
    min_capital = capital

    for day in sorted(by_day):
        day_packs = by_day[day]

        # 预测
        for p in day_packs:
            pred = predictor.predict(p["home_data"], p["away_data"], p.get("away_id"))
            p["prediction"] = pred
            p["max_signal"] = pred["max_signal"]

        # 方向6：动态选场
        if dynamic_selection:
            # 只选信号超过阈值的场次
            qualified = [p for p in day_packs if p["max_signal"] >= signal_threshold]
            if len(qualified) < min_legs:
                continue
            # 动态腿数：信号强度决定
            avg_signal = sum(p["max_signal"] for p in qualified) / len(qualified)
            n_legs = min_legs if avg_signal < 0.25 else max_legs
            selected = sorted(qualified, key=lambda p: -p["max_signal"])[:n_legs]
        else:
            if len(day_packs) < 2:
                continue
            selected = sorted(day_packs, key=lambda p: -p["max_signal"])[:2]
            n_legs = 2

        # 比分投注
        all_picks = []
        for p in selected:
            picks = []
            for score, sig in p["prediction"]["sorted_scores"][:2]:
                odds = p["odds"].get(score, 999)
                picks.append((score, odds, p["actual"]))
            all_picks.append(picks)

        all_bets = list(product(*all_picks))
        cost = len(all_bets) * UNIT

        if capital < cost:
            continue

        capital -= cost
        total_cost += cost
        n_tickets += 1

        # 计算派彩
        payout = 0.0
        for combo in all_bets:
            all_correct = all(score == actual for score, odds, actual in combo)
            if all_correct:
                combo_odds = 1.0
                for score, odds, actual in combo:
                    combo_odds *= odds
                payout += UNIT * combo_odds

        # 方向5：总进球数额外投注
        if use_total_goals and capital >= 4:
            total_cost_extra = 0
            total_payout_extra = 0

            for p in selected[:1]:  # 只选信号最强的1场
                pred_total = p["prediction"]["sorted_total"]
                top_total = pred_total[0][0] if pred_total else 2

                # 投注总进球数（简化：买over/under 2.5）
                actual_total = p["actual"][0] + p["actual"][1]

                # 假设over2.5赔率1.8，under2.5赔率2.0
                if top_total >= 3:  # 预测大球
                    bet_cost = 2
                    if actual_total > 2:
                        total_payout_extra += 2 * 1.8
                else:  # 预测小球
                    bet_cost = 2
                    if actual_total <= 2:
                        total_payout_extra += 2 * 2.0

                total_cost_extra += bet_cost

            if capital >= total_cost_extra:
                capital -= total_cost_extra
                capital += total_payout_extra
                total_cost += total_cost_extra
                payout += total_payout_extra

        capital += payout
        total_payout += payout

        if payout > 0:
            n_hits += 1
            hit_dates.append(day)

        capital_history.append((day, capital))
        min_capital = min(min_capital, capital)

    final_capital = capital
    max_drawdown = (max(c for _, c in capital_history) - min_capital) / max(c for _, c in capital_history) if capital_history else 0

    return {
        "final_capital": final_capital,
        "profit_rate": (final_capital - INITIAL_CAPITAL) / INITIAL_CAPITAL,
        "n_tickets": n_tickets,
        "n_hits": n_hits,
        "hit_rate": n_hits / n_tickets if n_tickets > 0 else 0,
        "total_cost": total_cost,
        "total_payout": total_payout,
        "min_capital": min_capital,
        "max_drawdown": max_drawdown,
        "hit_dates": hit_dates,
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

stats = defaultdict(TeamStatsV3)
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
            "home_data": ts_to_td_v3(stats[h]),
            "away_data": ts_to_td_v3(stats[a]),
            "away_id": a,
        })

    if kind == "L":
        stats[h].add(hg, ag, True, a)
        stats[a].add(ag, hg, False, h)

by_day = defaultdict(list)
for p in match_packs:
    by_day[p["date"]].append(p)

print(f"有效预测包：{len(match_packs)}场，跨越{len(by_day)}天")


# ============================================================
# 实验配置
# ============================================================

EXPERIMENTS = {
    # 基准（v2最优配置）
    "baseline_v2": {
        "predictor_config": {"use_h2h": False},
        "sim_config": {"use_total_goals": False, "dynamic_selection": False},
    },
    # 方向4：加入H2H场景
    "exp4_h2h": {
        "predictor_config": {"use_h2h": True},
        "sim_config": {"use_total_goals": False, "dynamic_selection": False},
    },
    # 方向5：加入总进球数投注
    "exp5_total_goals": {
        "predictor_config": {"use_h2h": False},
        "sim_config": {"use_total_goals": True, "dynamic_selection": False},
    },
    # 方向6：动态选场
    "exp6_dynamic": {
        "predictor_config": {"use_h2h": False},
        "sim_config": {"use_total_goals": False, "dynamic_selection": True, "signal_threshold": 0.15},
    },
    # 方向6：更严格的动态选场
    "exp6_dynamic_strict": {
        "predictor_config": {"use_h2h": False},
        "sim_config": {"use_total_goals": False, "dynamic_selection": True, "signal_threshold": 0.20},
    },
    # 组合：H2H + 动态选场
    "exp_combo_h2h_dynamic": {
        "predictor_config": {"use_h2h": True},
        "sim_config": {"use_total_goals": False, "dynamic_selection": True, "signal_threshold": 0.15},
    },
    # 组合：H2H + 总进球
    "exp_combo_h2h_total": {
        "predictor_config": {"use_h2h": True},
        "sim_config": {"use_total_goals": True, "dynamic_selection": False},
    },
    # 全组合
    "exp_all_features": {
        "predictor_config": {"use_h2h": True},
        "sim_config": {"use_total_goals": True, "dynamic_selection": True, "signal_threshold": 0.15},
    },
}


# ============================================================
# 运行实验
# ============================================================

print("\n" + "=" * 100)
print("运行实验")
print("=" * 100)

results = {}
for exp_name, exp_config in EXPERIMENTS.items():
    print(f"  运行 {exp_name}...", end=" ")

    predictor = PredictorV3(exp_config["predictor_config"])
    result = run_simulation_v3(predictor, match_packs, by_day, exp_config["sim_config"])
    results[exp_name] = result

    print(f"完成. 最终{result['final_capital']:.1f}元, 收益{result['profit_rate']*100:+.1f}%, "
          f"命中{result['n_hits']}, 回撤{result['max_drawdown']*100:.1f}%")


# ============================================================
# 结果汇总
# ============================================================

print("\n" + "=" * 100)
print("实验结果汇总（按最终资金排序）")
print("=" * 100)

sorted_results = sorted(results.items(), key=lambda x: -x[1]["final_capital"])

print(f"\n{'实验名称':<30} {'最终资金':>10} {'收益率':>10} {'命中':>6} {'命中率':>8} {'回撤':>8}")
print("-" * 85)

for exp_name, r in sorted_results:
    mark = " ✅" if r["profit_rate"] > 0.5 else ""
    print(f"{exp_name:<30} {r['final_capital']:>10.1f} {r['profit_rate']*100:>+9.1f}% "
          f"{r['n_hits']:>6} {r['hit_rate']*100:>7.1f}% {r['max_drawdown']*100:>7.1f}%{mark}")


# ============================================================
# 最佳方案分析
# ============================================================

best_name, best_result = sorted_results[0]
print(f"\n" + "=" * 100)
print(f"最佳方案【{best_name}】详情")
print("=" * 100)

print(f"\n最终资金：{best_result['final_capital']:.1f} 元")
print(f"收益率：{best_result['profit_rate']*100:+.1f}%")
print(f"命中：{best_result['n_hits']} 次（{best_result['hit_rate']*100:.1f}%）")
print(f"最大回撤：{best_result['max_drawdown']*100:.1f}%")
print(f"命中日期：{best_result['hit_dates'][:10]}...")  # 前10个
