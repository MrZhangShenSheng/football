# -*- coding: utf-8 -*-
"""准确收益计算 —— 按实际赔率计算比分+总进球数"""
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
print("准确收益计算 —— 按实际记录赔率")
print("=" * 100)

INITIAL_CAPITAL = 1000.0
START_DATE = "2025-12-01"
UNIT = 2.0


# ============================================================
# 数据加载（包含总进球数赔率）
# ============================================================

def load_hist_with_total_goals():
    """加载历史数据，包含总进球数赔率"""
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

            # 比分赔率
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

            # 总进球数赔率（从ttg字段获取，如果有的话）
            ttg = m.get("ttg") or {}
            total_goals_odds = {}
            for k, v in ttg.items():
                try:
                    if k == "7+":
                        total_goals_odds["7+"] = float(v)
                    else:
                        total_goals_odds[str(int(k))] = float(v)
                except (ValueError, TypeError):
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
                "actual_total": h + a,
                "score_odds": score_odds,
                "total_goals_odds": total_goals_odds,
            })

    out.sort(key=lambda x: x["date"])
    return out


zh = {}
for tid, srcs in sfm.load_aliases().items():
    if srcs.get("zh"):
        zh[srcs["zh"]] = tid

tl = sfm.league_timeline()
hist = load_hist_with_total_goals()

# 统计有总进球数赔率的场次
has_ttg = sum(1 for m in hist if m["total_goals_odds"])
print(f"数据：历史{len(hist)}场，其中有总进球数赔率{has_ttg}场")


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


def ts_to_td(ts) -> TeamData:
    return TeamData(
        n=ts.n, gf=ts.gf, ga=ts.ga,
        cs=ts.cs, becs=ts.becs, btts=ts.btts, over25=ts.over25,
        gf_home=ts.gf_side[0][0], gf_home_n=ts.gf_side[0][1],
        gf_away=ts.gf_side[1][0], gf_away_n=ts.gf_side[1][1],
        ga_home=ts.ga_side[0][0], ga_home_n=ts.ga_side[0][1],
        ga_away=ts.ga_side[1][0], ga_away_n=ts.ga_side[1][1],
        recent=ts.recent.copy(),
    )


# ============================================================
# 预测器
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


class ScorePredictor:
    """比分预测器（baseline配置）"""

    def __init__(self):
        self.th = THRESHOLDS
        self.calibration_strength = 0.5

    def predict(self, home: TeamData, away: TeamData) -> List[Tuple]:
        scenes = self._analyze_scenes(home, away)
        raw_signals = self._calc_raw_signals(scenes)
        calibrated = self._freq_calibrate(raw_signals)
        return sorted(calibrated.items(), key=lambda x: -x[1])

    def _analyze_scenes(self, home: TeamData, away: TeamData) -> Dict:
        scenes = {}
        th = self.th

        s1 = {"support": defaultdict(float)}
        if home.gf_home_avg > th["gf_high"] and away.ga_away_avg > th["ga_high"]:
            s1["support"]["home_win"] = 0.4
        elif away.gf_away_avg > th["gf_high"] and home.ga_home_avg > th["ga_high"]:
            s1["support"]["away_win"] = 0.3
        elif home.gf_avg < th["gf_low"] and away.gf_avg < th["gf_low"]:
            s1["support"]["low_score"] = 0.4
            s1["support"]["draw"] = 0.3
        else:
            s1["support"]["draw"] = 0.2
        scenes["S1"] = s1

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

        s3 = {"support": defaultdict(float)}
        if home.cs_rate > th["cs_rate_high"] and away.becs_rate > th["becs_rate_high"]:
            s3["support"]["home_clean"] = 0.4
        if away.cs_rate > th["cs_rate_high"] and home.becs_rate > th["becs_rate_high"]:
            s3["support"]["away_clean"] = 0.4
        if home.becs_rate > th["becs_rate_high"] and away.becs_rate > th["becs_rate_high"]:
            s3["support"]["low_score"] = 0.3
        scenes["S3"] = s3

        s4 = {"support": defaultdict(float)}
        if home.over25_rate > th["over25_high"] and away.over25_rate > th["over25_high"]:
            s4["support"]["high_score"] = 0.4
        if home.btts_rate > th["btts_high"] and away.btts_rate > th["btts_high"]:
            s4["support"]["both_score"] = 0.3
        scenes["S4"] = s4

        return scenes

    def _calc_raw_signals(self, scenes: Dict) -> Dict[Tuple, float]:
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
        return signals

    def _freq_calibrate(self, raw: Dict) -> Dict:
        calibrated = {}
        for score in HIST_FREQ:
            r = raw.get(score, 0)
            h = HIST_FREQ[score]
            calibrated[score] = (1 - self.calibration_strength) * r + self.calibration_strength * h
        return calibrated


import math

class TotalGoalsPredictor:
    """总进球数预测器（竞彩8选项）"""

    def predict(self, home: TeamData, away: TeamData) -> List[Tuple]:
        expected_home = (home.gf_home_avg + away.ga_away_avg) / 2
        expected_away = (away.gf_away_avg + home.ga_home_avg) / 2
        expected_total = expected_home + expected_away

        probs = self._calc_poisson(expected_total)
        return sorted(probs.items(), key=lambda x: -x[1])

    def _calc_poisson(self, lam: float) -> Dict[str, float]:
        probs = {}
        for k in range(7):
            probs[str(k)] = (lam ** k) * math.exp(-lam) / math.factorial(k)
        probs["7+"] = 1 - sum(probs.values())
        return probs


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
            "actual_total": m["actual_total"],
            "score_odds": m["score_odds"],
            "total_goals_odds": m["total_goals_odds"],
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

