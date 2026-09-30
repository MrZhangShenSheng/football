# -*- coding: utf-8 -*-
"""
倍率模型探索 —— 什么时候使用高倍率投注

核心问题：能否识别出"高置信度"场次，在这些场次加倍投注？

探索维度：
1. 信号强度：预测信号越高，倍率越高
2. 场景一致性：多个场景一致时加倍
3. 历史命中率：某些比分组合命中率更高
4. 赔率价值：赔率高于预期时加倍
5. 连续未中后：马丁格尔策略

作者：sszhang
日期：2026-09-30
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
print("倍率模型探索 —— 什么时候使用高倍率投注")
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
# 比分预测器
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
    def __init__(self):
        self.th = THRESHOLDS

    def predict(self, home: TeamData, away: TeamData) -> Dict:
        """返回预测结果，包含置信度指标"""
        scenes = self._analyze_scenes(home, away)
        raw_signals = self._calc_raw_signals(scenes)
        calibrated = self._freq_calibrate(raw_signals)
        sorted_scores = sorted(calibrated.items(), key=lambda x: -x[1])

        # 计算置信度指标
        confidence_metrics = self._calc_confidence(scenes, sorted_scores, home, away)

        return {
            "sorted_scores": sorted_scores,
            "scenes": scenes,
            "confidence": confidence_metrics,
        }

    def _analyze_scenes(self, home: TeamData, away: TeamData) -> Dict:
        scenes = {}
        th = self.th

        # S1: 攻防
        s1 = {"support": defaultdict(float), "judgment": "均衡"}
        if home.gf_home_avg > th["gf_high"] and away.ga_away_avg > th["ga_high"]:
            s1["judgment"] = "主攻压制"
            s1["support"]["home_win"] = 0.4
        elif away.gf_away_avg > th["gf_high"] and home.ga_home_avg > th["ga_high"]:
            s1["judgment"] = "客攻有威胁"
            s1["support"]["away_win"] = 0.3
        elif home.gf_home_avg < th["gf_low"] and away.gf_away_avg < th["gf_low"]:
            s1["judgment"] = "双方低迷"
            s1["support"]["low_score"] = 0.4
            s1["support"]["draw"] = 0.3
        else:
            s1["support"]["draw"] = 0.2
        scenes["S1"] = s1

        # S2: 状态
        s2 = {"support": defaultdict(float), "judgment": "状态接近"}
        home_form = home.form_score()
        away_form = away.form_score()
        if home_form >= th["form_good"] and away_form <= th["form_bad"]:
            s2["judgment"] = "主强客弱"
            s2["support"]["home_win"] = 0.4
        elif away_form >= th["form_good"] and home_form <= th["form_bad"]:
            s2["judgment"] = "客强主弱"
            s2["support"]["away_win"] = 0.4
        elif home_form <= th["form_bad"] and away_form <= th["form_bad"]:
            s2["judgment"] = "双方低迷"
            s2["support"]["low_score"] = 0.3
        else:
            s2["support"]["draw"] = 0.2
        scenes["S2"] = s2

        # S3: 零封
        s3 = {"support": defaultdict(float), "judgment": "无明显倾向"}
        if home.cs_rate > th["cs_rate_high"] and away.becs_rate > th["becs_rate_high"]:
            s3["judgment"] = "主零封倾向"
            s3["support"]["home_clean"] = 0.4
        if away.cs_rate > th["cs_rate_high"] and home.becs_rate > th["becs_rate_high"]:
            s3["judgment"] = "客零封倾向"
            s3["support"]["away_clean"] = 0.4
        scenes["S3"] = s3

        # S4: 大球
        s4 = {"support": defaultdict(float), "judgment": "无明显倾向"}
        if home.over25_rate > th["over25_high"] and away.over25_rate > th["over25_high"]:
            s4["judgment"] = "大球倾向"
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
                    signals[(3, 2)] += strength * 0.2
                elif effect == "home_clean":
                    signals[(1, 0)] += strength * 0.4
                    signals[(2, 0)] += strength * 0.4
                elif effect == "away_clean":
                    signals[(0, 1)] += strength * 0.4
                    signals[(0, 2)] += strength * 0.4
                elif effect == "both_score":
                    signals[(1, 1)] += strength * 0.3
                    signals[(2, 1)] += strength * 0.2
                    signals[(1, 2)] += strength * 0.2
        return dict(signals)

    def _freq_calibrate(self, raw: Dict) -> Dict:
        calibrated = {}
        for score in HIST_FREQ:
            raw_sig = raw.get(score, 0)
            hist = HIST_FREQ[score]
            calibrated[score] = 0.5 * raw_sig + 0.5 * hist
        total = sum(calibrated.values()) or 1
        return {s: v / total for s, v in calibrated.items()}

    def _calc_confidence(self, scenes: Dict, sorted_scores: List, home: TeamData, away: TeamData) -> Dict:
        """计算多维度置信度指标"""

        # 1. 信号强度差：Top1 vs Top2 的差距
        top1_sig = sorted_scores[0][1] if sorted_scores else 0
        top2_sig = sorted_scores[1][1] if len(sorted_scores) > 1 else 0
        signal_gap = top1_sig - top2_sig

        # 2. 场景一致性：有多少场景指向同一方向
        judgments = [s["judgment"] for s in scenes.values()]
        non_neutral = [j for j in judgments if j not in ["均衡", "状态接近", "无明显倾向"]]
        scene_consistency = len(non_neutral)

        # 3. 数据充分度：两队历史场次
        data_sufficiency = min(home.n, away.n)

        # 4. Top1比分是否为高频比分
        top1_score = sorted_scores[0][0] if sorted_scores else None
        is_high_freq = top1_score in [(1, 1), (2, 1), (1, 0), (1, 2), (0, 1)]

        # 5. 综合置信度得分
        confidence_score = 0
        if signal_gap > 0.03:
            confidence_score += 1
        if signal_gap > 0.05:
            confidence_score += 1
        if scene_consistency >= 2:
            confidence_score += 1
        if scene_consistency >= 3:
            confidence_score += 1
        if data_sufficiency >= 15:
            confidence_score += 1
        if is_high_freq:
            confidence_score += 1

        return {
            "signal_gap": signal_gap,
            "scene_consistency": scene_consistency,
            "data_sufficiency": data_sufficiency,
            "is_high_freq": is_high_freq,
            "confidence_score": confidence_score,  # 0-6分
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
predictor = ScorePredictor()
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
print("1. 置信度得分与命中率的关系")
print("=" * 100)

# 统计每个置信度得分的命中率
confidence_stats = defaultdict(lambda: {"total": 0, "hit": 0, "payout": 0, "cost": 0})

for p in match_packs:
    conf = p["prediction"]["confidence"]
    conf_score = conf["confidence_score"]
    sorted_scores = p["prediction"]["sorted_scores"]

    # Top1预测
    top1 = sorted_scores[0][0]
    is_hit = (top1 == p["actual"])
    odds = p["odds"].get(top1, 0)

    confidence_stats[conf_score]["total"] += 1
    confidence_stats[conf_score]["cost"] += BASE_UNIT
    if is_hit:
        confidence_stats[conf_score]["hit"] += 1
        confidence_stats[conf_score]["payout"] += BASE_UNIT * odds

print(f"\n{'置信度':>8} {'样本数':>10} {'命中':>8} {'命中率':>10} {'成本':>10} {'派彩':>10} {'ROI':>10}")
print("-" * 80)

for score in sorted(confidence_stats.keys()):
    s = confidence_stats[score]
    hit_rate = s["hit"] / s["total"] * 100 if s["total"] > 0 else 0
    roi = (s["payout"] - s["cost"]) / s["cost"] * 100 if s["cost"] > 0 else 0
    print(f"{score:>8} {s['total']:>10} {s['hit']:>8} {hit_rate:>9.1f}% {s['cost']:>10.0f} {s['payout']:>10.1f} {roi:>+9.1f}%")


# ============================================================
# 分析信号强度差与命中率
# ============================================================

print("\n" + "=" * 100)
print("2. 信号强度差与命中率的关系")
print("=" * 100)

gap_bins = [(0, 0.02, "<0.02"), (0.02, 0.04, "0.02-0.04"), (0.04, 0.06, "0.04-0.06"), (0.06, 1, ">0.06")]
gap_stats = {label: {"total": 0, "hit": 0, "payout": 0, "cost": 0} for _, _, label in gap_bins}

for p in match_packs:
    gap = p["prediction"]["confidence"]["signal_gap"]
    sorted_scores = p["prediction"]["sorted_scores"]
    top1 = sorted_scores[0][0]
    is_hit = (top1 == p["actual"])
    odds = p["odds"].get(top1, 0)

    for low, high, label in gap_bins:
        if low <= gap < high:
            gap_stats[label]["total"] += 1
            gap_stats[label]["cost"] += BASE_UNIT
            if is_hit:
                gap_stats[label]["hit"] += 1
                gap_stats[label]["payout"] += BASE_UNIT * odds
            break

print(f"\n{'信号差':>12} {'样本数':>10} {'命中':>8} {'命中率':>10} {'ROI':>10}")
print("-" * 60)

for _, _, label in gap_bins:
    s = gap_stats[label]
    hit_rate = s["hit"] / s["total"] * 100 if s["total"] > 0 else 0
    roi = (s["payout"] - s["cost"]) / s["cost"] * 100 if s["cost"] > 0 else 0
    print(f"{label:>12} {s['total']:>10} {s['hit']:>8} {hit_rate:>9.1f}% {roi:>+9.1f}%")


# ============================================================
# 分析场景一致性与命中率
# ============================================================

print("\n" + "=" * 100)
print("3. 场景一致性与命中率的关系")
print("=" * 100)

consistency_stats = defaultdict(lambda: {"total": 0, "hit": 0, "payout": 0, "cost": 0})

for p in match_packs:
    consistency = p["prediction"]["confidence"]["scene_consistency"]
    sorted_scores = p["prediction"]["sorted_scores"]
    top1 = sorted_scores[0][0]
    is_hit = (top1 == p["actual"])
    odds = p["odds"].get(top1, 0)

    consistency_stats[consistency]["total"] += 1
    consistency_stats[consistency]["cost"] += BASE_UNIT
    if is_hit:
        consistency_stats[consistency]["hit"] += 1
        consistency_stats[consistency]["payout"] += BASE_UNIT * odds

print(f"\n{'一致性':>8} {'样本数':>10} {'命中':>8} {'命中率':>10} {'ROI':>10}")
print("-" * 60)

for c in sorted(consistency_stats.keys()):
    s = consistency_stats[c]
    hit_rate = s["hit"] / s["total"] * 100 if s["total"] > 0 else 0
    roi = (s["payout"] - s["cost"]) / s["cost"] * 100 if s["cost"] > 0 else 0
    print(f"{c:>8} {s['total']:>10} {s['hit']:>8} {hit_rate:>9.1f}% {roi:>+9.1f}%")


# ============================================================
# 倍率策略模拟
# ============================================================

print("\n" + "=" * 100)
print("4. 倍率策略模拟（2串1双选）")
print("=" * 100)


def simulate_with_multiplier(by_day, multiplier_strategy):
    """
    模拟带倍率的投注
    multiplier_strategy: 函数，输入(day_packs)，返回倍率(1-5)
    """
    capital = INITIAL_CAPITAL
    total_cost = 0
    total_payout = 0
    n_tickets = 0
    n_hits = 0
    min_capital = capital

    for day in sorted(by_day):
        day_packs = by_day[day]
        if len(day_packs) < 2:
            continue

        # 选场
        for p in day_packs:
            p["max_signal"] = p["prediction"]["sorted_scores"][0][1]
        selected = sorted(day_packs, key=lambda x: -x["max_signal"])[:2]

        # 计算倍率
        multiplier = multiplier_strategy(selected)
        unit = BASE_UNIT * multiplier

        # 每场选2个比分
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

    return {
        "final": capital,
        "profit": (capital - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100,
        "n_tickets": n_tickets,
        "n_hits": n_hits,
        "cost": total_cost,
        "payout": total_payout,
        "max_drawdown": (INITIAL_CAPITAL - min_capital) / INITIAL_CAPITAL * 100 if min_capital < INITIAL_CAPITAL else 0,
    }


# 策略1：固定倍率
def strategy_fixed_1x(packs):
    return 1

def strategy_fixed_2x(packs):
    return 2

# 策略2：高置信度加倍
def strategy_confidence_based(packs):
    avg_conf = sum(p["prediction"]["confidence"]["confidence_score"] for p in packs) / len(packs)
    if avg_conf >= 4:
        return 3
    elif avg_conf >= 3:
        return 2
    else:
        return 1

# 策略3：信号差大时加倍
def strategy_signal_gap(packs):
    avg_gap = sum(p["prediction"]["confidence"]["signal_gap"] for p in packs) / len(packs)
    if avg_gap > 0.05:
        return 3
    elif avg_gap > 0.03:
        return 2
    else:
        return 1

# 策略4：场景一致时加倍
def strategy_scene_consistency(packs):
    avg_cons = sum(p["prediction"]["confidence"]["scene_consistency"] for p in packs) / len(packs)
    if avg_cons >= 2:
        return 2
    else:
        return 1

# 策略5：马丁格尔（连续未中后加倍）
consecutive_losses = [0]  # 用列表模拟可变状态
def strategy_martingale(packs):
    # 简化：根据历史未中次数决定倍率
    # 这里无法追踪历史，用置信度代替
    avg_conf = sum(p["prediction"]["confidence"]["confidence_score"] for p in packs) / len(packs)
    return 2 if avg_conf >= 3 else 1

# 策略6：高赔率时加倍
def strategy_high_odds(packs):
    total_odds = 1.0
    for p in packs:
        top1 = p["prediction"]["sorted_scores"][0][0]
        odds = p["odds"].get(top1, 5)
        total_odds *= odds
    if total_odds > 100:  # 组合赔率>100
        return 2
    else:
        return 1

# 策略7：综合策略
def strategy_combined(packs):
    score = 0
    # 置信度
    avg_conf = sum(p["prediction"]["confidence"]["confidence_score"] for p in packs) / len(packs)
    if avg_conf >= 4:
        score += 2
    elif avg_conf >= 3:
        score += 1
    # 信号差
    avg_gap = sum(p["prediction"]["confidence"]["signal_gap"] for p in packs) / len(packs)
    if avg_gap > 0.04:
        score += 1
    # 场景一致
    avg_cons = sum(p["prediction"]["confidence"]["scene_consistency"] for p in packs) / len(packs)
    if avg_cons >= 2:
        score += 1

    if score >= 3:
        return 3
    elif score >= 2:
        return 2
    else:
        return 1


strategies = [
    ("固定1倍", strategy_fixed_1x),
    ("固定2倍", strategy_fixed_2x),
    ("置信度加倍", strategy_confidence_based),
    ("信号差加倍", strategy_signal_gap),
    ("场景一致加倍", strategy_scene_consistency),
    ("高赔率加倍", strategy_high_odds),
    ("综合策略", strategy_combined),
]

print(f"\n{'策略':>15} {'最终资金':>12} {'收益率':>10} {'命中':>10} {'成本':>10} {'派彩':>12} {'回撤':>10}")
print("-" * 90)

for name, strategy in strategies:
    result = simulate_with_multiplier(by_day, strategy)
    print(f"{name:>15} {result['final']:>12.1f} {result['profit']:>+9.1f}% "
          f"{result['n_hits']}/{result['n_tickets']}({result['n_hits']/result['n_tickets']*100:.1f}%) "
          f"{result['cost']:>10.0f} {result['payout']:>12.1f} {result['max_drawdown']:>9.1f}%")


print("\n" + "=" * 100)
print("分析完成")
print("=" * 100)
