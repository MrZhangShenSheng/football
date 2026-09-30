# -*- coding: utf-8 -*-
"""exp6 配置滚动投注模拟"""
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
print("exp6（放宽阈值+频率校准）—— 滚动投注模拟")
print("假设：2025-12-01 起始资金 1000 元，派奖复购")
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
# exp6 预测器
# ============================================================

HIST_FREQ = {
    (1, 1): 0.12, (2, 1): 0.09, (1, 0): 0.09, (1, 2): 0.08,
    (0, 1): 0.07, (0, 0): 0.07, (2, 0): 0.07, (2, 2): 0.05,
    (0, 2): 0.05, (3, 0): 0.04, (3, 1): 0.04, (3, 2): 0.03,
    (1, 3): 0.03, (2, 3): 0.02, (4, 0): 0.02, (4, 1): 0.02,
    (0, 3): 0.02, (3, 3): 0.01,
}

RELAXED_TH = {
    "gf_low": 1.2, "gf_high": 1.5,
    "ga_low": 1.0, "ga_high": 1.3,
    "cs_rate_high": 0.25, "becs_rate_high": 0.25,
    "btts_low": 0.45, "btts_high": 0.50,
    "over25_low": 0.45, "over25_high": 0.50,
    "form_good": 6, "form_bad": 4,
    "fragility_high": 1.5, "fragility_low": 1.0,
}


class Exp6Predictor:
    def __init__(self):
        self.th = RELAXED_TH
        self.calibration_strength = 0.7

    def predict(self, home: TeamData, away: TeamData) -> Dict:
        scenes = self._analyze_scenes(home, away)
        raw_signals = self._calc_raw_signals(scenes)
        calibrated = self._freq_calibrate(raw_signals)
        sorted_scores = sorted(calibrated.items(), key=lambda x: -x[1])
        return {"predictions": calibrated, "sorted_scores": sorted_scores}

    def _analyze_scenes(self, home: TeamData, away: TeamData) -> Dict:
        scenes = {}
        th = self.th

        # S1: 攻防
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
        if away.cs_rate > th["cs_rate_high"] and home.becs_rate > th["becs_rate_high"]:
            s3["support"]["away_clean"] = 0.4
        if home.becs_rate > 0.3 and away.becs_rate > 0.3:
            s3["support"]["zero_zero"] = 0.3
        scenes["S3"] = s3

        # S4: 大球
        s4 = {"support": defaultdict(float)}
        if home.over25_rate > th["over25_high"] and away.over25_rate > th["over25_high"]:
            s4["support"]["high_score"] = 0.4
        elif home.over25_rate < th["over25_low"] and away.over25_rate < th["over25_low"]:
            s4["support"]["low_score"] = 0.4
        scenes["S4"] = s4

        return scenes

    def _calc_raw_signals(self, scenes: Dict) -> Dict[Tuple, float]:
        signals = defaultdict(float)
        score_mapping = {
            "home_win": [(1, 0), (2, 0), (2, 1), (3, 0), (3, 1)],
            "away_win": [(0, 1), (0, 2), (1, 2), (0, 3), (1, 3)],
            "draw": [(0, 0), (1, 1), (2, 2)],
            "low_score": [(0, 0), (1, 0), (0, 1), (1, 1)],
            "high_score": [(3, 2), (2, 3), (3, 3), (4, 1), (1, 4), (4, 2), (2, 4)],
            "home_clean": [(1, 0), (2, 0), (3, 0), (4, 0)],
            "away_clean": [(0, 1), (0, 2), (0, 3), (0, 4)],
            "zero_zero": [(0, 0)],
            "home_goals_2plus": [(2, 0), (2, 1), (3, 0), (3, 1), (3, 2)],
            "away_goals_2plus": [(0, 2), (1, 2), (0, 3), (1, 3), (2, 3)],
        }

        for scene_id, scene in scenes.items():
            for hint, strength in scene["support"].items():
                if hint in score_mapping:
                    for score in score_mapping[hint]:
                        signals[score] += strength / len(score_mapping[hint])

        return dict(signals)

    def _freq_calibrate(self, raw_signals: Dict) -> Dict[Tuple, float]:
        calibrated = {}
        for score in HIST_FREQ.keys():
            raw = raw_signals.get(score, 0.0)
            freq = HIST_FREQ.get(score, 0.01)
            calibrated[score] = raw * (1 - self.calibration_strength) + freq * self.calibration_strength
        return calibrated


# ============================================================
# 构建预测包
# ============================================================

blind = []
for m in hist:
    if m["date"] < START_DATE:
        continue
    hid, aid = zh.get(m["home_zh"]), zh.get(m["away_zh"])
    if hid and aid:
        blind.append({**m, "hid": hid, "aid": aid})

print(f"盲测样本（>={START_DATE}）：{len(blind)} 场")

merged = [("L", d, h, a, hg, ag, None) for d, h, a, hg, ag in tl]
merged += [("B", m["date"], m["hid"], m["aid"], m["actual"][0], m["actual"][1], i)
           for i, m in enumerate(blind)]
merged.sort(key=lambda r: (r[1], 0 if r[0] == "L" else 1))

stats = defaultdict(sfm.TeamStats)
predictor = Exp6Predictor()
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
        prediction = predictor.predict(home_data, away_data)

        sorted_scores = prediction["sorted_scores"]
        max_signal = sorted_scores[0][1] if sorted_scores else 0

        match_packs.append({
            "date": m["date"],
            "actual": m["actual"],
            "odds": m["odds"],
            "sorted_scores": sorted_scores,
            "max_signal": max_signal,
            "home": m["home_zh"],
            "away": m["away_zh"],
        })

    if kind == "L":
        stats[h].add(hg, ag, True, a)
        stats[a].add(ag, hg, False, h)

