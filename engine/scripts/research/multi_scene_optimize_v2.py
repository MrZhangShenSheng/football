# -*- coding: utf-8 -*-
"""多场景模型优化 —— 寻找命中分布均匀的配置"""
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
print("多场景模型优化 —— 寻找命中分布均匀的配置")
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


# ============================================================
# 可配置预测器
# ============================================================

class ConfigPredictor:
    def __init__(self, config: Dict):
        self.config = config
        self.th = config.get("thresholds", {})
        self.calib_strength = config.get("calibration_strength", 0.5)
        self.score_diversity = config.get("score_diversity", 0.0)  # 新增：分数多样性

    def predict(self, home: TeamData, away: TeamData) -> Dict:
        scenes = self._analyze_scenes(home, away)
        raw_signals = self._calc_raw_signals(scenes)
        calibrated = self._freq_calibrate(raw_signals)

        # 新增：分数多样性调整
        if self.score_diversity > 0:
            calibrated = self._add_diversity(calibrated)

        sorted_scores = sorted(calibrated.items(), key=lambda x: -x[1])
        return {"predictions": calibrated, "sorted_scores": sorted_scores}

    def _analyze_scenes(self, home: TeamData, away: TeamData) -> Dict:
        scenes = {}
        th = self.th

        gf_low = th.get("gf_low", 1.0)
        gf_high = th.get("gf_high", 1.8)
        ga_low = th.get("ga_low", 0.8)
        ga_high = th.get("ga_high", 1.5)

        # S1: 攻防
        s1 = {"support": defaultdict(float)}
        if home.gf_home_avg > gf_high and away.ga_away_avg > ga_high:
            s1["support"]["home_win"] = 0.4
        elif away.gf_away_avg > gf_high and home.ga_home_avg > ga_high:
            s1["support"]["away_win"] = 0.3
        elif home.gf_avg < gf_low and away.gf_avg < gf_low:
            s1["support"]["low_score"] = 0.4
            s1["support"]["draw"] = 0.3
        else:
            s1["support"]["draw"] = 0.2
        scenes["S1"] = s1

        # S2: 状态
        s2 = {"support": defaultdict(float)}
        home_form = home.form_score()
        away_form = away.form_score()
        form_good = th.get("form_good", 7)
        form_bad = th.get("form_bad", 3)

        if home_form >= form_good and away_form <= form_bad:
            s2["support"]["home_win"] = 0.4
        elif away_form >= form_good and home_form <= form_bad:
            s2["support"]["away_win"] = 0.4
        elif home_form <= form_bad and away_form <= form_bad:
            s2["support"]["low_score"] = 0.3
            s2["support"]["draw"] = 0.3
        else:
            s2["support"]["draw"] = 0.2
        scenes["S2"] = s2

        # S3: 零封
        s3 = {"support": defaultdict(float)}
        cs_high = th.get("cs_rate_high", 0.35)
        becs_high = th.get("becs_rate_high", 0.35)

        if home.cs_rate > cs_high and away.becs_rate > becs_high:
            s3["support"]["home_clean"] = 0.4
        if away.cs_rate > cs_high and home.becs_rate > becs_high:
            s3["support"]["away_clean"] = 0.4
        if home.becs_rate > becs_high and away.becs_rate > becs_high:
            s3["support"]["low_score"] = 0.3
        scenes["S3"] = s3

        # S4: 大球
        s4 = {"support": defaultdict(float)}
        over25_high = th.get("over25_high", 0.55)
        btts_high = th.get("btts_high", 0.55)

        if home.over25_rate > over25_high and away.over25_rate > over25_high:
            s4["support"]["high_score"] = 0.4
        if home.btts_rate > btts_high and away.btts_rate > btts_high:
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
                    signals[(2, 1)] += strength * 0.25
                    signals[(1, 2)] += strength * 0.25
                    signals[(2, 2)] += strength * 0.2

        return dict(signals)

    def _freq_calibrate(self, signals: Dict) -> Dict:
        calibrated = {}
        for score, sig in signals.items():
            freq = HIST_FREQ.get(score, 0.01)
            calibrated[score] = sig * (1 - self.calib_strength) + freq * self.calib_strength

        # 补充未覆盖的比分
        for score, freq in HIST_FREQ.items():
            if score not in calibrated:
                calibrated[score] = freq * self.calib_strength

        return calibrated

    def _add_diversity(self, signals: Dict) -> Dict:
        """增加预测多样性，避免过度集中"""
        if not signals:
            return signals

        max_sig = max(signals.values())
        min_sig = min(signals.values())
        range_sig = max_sig - min_sig if max_sig > min_sig else 1

        # 压缩信号范围，让差距变小
        compressed = {}
        for score, sig in signals.items():
            # 将信号压缩到更窄的范围
            normalized = (sig - min_sig) / range_sig
            compressed[score] = min_sig + normalized * range_sig * (1 - self.score_diversity)

        return compressed


