# -*- coding: utf-8 -*-
"""多场景模型优化 —— 多组对照实验"""
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
print("多场景模型优化 —— 多组对照实验")
print("=" * 100)

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

print(f"数据加载完成：历史{len(hist)}场，联赛库{len(tl)}场")


# ============================================================
# 优化后的数据结构
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
# 比分分组（扩展版）
# ============================================================

SCORE_GROUPS_V2 = {
    "zero_zero":    [(0, 0)],
    "home_1_0":     [(1, 0)],
    "home_2_0":     [(2, 0)],
    "home_3_0":     [(3, 0), (4, 0)],
    "away_0_1":     [(0, 1)],
    "away_0_2":     [(0, 2)],
    "away_0_3":     [(0, 3), (0, 4)],
    "draw_1_1":     [(1, 1)],
    "draw_2_2":     [(2, 2)],
    "draw_3_3":     [(3, 3)],
    "home_2_1":     [(2, 1)],
    "home_3_1":     [(3, 1), (4, 1)],
    "home_3_2":     [(3, 2), (4, 2)],
    "away_1_2":     [(1, 2)],
    "away_1_3":     [(1, 3), (1, 4)],
    "away_2_3":     [(2, 3), (2, 4)],
}

# 历史频率（用于校准）
HIST_FREQ = {
    (1, 1): 0.12, (2, 1): 0.09, (1, 0): 0.09, (1, 2): 0.08,
    (0, 1): 0.07, (0, 0): 0.07, (2, 0): 0.07, (2, 2): 0.05,
    (0, 2): 0.05, (3, 0): 0.04, (3, 1): 0.04, (3, 2): 0.03,
    (1, 3): 0.03, (2, 3): 0.02, (4, 0): 0.02, (4, 1): 0.02,
    (0, 3): 0.02, (3, 3): 0.01,
}


# ============================================================
# 场景分析器（可配置版）
# ============================================================

