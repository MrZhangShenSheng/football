# -*- coding: utf-8 -*-
"""exp6 配置多切分点稳定性验证"""
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import product
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, str(Path("engine/scripts/research")))

import score_family_model as sfm

print("=" * 100)
print("exp6（放宽阈值+频率校准）—— 多切分点稳定性验证")
print("=" * 100)

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
        n=ts.n, gf=ts.gf, ga=ts.ga, win=ts.win,
        cs=ts.cs, becs=ts.becs, btts=ts.btts, over25=ts.over25,
        gf_home=ts.gf_side[0][0], gf_home_n=ts.gf_side[0][1],
        gf_away=ts.gf_side[1][0], gf_away_n=ts.gf_side[1][1],
        ga_home=ts.ga_side[0][0], ga_home_n=ts.ga_side[0][1],
        ga_away=ts.ga_side[1][0], ga_away_n=ts.ga_side[1][1],
        recent_gd=ts.recent_gd.copy(), recent=ts.recent.copy(),
    )


# ============================================================
# exp6 预测器（放宽阈值 + 频率校准）
# ============================================================

# 历史频率
HIST_FREQ = {
    (1, 1): 0.12, (2, 1): 0.09, (1, 0): 0.09, (1, 2): 0.08,
    (0, 1): 0.07, (0, 0): 0.07, (2, 0): 0.07, (2, 2): 0.05,
    (0, 2): 0.05, (3, 0): 0.04, (3, 1): 0.04, (3, 2): 0.03,
    (1, 3): 0.03, (2, 3): 0.02, (4, 0): 0.02, (4, 1): 0.02,
    (0, 3): 0.02, (3, 3): 0.01,
}

# 放宽后的阈值
RELAXED_TH = {
    "gf_low": 1.2, "gf_high": 1.5,
    "ga_low": 1.0, "ga_high": 1.3,
    "cs_rate_high": 0.25,
    "becs_rate_high": 0.25,
    "btts_low": 0.45, "btts_high": 0.50,
    "over25_low": 0.45, "over25_high": 0.50,
    "form_good": 6, "form_bad": 4,
    "fragility_high": 1.5, "fragility_low": 1.0,
}