print(f"有效预测包：{len(match_packs)} 场")

by_day = defaultdict(list)
for p in match_packs:
    by_day[p["date"]].append(p)

print(f"跨越 {len(by_day)} 天")


# ============================================================
# 滚动投注模拟
# ============================================================

print("\n" + "=" * 100)
print("滚动投注明细（2串1双选）")
print("=" * 100)

capital = INITIAL_CAPITAL
n_legs = 2
k_picks = 2

daily_log = []
monthly_summary = defaultdict(lambda: {"start": 0, "end": 0, "tickets": 0, "hits": 0, "cost": 0, "payout": 0})

print(f"\n{'日期':<12} {'票型':<15} {'成本':>8} {'派彩':>10} {'盈亏':>10} {'余额':>10} {'备注'}")
print("-" * 90)

prev_month = None

for day in sorted(by_day):
    day_packs = by_day[day]

    month = day[:7]
    if month != prev_month:
        if prev_month:
            monthly_summary[prev_month]["end"] = capital
        monthly_summary[month]["start"] = capital
        prev_month = month

    if len(day_packs) < n_legs:
        continue

    selected = sorted(day_packs, key=lambda p: -p["max_signal"])[:n_legs]

    all_picks = []
    for p in selected:
        picks = []
        for score, sig in p["sorted_scores"][:k_picks]:
            odds = p["odds"].get(score, 999)
            picks.append((score, odds, p["actual"]))
        all_picks.append(picks)

    all_bets = list(product(*all_picks))
    cost = len(all_bets) * UNIT

    if capital < cost:
        print(f"{day:<12} {'资金不足':<15} {cost:>8.0f} {'-':>10} {'-':>10} {capital:>10.1f} 跳过")
        continue

    capital -= cost

    payout = 0.0
    hit_scores = None
    for combo in all_bets:
        all_correct = all(score == actual for score, odds, actual in combo)
        if all_correct:
            combo_odds = 1.0
            for score, odds, actual in combo:
                combo_odds *= odds
            payout += UNIT * combo_odds
            hit_scores = [score for score, odds, actual in combo]

    capital += payout
    profit = payout - cost

    monthly_summary[month]["tickets"] += 1
    monthly_summary[month]["cost"] += cost
    monthly_summary[month]["payout"] += payout
    if payout > 0:
        monthly_summary[month]["hits"] += 1

    if payout > 0:
        print(f"{day:<12} {'2串1×4注':<15} {cost:>8.0f} {payout:>10.1f} {profit:>+10.1f} {capital:>10.1f} ✅ 中! {hit_scores}")
    else:
        daily_log.append((day, cost, payout, profit, capital))

if prev_month:
    monthly_summary[prev_month]["end"] = capital

# 打印部分未命中日期
print(f"\n... 省略 {len(daily_log)} 天未命中记录 ...")


# ============================================================
# 月度汇总
# ============================================================

print("\n" + "=" * 100)
print("月度汇总")
print("=" * 100)

print(f"\n{'月份':<12} {'月初余额':>12} {'票数':>8} {'命中':>8} {'成本':>12} {'派彩':>12} {'盈亏':>12} {'月末余额':>12} {'月收益率':>12}")
print("-" * 110)

for month in sorted(monthly_summary.keys()):
    s = monthly_summary[month]
    profit = s["payout"] - s["cost"]
    rate = profit / s["start"] * 100 if s["start"] > 0 else 0
    mark = "✅" if profit > 0 else ""
    print(f"{month:<12} {s['start']:>12.1f} {s['tickets']:>8} {s['hits']:>8} {s['cost']:>12.0f} {s['payout']:>12.1f} {profit:>+12.1f} {s['end']:>12.1f} {rate:>+11.1f}% {mark}")


# ============================================================
# 最终结果
# ============================================================

print("\n" + "=" * 100)
print("最终结果")
print("=" * 100)

total_tickets = sum(s["tickets"] for s in monthly_summary.values())
total_hits = sum(s["hits"] for s in monthly_summary.values())
total_cost = sum(s["cost"] for s in monthly_summary.values())
total_payout = sum(s["payout"] for s in monthly_summary.values())

print(f"""
起始日期：{START_DATE}
起始资金：{INITIAL_CAPITAL:.0f} 元
结束资金：{capital:.1f} 元
总盈亏：  {capital - INITIAL_CAPITAL:+.1f} 元
总收益率：{(capital - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100:+.1f}%

总票数：  {total_tickets}
总命中：  {total_hits}
命中率：  {total_hits / total_tickets * 100:.1f}%

总投入：  {total_cost:.0f} 元
总派彩：  {total_payout:.1f} 元
净盈亏：  {total_payout - total_cost:+.1f} 元（投入口径）
""")


# ============================================================
# 与原方案对比
# ============================================================

print("=" * 100)
print("与原方案对比")
print("=" * 100)

print(f"""
                        exp6方案         原多场景方案
起始资金：               1000 元          1000 元
结束资金：               {capital:.1f} 元         1190.2 元
总收益率：               {(capital - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100:+.1f}%           +19.0%
总命中：                 {total_hits}              9
命中率：                 {total_hits / total_tickets * 100:.1f}%             4.2%
""")