class ConfigurablePredictor:
    """可配置的预测器"""

    def __init__(self, config: Dict):
        self.config = config
        # 阈值配置
        self.th = config.get("thresholds", {})
        # 权重配置
        self.weights = config.get("weights", {})
        # 信号计算方式
        self.signal_mode = config.get("signal_mode", "weighted_sum")
        # 是否使用频率校准
        self.use_freq_calibration = config.get("use_freq_calibration", False)
        # 频率校准强度
        self.calibration_strength = config.get("calibration_strength", 0.5)

    def predict(self, home: TeamData, away: TeamData) -> Dict:
        """预测所有比分的信号"""

        # 1. 运行各场景分析
        scenes = self._analyze_scenes(home, away)

        # 2. 计算原始信号
        raw_signals = self._calc_raw_signals(scenes)

        # 3. 频率校准（如果启用）
        if self.use_freq_calibration:
            signals = self._calibrate_signals(raw_signals)
        else:
            signals = raw_signals

        # 4. 排序输出
        sorted_scores = sorted(signals.items(), key=lambda x: -x[1])

        return {
            "predictions": {s: {"signal": sig, "rank": i+1}
                          for i, (s, sig) in enumerate(sorted_scores)},
            "sorted_scores": sorted_scores,
            "scenes": scenes,
        }

    def _analyze_scenes(self, home: TeamData, away: TeamData) -> Dict:
        """分析各场景"""
        th = self.th
        scenes = {}

        # S1: 攻防对比
        home_attack = home.gf_home_avg
        away_attack = away.gf_away_avg
        home_defense = home.ga_home_avg
        away_defense = away.ga_away_avg

        s1 = {"support": defaultdict(float)}
        if home_attack > th.get("gf_high", 1.8) and away_defense > th.get("ga_high", 1.5):
            s1["judgment"] = "home_attack_strong"
            s1["support"]["home_2_0"] = 0.4
            s1["support"]["home_3_0"] = 0.3
            s1["support"]["home_2_1"] = 0.3
            s1["support"]["home_3_1"] = 0.2
        elif away_attack > th.get("gf_high", 1.5) and home_defense > th.get("ga_high", 1.5):
            s1["judgment"] = "away_attack_strong"
            s1["support"]["away_0_2"] = 0.4
            s1["support"]["away_0_3"] = 0.3
            s1["support"]["away_1_2"] = 0.3
            s1["support"]["away_1_3"] = 0.2
        elif home_attack < th.get("gf_low", 1.0) and away_attack < th.get("gf_low", 1.0):
            s1["judgment"] = "both_weak_attack"
            s1["support"]["zero_zero"] = 0.4
            s1["support"]["draw_1_1"] = 0.3
            s1["support"]["home_1_0"] = 0.2
            s1["support"]["away_0_1"] = 0.2
        else:
            s1["judgment"] = "balanced"
            s1["support"]["draw_1_1"] = 0.2
            s1["support"]["home_2_1"] = 0.15
            s1["support"]["away_1_2"] = 0.15
        scenes["S1"] = s1

        # S2: 近期状态
        home_form = home.form_score()
        away_form = away.form_score()

        s2 = {"support": defaultdict(float)}
        if home_form >= th.get("form_good", 7) and away_form <= th.get("form_bad", 3):
            s2["judgment"] = "home_strong"
            s2["support"]["home_2_0"] = 0.3
            s2["support"]["home_2_1"] = 0.3
            s2["support"]["home_1_0"] = 0.2
            s2["support"]["home_3_1"] = 0.2
        elif away_form >= th.get("form_good", 7) and home_form <= th.get("form_bad", 3):
            s2["judgment"] = "away_strong"
            s2["support"]["away_0_2"] = 0.3
            s2["support"]["away_1_2"] = 0.3
            s2["support"]["away_0_1"] = 0.2
            s2["support"]["away_1_3"] = 0.2
        elif home_form <= th.get("form_bad", 3) and away_form <= th.get("form_bad", 3):
            s2["judgment"] = "both_poor"
            s2["support"]["zero_zero"] = 0.3
            s2["support"]["draw_1_1"] = 0.3
        else:
            s2["judgment"] = "similar"
            s2["support"]["draw_1_1"] = 0.2
            s2["support"]["draw_2_2"] = 0.1
        scenes["S2"] = s2

        # S3: 零封能力
        home_cs = home.cs_rate
        away_cs = away.cs_rate
        home_becs = home.becs_rate
        away_becs = away.becs_rate

        s3 = {"support": defaultdict(float)}
        if home_cs > th.get("cs_high", 0.35) and away_becs > th.get("becs_high", 0.35):
            s3["judgment"] = "home_clean_likely"
            s3["support"]["home_1_0"] = 0.4
            s3["support"]["home_2_0"] = 0.4
            s3["support"]["home_3_0"] = 0.2
        elif away_cs > th.get("cs_high", 0.35) and home_becs > th.get("becs_high", 0.35):
            s3["judgment"] = "away_clean_likely"
            s3["support"]["away_0_1"] = 0.4
            s3["support"]["away_0_2"] = 0.4
            s3["support"]["away_0_3"] = 0.2
        elif home_becs > th.get("becs_high", 0.35) and away_becs > th.get("becs_high", 0.35):
            s3["judgment"] = "zero_zero_likely"
            s3["support"]["zero_zero"] = 0.5
        else:
            s3["judgment"] = "normal"
        scenes["S3"] = s3

        # S4: 大球倾向
        home_over25 = home.over25_rate
        away_over25 = away.over25_rate
        home_btts = home.btts_rate
        away_btts = away.btts_rate

        s4 = {"support": defaultdict(float)}
        if home_over25 > th.get("over25_high", 0.55) and away_over25 > th.get("over25_high", 0.55):
            s4["judgment"] = "high_scoring"
            s4["support"]["home_2_1"] = 0.3
            s4["support"]["away_1_2"] = 0.3
            s4["support"]["home_3_1"] = 0.2
            s4["support"]["away_1_3"] = 0.2
            s4["support"]["draw_2_2"] = 0.2
            s4["support"]["home_3_2"] = 0.15
            s4["support"]["away_2_3"] = 0.15
        elif home_over25 < th.get("over25_low", 0.40) and away_over25 < th.get("over25_low", 0.40):
            s4["judgment"] = "low_scoring"
            s4["support"]["zero_zero"] = 0.3
            s4["support"]["draw_1_1"] = 0.3
            s4["support"]["home_1_0"] = 0.2
            s4["support"]["away_0_1"] = 0.2
        else:
            s4["judgment"] = "normal"
        scenes["S4"] = s4

        # S5: BTTS倾向
        s5 = {"support": defaultdict(float)}
        if home_btts > th.get("btts_high", 0.55) and away_btts > th.get("btts_high", 0.55):
            s5["judgment"] = "btts_likely"
            s5["support"]["draw_1_1"] = 0.3
            s5["support"]["draw_2_2"] = 0.25
            s5["support"]["home_2_1"] = 0.25
            s5["support"]["away_1_2"] = 0.25
            # 零封负信号
            s5["support"]["home_1_0"] = -0.2
            s5["support"]["home_2_0"] = -0.2
            s5["support"]["away_0_1"] = -0.2
            s5["support"]["away_0_2"] = -0.2
            s5["support"]["zero_zero"] = -0.3
        elif home_btts < th.get("btts_low", 0.40) and away_btts < th.get("btts_low", 0.40):
            s5["judgment"] = "btts_unlikely"
            s5["support"]["home_1_0"] = 0.3
            s5["support"]["home_2_0"] = 0.25
            s5["support"]["away_0_1"] = 0.3
            s5["support"]["away_0_2"] = 0.25
            s5["support"]["zero_zero"] = 0.3
        else:
            s5["judgment"] = "normal"
        scenes["S5"] = s5

        return scenes

    def _calc_raw_signals(self, scenes: Dict) -> Dict[Tuple, float]:
        """计算原始信号"""
        signals = defaultdict(float)

        # 遍历所有比分组
        for group_name, scores in SCORE_GROUPS_V2.items():
            group_signal = 0.0

            if self.signal_mode == "weighted_sum":
                # 加权求和
                weights = self.weights.get("scene_weights", [0.2, 0.2, 0.2, 0.15, 0.15, 0.1])
                for i, (scene_name, scene) in enumerate(scenes.items()):
                    support = scene["support"].get(group_name, 0.0)
                    w = weights[i] if i < len(weights) else 0.1
                    group_signal += w * support

            elif self.signal_mode == "max_support":
                # 取最大支持度
                for scene_name, scene in scenes.items():
                    support = scene["support"].get(group_name, 0.0)
                    if abs(support) > abs(group_signal):
                        group_signal = support

            elif self.signal_mode == "vote_count":
                # 计算支持场景数
                positive_votes = 0
                negative_votes = 0
                for scene_name, scene in scenes.items():
                    support = scene["support"].get(group_name, 0.0)
                    if support > 0.1:
                        positive_votes += 1
                    elif support < -0.1:
                        negative_votes += 1
                group_signal = (positive_votes - negative_votes) / 5  # 归一化

            # 分配到具体比分
            for score in scores:
                signals[score] = group_signal

        return dict(signals)

    def _calibrate_signals(self, raw_signals: Dict) -> Dict[Tuple, float]:
        """频率校准"""
        calibrated = {}
        strength = self.calibration_strength

        for score, signal in raw_signals.items():
            freq = HIST_FREQ.get(score, 0.01)
            # 混合：原始信号 + 历史频率
            calibrated[score] = signal * (1 - strength) + freq * strength * 10

        return calibrated


