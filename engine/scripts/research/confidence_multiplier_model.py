# -*- coding: utf-8 -*-
"""
精细置信度模型设计与验证

置信度计算维度：
1. 信号强度：Top1信号值
2. 信号集中度：Top1与Top2的差距
3. 场景一致性：多个场景支持同一方向
4. 数据充足度：两队历史场次
5. 赔率价值：预测概率 vs 隐含概率

动态倍率：0.5 / 1 / 1.5 / 2 / 3
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
print("精细置信度模型设计与验证")
print("=" * 100)

INITIAL_CAPITAL = 1000.0
START_DATE = "2025-12-01"
BASE_UNIT = 2.0


# ============================================================
# 数据加载
# ============================================================

def load_hist_complete():
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
hist = load_hist_complete()

print(f"数据：历史{len(hist)}场")


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
        recent=ts.recent.copy() if hasattr(ts, 'recent') else [],
    )


# ============================================================
# 比分预测器（带详细置信度）
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


class ScorePredictorWithConfidence:
    """带详细置信度的比分预测器"""

    def __init__(self):
        self.th = THRESHOLDS
        self.calibration_strength = 0.5

    def predict(self, home: TeamData, away: TeamData, score_odds: Dict) -> Dict:
        """返回预测结果和详细置信度"""
        scenes = self._analyze_scenes(home, away)
        raw_signals = self._calc_raw_signals(scenes)
        calibrated = self._freq_calibrate(raw_signals)
        sorted_scores = sorted(calibrated.items(), key=lambda x: -x[1])

        # 计算详细置信度
        confidence = self._calc_detailed_confidence(
            scenes, sorted_scores, home, away, score_odds
        )

        return {
            "sorted_scores": sorted_scores,
            "scenes": scenes,
            "confidence": confidence,
        }

    def _analyze_scenes(self, home: TeamData, away: TeamData) -> Dict:
        scenes = {}
        th = self.th

        # S1: 攻防
        s1 = {"support": defaultdict(float), "direction": "neutral", "strength": 0}
        if home.gf_home_avg > th["gf_high"] and away.ga_away_avg > th["ga_high"]:
            s1["support"]["home_win"] = 0.4
            s1["direction"] = "home"
            s1["strength"] = min(home.gf_home_avg / 2, away.ga_away_avg / 1.5) / 2
        elif away.gf_away_avg > th["gf_high"] and home.ga_home_avg > th["ga_high"]:
            s1["support"]["away_win"] = 0.3
            s1["direction"] = "away"
            s1["strength"] = min(away.gf_away_avg / 2, home.ga_home_avg / 1.5) / 2
        elif home.gf_avg < th["gf_low"] and away.gf_avg < th["gf_low"]:
            s1["support"]["low_score"] = 0.4
            s1["support"]["draw"] = 0.3
            s1["direction"] = "low"
            s1["strength"] = (2 - home.gf_avg - away.gf_avg) / 2
        elif home.gf_avg > th["gf_high"] and away.gf_avg > th["gf_high"]:
            s1["support"]["high_score"] = 0.4
            s1["direction"] = "high"
            s1["strength"] = (home.gf_avg + away.gf_avg - 3.6) / 2
        else:
            s1["support"]["draw"] = 0.2
            s1["direction"] = "neutral"
        scenes["S1"] = s1

        # S2: 状态
        s2 = {"support": defaultdict(float), "direction": "neutral", "strength": 0}
        home_form = home.form_score()
        away_form = away.form_score()
        if home_form >= th["form_good"] and away_form <= th["form_bad"]:
            s2["support"]["home_win"] = 0.4
            s2["direction"] = "home"
            s2["strength"] = (home_form - away_form) / 9
        elif away_form >= th["form_good"] and home_form <= th["form_bad"]:
            s2["support"]["away_win"] = 0.4
            s2["direction"] = "away"
            s2["strength"] = (away_form - home_form) / 9
        elif home_form <= th["form_bad"] and away_form <= th["form_bad"]:
            s2["support"]["low_score"] = 0.3
            s2["support"]["draw"] = 0.3
            s2["direction"] = "low"
            s2["strength"] = (6 - home_form - away_form) / 6
        else:
            s2["support"]["draw"] = 0.2
            s2["direction"] = "neutral"
        scenes["S2"] = s2

        # S3: 零封
        s3 = {"support": defaultdict(float), "direction": "neutral", "strength": 0}
        if home.cs_rate > th["cs_rate_high"] and away.becs_rate > th["becs_rate_high"]:
            s3["support"]["home_clean"] = 0.4
            s3["direction"] = "home_clean"
            s3["strength"] = (home.cs_rate + away.becs_rate - 0.7) / 0.6
        if away.cs_rate > th["cs_rate_high"] and home.becs_rate > th["becs_rate_high"]:
            s3["support"]["away_clean"] = 0.4
            s3["direction"] = "away_clean"
            s3["strength"] = (away.cs_rate + home.becs_rate - 0.7) / 0.6
        if home.becs_rate > th["becs_rate_high"] and away.becs_rate > th["becs_rate_high"]:
            s3["support"]["low_score"] = 0.3
            s3["direction"] = "low"
            s3["strength"] = (home.becs_rate + away.becs_rate - 0.7) / 0.6
        scenes["S3"] = s3

        # S4: 大球
        s4 = {"support": defaultdict(float), "direction": "neutral", "strength": 0}
        if home.over25_rate > th["over25_high"] and away.over25_rate > th["over25_high"]:
            s4["support"]["high_score"] = 0.4
            s4["direction"] = "high"
            s4["strength"] = (home.over25_rate + away.over25_rate - 1.1) / 0.8
        if home.btts_rate > th["btts_high"] and away.btts_rate > th["btts_high"]:
            s4["support"]["both_score"] = 0.3
            s4["direction"] = "btts"
            s4["strength"] = (home.btts_rate + away.btts_rate - 1.1) / 0.8
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
        for score in set(raw.keys()) | set(HIST_FREQ.keys()):
            r = raw.get(score, 0)
            h = HIST_FREQ.get(score, 0)
            calibrated[score] = (1 - self.calibration_strength) * r + self.calibration_strength * h
        return calibrated

    def _calc_detailed_confidence(self, scenes, sorted_scores, home, away, score_odds) -> Dict:
        """计算详细置信度指标"""

        # 1. 信号强度 (0-1)
        top1_signal = sorted_scores[0][1] if sorted_scores else 0
        signal_strength = min(top1_signal / 0.2, 1.0)  # 0.2为满分

        # 2. 信号集中度：Top1与Top2的差距 (0-1)
        if len(sorted_scores) >= 2:
            gap = sorted_scores[0][1] - sorted_scores[1][1]
            signal_concentration = min(gap / 0.05, 1.0)  # 0.05差距为满分
        else:
            signal_concentration = 0

        # 3. 场景一致性 (0-1)
        directions = [s["direction"] for s in scenes.values() if s["direction"] != "neutral"]
        if directions:
            # 计算最多的方向占比
            from collections import Counter
            dir_counts = Counter(directions)
            most_common_count = dir_counts.most_common(1)[0][1]
            scene_consistency = most_common_count / len(directions)
        else:
            scene_consistency = 0

        # 4. 场景强度均值 (0-1)
        strengths = [s["strength"] for s in scenes.values() if s["strength"] > 0]
        scene_strength = sum(strengths) / len(strengths) if strengths else 0
        scene_strength = min(scene_strength, 1.0)

        # 5. 数据充足度 (0-1)
        min_n = min(home.n, away.n)
        data_sufficiency = min(min_n / 20, 1.0)  # 20场为满分

        # 6. 赔率价值：预测概率 vs 隐含概率 (0-1)
        top1_score = sorted_scores[0][0] if sorted_scores else None
        if top1_score and top1_score in score_odds:
            odds = score_odds[top1_score]
            implied_prob = 1 / odds  # 庄家隐含概率
            pred_prob = sorted_scores[0][1]  # 我们的预测概率（近似）
            # 如果我们认为概率高于庄家，说明有价值
            value_ratio = pred_prob / implied_prob if implied_prob > 0 else 0
            odds_value = min(max(value_ratio - 0.5, 0) / 1.0, 1.0)  # 0.5-1.5映射到0-1
        else:
            odds_value = 0

        # 7. 综合置信度分数 (加权平均)
        weights = {
            "signal_strength": 0.20,
            "signal_concentration": 0.15,
            "scene_consistency": 0.20,
            "scene_strength": 0.15,
            "data_sufficiency": 0.10,
            "odds_value": 0.20,
        }

        composite_score = (
            weights["signal_strength"] * signal_strength +
            weights["signal_concentration"] * signal_concentration +
            weights["scene_consistency"] * scene_consistency +
            weights["scene_strength"] * scene_strength +
            weights["data_sufficiency"] * data_sufficiency +
            weights["odds_value"] * odds_value
        )

        return {
            "signal_strength": signal_strength,
            "signal_concentration": signal_concentration,
            "scene_consistency": scene_consistency,
            "scene_strength": scene_strength,
            "data_sufficiency": data_sufficiency,
            "odds_value": odds_value,
            "composite": composite_score,
            # 原始值
            "raw_top1_signal": top1_signal,
            "raw_gap": sorted_scores[0][1] - sorted_scores[1][1] if len(sorted_scores) >= 2 else 0,
            "raw_min_n": min_n,
        }


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
predictor = ScorePredictorWithConfidence()
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
        pred = predictor.predict(home_data, away_data, m["score_odds"])
        match_packs.append({
            "date": m["date"],
            "actual": m["actual"],
            "odds": m["score_odds"],
            "prediction": pred,
        })
    if kind == "L":
        stats[h].add(hg, ag, True, a)
        stats[a].add(ag, hg, False, h)

by_day = defaultdict(list)
for p in match_packs:
    by_day[p["date"]].append(p)

print(f"有效预测包：{len(match_packs)}场，跨越{len(by_day)}天")


# ============================================================
# 分析置信度与命中率的关系
# ============================================================

print("\n" + "=" * 100)
print("1. 各置信度维度与命中率的关系（单场分析）")
print("=" * 100)

# 收集单场数据
single_match_data = []
for p in match_packs:
    top1 = p["prediction"]["sorted_scores"][0][0]
    is_hit = (top1 == p["actual"])
    conf = p["prediction"]["confidence"]
    single_match_data.append({
        "is_hit": is_hit,
        **conf
    })

# 分析各维度
dimensions = [
    ("signal_strength", "信号强度"),
    ("signal_concentration", "信号集中度"),
    ("scene_consistency", "场景一致性"),
    ("scene_strength", "场景强度"),
    ("data_sufficiency", "数据充足度"),
    ("odds_value", "赔率价值"),
    ("composite", "综合置信度"),
]

for dim_key, dim_name in dimensions:
    print(f"\n【{dim_name}】")
    # 分桶统计
    buckets = [(0, 0.2, "极低"), (0.2, 0.4, "低"), (0.4, 0.6, "中"), (0.6, 0.8, "高"), (0.8, 1.01, "极高")]
    for low, high, label in buckets:
        subset = [d for d in single_match_data if low <= d[dim_key] < high]
        if len(subset) >= 10:
            hit_rate = sum(1 for d in subset if d["is_hit"]) / len(subset) * 100
            print(f"  {label}({low:.1f}-{high:.1f}): {len(subset)}场，命中率{hit_rate:.1f}%")


# ============================================================
# 分析串关票的置信度与命中率
# ============================================================

print("\n" + "=" * 100)
print("2. 串关票置信度与命中率的关系")
print("=" * 100)

# 提取串关票数据
ticket_data = []
for day in sorted(by_day):
    day_packs = by_day[day]
    if len(day_packs) < 2:
        continue

    for p in day_packs:
        p["max_signal"] = p["prediction"]["sorted_scores"][0][1]
    selected = sorted(day_packs, key=lambda x: -x["max_signal"])[:2]

    # 检查是否命中
    all_picks = []
    for p in selected:
        picks = []
        for score, sig in p["prediction"]["sorted_scores"][:2]:
            picks.append((score, p["actual"]))
        all_picks.append(picks)

    is_hit = False
    for combo in product(*all_picks):
        if all(score == actual for score, actual in combo):
            is_hit = True
            break

    # 计算票级置信度（两场平均）
    avg_conf = {}
    for key in selected[0]["prediction"]["confidence"]:
        if isinstance(selected[0]["prediction"]["confidence"][key], (int, float)):
            avg_conf[key] = sum(p["prediction"]["confidence"][key] for p in selected) / 2

    ticket_data.append({
        "date": day,
        "is_hit": is_hit,
        **avg_conf
    })

print(f"总票数：{len(ticket_data)}，命中：{sum(1 for t in ticket_data if t['is_hit'])}")

for dim_key, dim_name in dimensions:
    print(f"\n【{dim_name}】")
    buckets = [(0, 0.3, "低"), (0.3, 0.5, "中"), (0.5, 0.7, "高"), (0.7, 1.01, "极高")]
    for low, high, label in buckets:
        subset = [t for t in ticket_data if low <= t.get(dim_key, 0) < high]
        if len(subset) >= 5:
            hit_rate = sum(1 for t in subset if t["is_hit"]) / len(subset) * 100
            print(f"  {label}({low:.1f}-{high:.1f}): {len(subset)}票，命中率{hit_rate:.1f}%")


# ============================================================
# 动态倍率策略测试
# ============================================================

print("\n" + "=" * 100)
print("3. 动态倍率策略测试")
print("=" * 100)


def run_with_dynamic_multiplier(by_day, get_multiplier):
    """带动态倍率的2串1双选"""
    capital = INITIAL_CAPITAL
    total_cost = 0
    total_payout = 0
    n_tickets = 0
    n_hits = 0
    min_capital = capital
    max_capital = capital
    multiplier_dist = defaultdict(int)

    for day in sorted(by_day):
        day_packs = by_day[day]
        if len(day_packs) < 2:
            continue

        for p in day_packs:
            p["max_signal"] = p["prediction"]["sorted_scores"][0][1]
        selected = sorted(day_packs, key=lambda x: -x["max_signal"])[:2]

        # 计算票级置信度
        avg_conf = {}
        for key in selected[0]["prediction"]["confidence"]:
            if isinstance(selected[0]["prediction"]["confidence"][key], (int, float)):
                avg_conf[key] = sum(p["prediction"]["confidence"][key] for p in selected) / 2

        # 获取倍率
        multiplier = get_multiplier(avg_conf, selected)
        multiplier_dist[multiplier] += 1
        unit = BASE_UNIT * multiplier

        all_picks = []
        for p in selected:
            picks = []
            for score, sig in p["prediction"]["sorted_scores"][:2]:
                odds = p["odds"].get(score, 999)
                picks.append((score, odds, p["actual"]))
            all_picks.append(picks)

        all_bets = list(product(*all_picks))
        cost = len(all_bets) * unit

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
                payout += unit * combo_odds

        capital += payout
        total_payout += payout
        if payout > 0:
            n_hits += 1

        min_capital = min(min_capital, capital)
        max_capital = max(max_capital, capital)

    max_drawdown = (max_capital - min_capital) / max_capital if max_capital > 0 else 0

    return {
        "final": capital,
        "profit": (capital - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100,
        "n_tickets": n_tickets,
        "n_hits": n_hits,
        "cost": total_cost,
        "payout": total_payout,
        "max_drawdown": max_drawdown * 100,
        "multiplier_dist": dict(multiplier_dist),
    }


# 策略定义
strategies = {
    # 基准
    "固定1倍": lambda conf, packs: 1,

    # 简单规则
    "高综合2倍": lambda conf, packs: 2 if conf.get("composite", 0) > 0.5 else 1,
    "高综合3倍": lambda conf, packs: 3 if conf.get("composite", 0) > 0.6 else 1,

    # 动态多档
    "动态4档(综合)": lambda conf, packs: (
        3 if conf.get("composite", 0) > 0.6 else
        2 if conf.get("composite", 0) > 0.5 else
        1 if conf.get("composite", 0) > 0.4 else
        0.5
    ),

    # 多因子组合
    "高信号+高一致": lambda conf, packs: (
        2 if conf.get("signal_strength", 0) > 0.6 and conf.get("scene_consistency", 0) > 0.6 else 1
    ),

    "高价值2倍": lambda conf, packs: 2 if conf.get("odds_value", 0) > 0.5 else 1,

    "综合>0.5且价值>0.3": lambda conf, packs: (
        2 if conf.get("composite", 0) > 0.5 and conf.get("odds_value", 0) > 0.3 else 1
    ),

    # 保守策略：低置信度减半
    "低综合0.5倍": lambda conf, packs: 0.5 if conf.get("composite", 0) < 0.4 else 1,

    # 激进策略
    "动态5档": lambda conf, packs: (
        3 if conf.get("composite", 0) > 0.65 else
        2 if conf.get("composite", 0) > 0.55 else
        1.5 if conf.get("composite", 0) > 0.45 else
        1 if conf.get("composite", 0) > 0.35 else
        0.5
    ),
}

print(f"\n{'策略':<25} {'最终资金':>10} {'收益率':>10} {'命中':>10} {'回撤':>8} {'倍率分布'}")
print("-" * 90)

for name, get_mult in strategies.items():
    result = run_with_dynamic_multiplier(by_day, get_mult)
    dist_str = str(result["multiplier_dist"])
    print(f"{name:<25} {result['final']:>10.1f} {result['profit']:>+9.1f}% {result['n_hits']:>3}/{result['n_tickets']:<5} {result['max_drawdown']:>7.1f}% {dist_str}")


print("\n" + "=" * 100)
print("完成")
print("=" * 100)