# ============================================================
# 滚动投注模拟函数
# ============================================================

def run_roll_simulation(predictor, match_packs, by_day):
    """运行滚动投注模拟，返回详细结果"""
    capital = INITIAL_CAPITAL
    monthly_hits = defaultdict(int)
    monthly_cost = defaultdict(float)
    monthly_payout = defaultdict(float)

    total_cost = 0
    total_payout = 0
    n_tickets = 0
    n_hits = 0
    hit_dates = []

    capital_history = [(START_DATE, capital)]
    min_capital = capital
    max_capital = capital

    for day in sorted(by_day):
        day_packs = by_day[day]
        month = day[:7]

        if len(day_packs) < 2:
            continue

        # 选场：按信号排序取前2场
        for p in day_packs:
            home_data, away_data = p["home_data"], p["away_data"]
            pred = predictor.predict(home_data, away_data)
            p["sorted_scores"] = pred["sorted_scores"]
            p["max_signal"] = pred["sorted_scores"][0][1] if pred["sorted_scores"] else 0

        selected = sorted(day_packs, key=lambda p: -p["max_signal"])[:2]

        # 每场选2个比分
        all_picks = []
        for p in selected:
            picks = []
            for score, sig in p["sorted_scores"][:2]:
                odds = p["odds"].get(score, 999)
                picks.append((score, odds, p["actual"]))
            all_picks.append(picks)

        all_bets = list(product(*all_picks))
        cost = len(all_bets) * UNIT

        if capital < cost:
            continue

        capital -= cost
        total_cost += cost
        monthly_cost[month] += cost
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

        capital += payout
        total_payout += payout
        monthly_payout[month] += payout

        if payout > 0:
            n_hits += 1
            monthly_hits[month] += 1
            hit_dates.append(day)

        capital_history.append((day, capital))
        min_capital = min(min_capital, capital)
        max_capital = max(max_capital, capital)

    # 计算命中分布均匀度（月度命中的标准差）
    months = sorted(set(d[:7] for d in by_day.keys()))
    hits_per_month = [monthly_hits.get(m, 0) for m in months]
    avg_hits = sum(hits_per_month) / len(hits_per_month) if hits_per_month else 0
    hit_std = (sum((h - avg_hits) ** 2 for h in hits_per_month) / len(hits_per_month)) ** 0.5 if hits_per_month else 0

    # 最大回撤
    max_drawdown = (max_capital - min_capital) / max_capital if max_capital > 0 else 0

    return {
        "final_capital": capital,
        "total_return": (capital - INITIAL_CAPITAL) / INITIAL_CAPITAL,
        "n_tickets": n_tickets,
        "n_hits": n_hits,
        "hit_rate": n_hits / n_tickets if n_tickets > 0 else 0,
        "total_cost": total_cost,
        "total_payout": total_payout,
        "min_capital": min_capital,
        "max_capital": max_capital,
        "max_drawdown": max_drawdown,
        "hit_std": hit_std,
        "hits_per_month": dict(monthly_hits),
        "hit_dates": hit_dates,
    }


# ============================================================
# 准备数据
# ============================================================

# 构建盲测数据
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
# 实验配置
# ============================================================