# ============================================================
# 实验配置
# ============================================================

EXPERIMENTS = {
    # 基线（当前配置）
    "baseline": {
        "thresholds": {
            "gf_low": 1.0, "gf_high": 1.8,
            "ga_low": 0.8, "ga_high": 1.5,
            "cs_high": 0.35, "becs_high": 0.35,
            "over25_low": 0.40, "over25_high": 0.55,
            "btts_low": 0.40, "btts_high": 0.55,
            "form_good": 7, "form_bad": 3,
        },
        "signal_mode": "weighted_sum",
        "use_freq_calibration": False,
    },

    # 实验1：放宽阈值
    "exp1_relaxed_th": {
        "thresholds": {
            "gf_low": 1.2, "gf_high": 1.5,
            "ga_low": 1.0, "ga_high": 1.3,
            "cs_high": 0.30, "becs_high": 0.30,
            "over25_low": 0.45, "over25_high": 0.50,
            "btts_low": 0.45, "btts_high": 0.50,
            "form_good": 6, "form_bad": 4,
        },
        "signal_mode": "weighted_sum",
        "use_freq_calibration": False,
    },

    # 实验2：频率校准（弱）
    "exp2_freq_calib_weak": {
        "thresholds": {
            "gf_low": 1.0, "gf_high": 1.8,
            "ga_low": 0.8, "ga_high": 1.5,
            "cs_high": 0.35, "becs_high": 0.35,
            "over25_low": 0.40, "over25_high": 0.55,
            "btts_low": 0.40, "btts_high": 0.55,
            "form_good": 7, "form_bad": 3,
        },
        "signal_mode": "weighted_sum",
        "use_freq_calibration": True,
        "calibration_strength": 0.3,
    },

    # 实验3：频率校准（强）
    "exp3_freq_calib_strong": {
        "thresholds": {
            "gf_low": 1.0, "gf_high": 1.8,
            "ga_low": 0.8, "ga_high": 1.5,
            "cs_high": 0.35, "becs_high": 0.35,
            "over25_low": 0.40, "over25_high": 0.55,
            "btts_low": 0.40, "btts_high": 0.55,
            "form_good": 7, "form_bad": 3,
        },
        "signal_mode": "weighted_sum",
        "use_freq_calibration": True,
        "calibration_strength": 0.6,
    },

    # 实验4：最大支持度模式
    "exp4_max_support": {
        "thresholds": {
            "gf_low": 1.0, "gf_high": 1.8,
            "ga_low": 0.8, "ga_high": 1.5,
            "cs_high": 0.35, "becs_high": 0.35,
            "over25_low": 0.40, "over25_high": 0.55,
            "btts_low": 0.40, "btts_high": 0.55,
            "form_good": 7, "form_bad": 3,
        },
        "signal_mode": "max_support",
        "use_freq_calibration": False,
    },

    # 实验5：投票计数模式
    "exp5_vote_count": {
        "thresholds": {
            "gf_low": 1.0, "gf_high": 1.8,
            "ga_low": 0.8, "ga_high": 1.5,
            "cs_high": 0.35, "becs_high": 0.35,
            "over25_low": 0.40, "over25_high": 0.55,
            "btts_low": 0.40, "btts_high": 0.55,
            "form_good": 7, "form_bad": 3,
        },
        "signal_mode": "vote_count",
        "use_freq_calibration": False,
    },

    # 实验6：放宽阈值 + 频率校准
    "exp6_relaxed_calib": {
        "thresholds": {
            "gf_low": 1.2, "gf_high": 1.5,
            "ga_low": 1.0, "ga_high": 1.3,
            "cs_high": 0.30, "becs_high": 0.30,
            "over25_low": 0.45, "over25_high": 0.50,
            "btts_low": 0.45, "btts_high": 0.50,
            "form_good": 6, "form_bad": 4,
        },
        "signal_mode": "weighted_sum",
        "use_freq_calibration": True,
        "calibration_strength": 0.4,
    },

    # 实验7：投票 + 频率校准
    "exp7_vote_calib": {
        "thresholds": {
            "gf_low": 1.0, "gf_high": 1.8,
            "ga_low": 0.8, "ga_high": 1.5,
            "cs_high": 0.35, "becs_high": 0.35,
            "over25_low": 0.40, "over25_high": 0.55,
            "btts_low": 0.40, "btts_high": 0.55,
            "form_good": 7, "form_bad": 3,
        },
        "signal_mode": "vote_count",
        "use_freq_calibration": True,
        "calibration_strength": 0.4,
    },
}