class Exp6Predictor:
    """exp6 预测器：放宽阈值 + 频率校准"""

    def __init__(self):
        self.th = RELAXED_TH
        self.calibration_strength = 0.7

    def predict(self, home: TeamData, away: TeamData) -> Dict:
        """预测所有比分的信号"""

        # 场景分析
        scenes = self._analyze_scenes(home, away)

        # 计算原始信号
        raw_signals = self._calc_raw_signals(scenes)

        # 频率校准
        calibrated = self._freq_calibrate(raw_signals)

        # 排序
        sorted_scores = sorted(calibrated.items(), key=lambda x: -x[1])

        return {
            "predictions": calibrated,
            "sorted_scores": sorted_scores,
            "scenes": scenes,
        }

    def _analyze_scenes(self, home: TeamData, away: TeamData) -> Dict:
        """分析各场景"""
        scenes = {}
        th = self.th

        # S1: 攻防对比
        home_attack = home.gf_home_avg
        away_attack = away.gf_away_avg
        home_defense = home.ga_home_avg
        away_defense = away.ga_away_avg

        s1 = {"home_attack": 0, "away_attack": 0, "high_scoring": 0, "low_scoring": 0}
        if home_attack > th["gf_high"] and away_defense > th["ga_high"]:
            s1["home_attack"] = 1
        if away_attack > th["gf_high"] and home_defense > th["ga_high"]:
            s1["away_attack"] = 1
        if home_attack > th["gf_high"] and away_attack > th["gf_high"]:
            s1["high_scoring"] = 1
        if home_attack < th["gf_low"] and away_attack < th["gf_low"]:
            s1["low_scoring"] = 1
        scenes["S1"] = s1

        # S2: 近期状态
        home_form = home.form_score()
        away_form = away.form_score()

        s2 = {"home_strong": 0, "away_strong": 0, "both_weak": 0}
        if home_form >= th["form_good"] and away_form <= th["form_bad"]:
            s2["home_strong"] = 1
        if away_form >= th["form_good"] and home_form <= th["form_bad"]:
            s2["away_strong"] = 1
        if home_form <= th["form_bad"] and away_form <= th["form_bad"]:
            s2["both_weak"] = 1
        scenes["S2"] = s2

        # S3: 零封特征
        s3 = {"home_clean": 0, "away_clean": 0, "zero_zero": 0}
        if home.cs_rate > th["cs_rate_high"] and away.becs_rate > th["becs_rate_high"]:
            s3["home_clean"] = 1
        if away.cs_rate > th["cs_rate_high"] and home.becs_rate > th["becs_rate_high"]:
            s3["away_clean"] = 1
        if home.becs_rate > th["becs_rate_high"] and away.becs_rate > th["becs_rate_high"]:
            s3["zero_zero"] = 1
        scenes["S3"] = s3

        # S4: 大球特征
        s4 = {"high_btts": 0, "high_over25": 0, "low_btts": 0}
        if home.btts_rate > th["btts_high"] and away.btts_rate > th["btts_high"]:
            s4["high_btts"] = 1
        if home.over25_rate > th["over25_high"] and away.over25_rate > th["over25_high"]:
            s4["high_over25"] = 1
        if home.btts_rate < th["btts_low"] and away.btts_rate < th["btts_low"]:
            s4["low_btts"] = 1
        scenes["S4"] = s4

        return scenes

    def _calc_raw_signals(self, scenes: Dict) -> Dict[Tuple, float]:
        """计算原始信号"""
        signals = defaultdict(float)

        s1 = scenes["S1"]
        s2 = scenes["S2"]
        s3 = scenes["S3"]
        s4 = scenes["S4"]

        # 主胜零封
        if s1["home_attack"] or s2["home_strong"] or s3["home_clean"]:
            signals[(1, 0)] += 0.3 * (s1["home_attack"] + s2["home_strong"] + s3["home_clean"])
            signals[(2, 0)] += 0.2 * (s1["home_attack"] + s2["home_strong"] + s3["home_clean"])
            signals[(3, 0)] += 0.1 * (s1["home_attack"] + s2["home_strong"])

        # 客胜零封
        if s1["away_attack"] or s2["away_strong"] or s3["away_clean"]:
            signals[(0, 1)] += 0.3 * (s1["away_attack"] + s2["away_strong"] + s3["away_clean"])
            signals[(0, 2)] += 0.2 * (s1["away_attack"] + s2["away_strong"] + s3["away_clean"])
            signals[(0, 3)] += 0.1 * (s1["away_attack"] + s2["away_strong"])

        # 0:0
        if s3["zero_zero"] or s1["low_scoring"] or s2["both_weak"]:
            signals[(0, 0)] += 0.4 * (s3["zero_zero"] + s1["low_scoring"] + s2["both_weak"])

        # 平局
        if not (s2["home_strong"] or s2["away_strong"]):
            signals[(1, 1)] += 0.3
            signals[(2, 2)] += 0.1

        # 高比分
        if s1["high_scoring"] or s4["high_btts"] or s4["high_over25"]:
            mult = s1["high_scoring"] + s4["high_btts"] + s4["high_over25"]
            signals[(2, 1)] += 0.2 * mult
            signals[(1, 2)] += 0.2 * mult
            signals[(2, 2)] += 0.15 * mult
            signals[(3, 1)] += 0.1 * mult
            signals[(1, 3)] += 0.1 * mult
            signals[(3, 2)] += 0.1 * mult
            signals[(2, 3)] += 0.1 * mult

        # 主胜不零封
        if s1["home_attack"] and s4["high_btts"]:
            signals[(2, 1)] += 0.3
            signals[(3, 1)] += 0.2
            signals[(3, 2)] += 0.15

        # 客胜不零封
        if s1["away_attack"] and s4["high_btts"]:
            signals[(1, 2)] += 0.3
            signals[(1, 3)] += 0.2
            signals[(2, 3)] += 0.15

        return dict(signals)

    def _freq_calibrate(self, raw_signals: Dict[Tuple, float]) -> Dict[Tuple, float]:
        """频率校准"""
        calibrated = {}

        for score, freq in HIST_FREQ.items():
            raw = raw_signals.get(score, 0)
            # 混合：原始信号 + 历史频率
            calibrated[score] = raw * (1 - self.calibration_strength) + freq * self.calibration_strength

        return calibrated


# ============================================================
# 回测函数
# ============================================================