# 统计有总进球赔率的场次
packs_with_ttg = sum(1 for p in match_packs if p["total_goals_odds"])
print(f"其中有总进球数赔率：{packs_with_ttg}场")


# ============================================================
# 滚动投注模拟（纯比分2串1双选）
# ============================================================

print("\n" + "=" * 100)
print("方案1：纯比分2串1双选（按实际赔率）")
print("=" * 100)

score_predictor = ScorePredictor()
capital = INITIAL_CAPITAL
total_cost = 0
total_payout = 0
n_tickets = 0
n_hits = 0
monthly = defaultdict(lambda: {"cost": 0, "payout": 0, "hits": 0})

for day in sorted(by_day):
    day_packs = by_day[day]
    month = day[:7]

    if len(day_packs) < 2:
        continue

    for p in day_packs:
        p["score_pred"] = score_predictor.predict(p["home_data"], p["away_data"])
        p["max_signal"] = p["score_pred"][0][1] if p["score_pred"] else 0

    selected = sorted(day_packs, key=lambda p: -p["max_signal"])[:2]

    all_picks = []
    for p in selected:
        picks = []
        for score, sig in p["score_pred"][:2]:
            # 使用实际记录的赔率
            odds = p["score_odds"].get(score, 0)
            if odds > 0:
                picks.append((score, odds, p["actual"]))
        if not picks:
            break
        all_picks.append(picks)

    if len(all_picks) < 2:
        continue

    all_bets = list(product(*all_picks))
    cost = len(all_bets) * UNIT

    payout = 0.0
    for combo in all_bets:
        all_correct = all(score == actual for score, odds, actual in combo)
        if all_correct:
            combo_odds = 1.0
            for score, odds, actual in combo:
                combo_odds *= odds
            payout += UNIT * combo_odds

    total_cost += cost
    total_payout += payout
    n_tickets += 1
    if payout > 0:
        n_hits += 1
    monthly[month]["cost"] += cost
    monthly[month]["payout"] += payout
    if payout > 0:
        monthly[month]["hits"] += 1

print(f"\n总票数：{n_tickets}")
print(f"总命中：{n_hits}（{n_hits/n_tickets*100:.1f}%）")
print(f"总成本：{total_cost:.0f} 元")
print(f"总派彩：{total_payout:.1f} 元")
print(f"净盈亏：{total_payout - total_cost:+.1f} 元")
print(f"收益率：{(total_payout - total_cost) / total_cost * 100:+.1f}%")

print(f"\n月度明细：")
print(f"{'月份':<10} {'成本':>8} {'派彩':>10} {'盈亏':>10} {'命中':>6}")
print("-" * 50)
for month in sorted(monthly):
    m = monthly[month]
    profit = m["payout"] - m["cost"]
    print(f"{month:<10} {m['cost']:>8.0f} {m['payout']:>10.1f} {profit:>+10.1f} {m['hits']:>6}")


# ============================================================
# 滚动投注模拟（纯总进球数）
# ============================================================

print("\n" + "=" * 100)
print("方案2：纯总进球数单选（按实际赔率）")
print("=" * 100)

ttg_predictor = TotalGoalsPredictor()
capital = INITIAL_CAPITAL
total_cost = 0
total_payout = 0
n_tickets = 0
n_hits = 0
monthly = defaultdict(lambda: {"cost": 0, "payout": 0, "hits": 0})

for day in sorted(by_day):
    day_packs = by_day[day]
    month = day[:7]

    for p in day_packs:
        if not p["total_goals_odds"]:
            continue

        pred = ttg_predictor.predict(p["home_data"], p["away_data"])
        top1 = pred[0][0]

        actual_total = p["actual_total"]
        actual_opt = "7+" if actual_total >= 7 else str(actual_total)

        odds = p["total_goals_odds"].get(top1, 0)
        if odds <= 0:
            continue

        cost = UNIT
        payout = 0.0
        if top1 == actual_opt:
            payout = UNIT * odds
            n_hits += 1
            monthly[month]["hits"] += 1

        total_cost += cost
        total_payout += payout
        n_tickets += 1
        monthly[month]["cost"] += cost
        monthly[month]["payout"] += payout

if n_tickets > 0:
    print(f"\n总票数：{n_tickets}")
    print(f"总命中：{n_hits}（{n_hits/n_tickets*100:.1f}%）")
    print(f"总成本：{total_cost:.0f} 元")
    print(f"总派彩：{total_payout:.1f} 元")
    print(f"净盈亏：{total_payout - total_cost:+.1f} 元")
    print(f"收益率：{(total_payout - total_cost) / total_cost * 100:+.1f}%")

    print(f"\n月度明细：")
    print(f"{'月份':<10} {'成本':>8} {'派彩':>10} {'盈亏':>10} {'命中':>6}")
    print("-" * 50)
    for month in sorted(monthly):
        m = monthly[month]
        profit = m["payout"] - m["cost"]
        print(f"{month:<10} {m['cost']:>8.0f} {m['payout']:>10.1f} {profit:>+10.1f} {m['hits']:>6}")
else:
    print("\n没有总进球数赔率数据，无法计算")


print("\n" + "=" * 100)
print("完成")
print("=" * 100)