# ============================================================
# 回测函数
# ============================================================

def run_experiment(config: Dict, cut_date: str = "2026-01-01"):
    """运行单个实验"""

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
    predictor = ConfigurablePredictor(config)
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
            })

        if kind == "L":
            stats[h].add(hg, ag, True, a)
            stats[a].add(ag, hg, False, h)

    # 按日期分组
    by_day = defaultdict(list)
    for p in match_packs:
        by_day[p["date"]].append(p)

    # 模拟2串1双选
    total_cost = 0.0
    total_payout = 0.0
    n_tickets = 0
    n_hits = 0

    for day in sorted(by_day):
        day_packs = by_day[day]
        if len(day_packs) < 2:
            continue

        selected = sorted(day_packs, key=lambda p: -p["max_signal"])[:2]

        all_picks = []
        for p in selected:
            picks = []
            for score, sig in p["sorted_scores"][:2]:
                odds = p["odds"].get(score, 999)
                picks.append((score, odds, p["actual"]))
            all_picks.append(picks)

        all_bets = list(product(*all_picks))
        cost = len(all_bets) * 2.0
        payout = 0.0

        for combo in all_bets:
            all_correct = all(score == actual for score, odds, actual in combo)
            if all_correct:
                combo_odds = 1.0
                for score, odds, actual in combo:
                    combo_odds *= odds
                payout += 2.0 * combo_odds

        total_cost += cost
        total_payout += payout
        n_tickets += 1
        if payout > 0:
            n_hits += 1

    # 统计预测分布
    top1_dist = Counter()
    for p in match_packs:
        if p["sorted_scores"]:
            top1_dist[p["sorted_scores"][0][0]] += 1

    profit = (total_payout - total_cost) / total_cost if total_cost > 0 else -1

    return {
        "n_matches": len(match_packs),
        "n_tickets": n_tickets,
        "n_hits": n_hits,
        "total_cost": total_cost,
        "total_payout": total_payout,
        "profit": profit,
        "top1_dist": top1_dist,
    }