def run_backtest(cut_date: str) -> Dict:
    """运行单个切分点的回测"""

    # 构建盲测数据
    blind = []
    for m in hist:
        if m["date"] < cut_date:
            continue
        hid, aid = zh.get(m["home_zh"]), zh.get(m["away_zh"])
        if hid and aid:
            blind.append({**m, "hid": hid, "aid": aid})

    if len(blind) < 100:
        return None

    # 合并时间线
    merged = [("L", d, h, a, hg, ag, None) for d, h, a, hg, ag in tl]
    merged += [("B", m["date"], m["hid"], m["aid"], m["actual"][0], m["actual"][1], i)
               for i, m in enumerate(blind)]
    merged.sort(key=lambda r: (r[1], 0 if r[0] == "L" else 1))

    # 滚动统计
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

            match_packs.append({
                "date": m["date"],
                "actual": m["actual"],
                "odds": m["odds"],
                "sorted_scores": prediction["sorted_scores"],
            })

        if kind == "L":
            stats[h].add(hg, ag, True, a)
            stats[a].add(ag, hg, False, h)

    # 按日期分组
    by_day = defaultdict(list)
    for p in match_packs:
        by_day[p["date"]].append(p)

    # 2串1双选模拟
    n_legs = 2
    k_picks = 2

    total_cost = 0.0
    total_payout = 0.0
    n_tickets = 0
    n_hits = 0

    for day in sorted(by_day):
        day_packs = by_day[day]
        if len(day_packs) < n_legs:
            continue

        # 按信号排序选场
        selected = sorted(day_packs, key=lambda p: -p["sorted_scores"][0][1] if p["sorted_scores"] else 0)[:n_legs]

        all_picks = []
        for p in selected:
            picks = []
            for score, signal in p["sorted_scores"][:k_picks]:
                odds = p["odds"].get(score, 999)
                picks.append((score, odds, p["actual"]))
            all_picks.append(picks)

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

    profit = (total_payout - total_cost) / total_cost if total_cost > 0 else -1

    return {
        "n_blind": len(blind),
        "n_tickets": n_tickets,
        "n_hits": n_hits,
        "total_cost": total_cost,
        "total_payout": total_payout,
        "profit": profit,
    }


# ============================================================
# 运行多切分点验证
# ============================================================

print("\n" + "=" * 100)
print("多切分点稳定性验证")
print("=" * 100)

cuts = [
    "2025-12-01",
    "2026-01-01",
    "2026-02-01",
    "2026-03-01",
    "2026-04-01",
    "2026-05-01",
    "2026-06-01",
    "2026-07-01",
]

results = {}
for cut in cuts:
    r = run_backtest(cut)
    if r:
        results[cut] = r
        profit_str = f"{r['profit']*100:+.1f}%"
        mark = "✅" if r['profit'] > 0 else ""
        print(f"  {cut}: 盲测{r['n_blind']:>4}场, 票数{r['n_tickets']:>3}, 命中{r['n_hits']:>2}, 盈利率{profit_str:>8} {mark}")

# 汇总
print("\n" + "=" * 100)
print("汇总")
print("=" * 100)

n_positive = sum(1 for r in results.values() if r['profit'] > 0)
total_tickets = sum(r['n_tickets'] for r in results.values())
total_hits = sum(r['n_hits'] for r in results.values())
total_cost = sum(r['total_cost'] for r in results.values())
total_payout = sum(r['total_payout'] for r in results.values())
total_profit = (total_payout - total_cost) / total_cost if total_cost > 0 else -1

print(f"正收益切分点：{n_positive}/{len(results)}")
print(f"总票数：{total_tickets}")
print(f"总命中：{total_hits}")
print(f"总成本：{total_cost:.0f}")
print(f"总派彩：{total_payout:.1f}")
print(f"总盈利率：{total_profit*100:+.1f}%")


# ============================================================
# 与其他方案对比
# ============================================================

print("\n" + "=" * 100)
print("与其他方案对比")
print("=" * 100)

print(f"\n{'方案':<25} {'正收益率':<12} {'总票数':<10} {'总命中':<10} {'总盈利率':<12}")
print("-" * 70)
print(f"{'exp6(放宽阈值+频率校准)':<25} {n_positive}/{len(results):<10} {total_tickets:<10} {total_hits:<10} {total_profit*100:+.1f}%")
print(f"{'原多场景2串1双选':<25} {'8/8':<10} {1010:<10} {40:<10} {'+16.69%':<12}")
print(f"{'族模型基准':<25} {'4/8':<10} {1133:<10} {46:<10} {'-3.36%':<12}")