EXPERIMENTS = {
    # 原多场景（基准）
    "baseline": {
        "thresholds": {
            "gf_low": 1.0, "gf_high": 1.8,
            "ga_low": 0.8, "ga_high": 1.5,
            "cs_rate_high": 0.35, "becs_rate_high": 0.35,
            "btts_high": 0.55, "over25_high": 0.55,
            "form_good": 7, "form_bad": 3,
        },
        "calibration_strength": 0.5,
        "score_diversity": 0.0,
    },
    # exp6 原版
    "exp6_original": {
        "thresholds": {
            "gf_low": 1.2, "gf_high": 1.5,
            "ga_low": 1.0, "ga_high": 1.3,
            "cs_rate_high": 0.25, "becs_rate_high": 0.25,
            "btts_high": 0.50, "over25_high": 0.50,
            "form_good": 6, "form_bad": 4,
        },
        "calibration_strength": 0.7,
        "score_diversity": 0.0,
    },
    # exp6 + 多样性
    "exp6_diverse_0.2": {
        "thresholds": {
            "gf_low": 1.2, "gf_high": 1.5,
            "ga_low": 1.0, "ga_high": 1.3,
            "cs_rate_high": 0.25, "becs_rate_high": 0.25,
            "btts_high": 0.50, "over25_high": 0.50,
            "form_good": 6, "form_bad": 4,
        },
        "calibration_strength": 0.7,
        "score_diversity": 0.2,
    },
    # 弱校准
    "exp_calib_0.3": {
        "thresholds": {
            "gf_low": 1.2, "gf_high": 1.5,
            "ga_low": 1.0, "ga_high": 1.3,
            "cs_rate_high": 0.25, "becs_rate_high": 0.25,
            "btts_high": 0.50, "over25_high": 0.50,
            "form_good": 6, "form_bad": 4,
        },
        "calibration_strength": 0.3,
        "score_diversity": 0.0,
    },
    # 中等校准
    "exp_calib_0.5": {
        "thresholds": {
            "gf_low": 1.2, "gf_high": 1.5,
            "ga_low": 1.0, "ga_high": 1.3,
            "cs_rate_high": 0.25, "becs_rate_high": 0.25,
            "btts_high": 0.50, "over25_high": 0.50,
            "form_good": 6, "form_bad": 4,
        },
        "calibration_strength": 0.5,
        "score_diversity": 0.0,
    },
    # 混合：放宽阈值 + 弱校准 + 多样性
    "exp_mixed": {
        "thresholds": {
            "gf_low": 1.1, "gf_high": 1.6,
            "ga_low": 0.9, "ga_high": 1.4,
            "cs_rate_high": 0.30, "becs_rate_high": 0.30,
            "btts_high": 0.52, "over25_high": 0.52,
            "form_good": 6, "form_bad": 4,
        },
        "calibration_strength": 0.5,
        "score_diversity": 0.1,
    },
    # 保守阈值 + 强校准
    "exp_conservative": {
        "thresholds": {
            "gf_low": 1.0, "gf_high": 1.8,
            "ga_low": 0.8, "ga_high": 1.5,
            "cs_rate_high": 0.35, "becs_rate_high": 0.35,
            "btts_high": 0.55, "over25_high": 0.55,
            "form_good": 7, "form_bad": 3,
        },
        "calibration_strength": 0.6,
        "score_diversity": 0.0,
    },
}


# ============================================================
# 运行实验
# ============================================================

print("\n" + "=" * 100)
print("运行滚动投注模拟实验")
print("=" * 100)

results = {}

for exp_name, config in EXPERIMENTS.items():
    print(f"  运行 {exp_name}...", end=" ", flush=True)
    predictor = ConfigPredictor(config)
    result = run_roll_simulation(predictor, match_packs, by_day)
    results[exp_name] = result
    print(f"完成. 最终{result['final_capital']:.1f}元, 收益{result['total_return']*100:+.1f}%, "
          f"命中{result['n_hits']}, 回撤{result['max_drawdown']*100:.1f}%")


# ============================================================
# 结果汇总
# ============================================================

print("\n" + "=" * 100)
print("实验结果汇总（按最终资金排序）")
print("=" * 100)

print(f"\n{'实验名称':<25} {'最终资金':>10} {'收益率':>10} {'命中':>6} {'命中率':>8} {'最低':>10} {'回撤':>8} {'命中分布':>10}")
print("-" * 105)

sorted_results = sorted(results.items(), key=lambda x: -x[1]["final_capital"])

for exp_name, r in sorted_results:
    mark = "✅" if r["total_return"] > 0 else ""
    print(f"{exp_name:<25} {r['final_capital']:>10.1f} {r['total_return']*100:>+9.1f}% "
          f"{r['n_hits']:>6} {r['hit_rate']*100:>7.1f}% {r['min_capital']:>10.1f} "
          f"{r['max_drawdown']*100:>7.1f}% {r['hit_std']:>10.2f} {mark}")


# ============================================================
# 月度命中分布对比
# ============================================================

print("\n" + "=" * 100)
print("月度命中分布对比（Top3方案）")
print("=" * 100)

top3 = sorted_results[:3]
months = sorted(set(d[:7] for d in by_day.keys()))

print(f"\n{'月份':<10}", end="")
for exp_name, _ in top3:
    print(f"{exp_name[:15]:>18}", end="")
print()
print("-" * 70)

for month in months:
    print(f"{month:<10}", end="")
    for exp_name, r in top3:
        hits = r["hits_per_month"].get(month, 0)
        print(f"{hits:>18}", end="")
    print()


# ============================================================
# 最佳方案命中明细
# ============================================================

best_name, best_result = sorted_results[0]

print("\n" + "=" * 100)
print(f"最佳方案【{best_name}】命中明细")
print("=" * 100)

print(f"\n命中日期：{best_result['hit_dates']}")
print(f"\n月度命中：{dict(best_result['hits_per_month'])}")