# ============================================================
# 运行所有实验
# ============================================================

print("\n" + "=" * 100)
print("运行对照实验")
print("=" * 100)

results = {}
for exp_name, config in EXPERIMENTS.items():
    print(f"  运行 {exp_name}...", end=" ")
    result = run_experiment(config)
    results[exp_name] = result
    if result:
        print(f"完成. 票数={result['n_tickets']}, 命中={result['n_hits']}, 盈利率={result['profit']*100:+.1f}%")
    else:
        print("失败")


# ============================================================
# 结果汇总
# ============================================================

print("\n" + "=" * 100)
print("实验结果汇总")
print("=" * 100)

print(f"\n{'实验名称':<25} {'票数':>8} {'命中':>6} {'成本':>10} {'派彩':>10} {'盈利率':>10}")
print("-" * 80)

for exp_name, result in sorted(results.items(), key=lambda x: -x[1]["profit"] if x[1] else -999):
    if result:
        mark = "✅" if result["profit"] > 0 else ""
        print(f"{exp_name:<25} {result['n_tickets']:>8} {result['n_hits']:>6} "
              f"{result['total_cost']:>10.0f} {result['total_payout']:>10.1f} "
              f"{result['profit']*100:>+9.1f}% {mark}")


# ============================================================
# 预测分布分析
# ============================================================

print("\n" + "=" * 100)
print("预测分布对比（Top1 预测次数）")
print("=" * 100)

# 选择几个关键实验对比
key_exps = ["baseline", "exp6_relaxed_calib", "exp7_vote_calib"]
key_scores = [(1,1), (2,1), (1,0), (0,0), (2,0), (0,1), (0,2), (2,2)]

print(f"\n{'比分':>10}", end="")
for exp in key_exps:
    print(f" {exp[:12]:>12}", end="")
print()
print("-" * 60)

for score in key_scores:
    print(f"{str(score):>10}", end="")
    for exp in key_exps:
        if results.get(exp) and results[exp].get("top1_dist"):
            cnt = results[exp]["top1_dist"].get(score, 0)
            print(f" {cnt:>12}", end="")
        else:
            print(f" {'N/A':>12}", end="")
    print()
