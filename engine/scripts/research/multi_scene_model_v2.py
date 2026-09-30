# -*- coding: utf-8 -*-
"""多场景博弈比分预测模型 v2（exp6配置）

优化版本：放宽阈值 + 频率校准
验证结果：7/8切分点正收益，总盈利率+17.1%，命中率8.2%

设计文档：docs/multi_scene_consensus_model_v0.3.md
验证脚本：engine/scripts/research/exp6_stability_test.py

作者：sszhang
日期：2026-09-30
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

# ============================================================
# 常量定义
# ============================================================

# 历史频率（用于校准）
HIST_FREQ = {
    (1, 1): 0.12, (2, 1): 0.09, (1, 0): 0.09, (1, 2): 0.08,
    (0, 1): 0.07, (0, 0): 0.07, (2, 0): 0.07, (2, 2): 0.05,
    (0, 2): 0.05, (3, 0): 0.04, (3, 1): 0.04, (3, 2): 0.03,
    (1, 3): 0.03, (2, 3): 0.02, (4, 0): 0.02, (4, 1): 0.02,
    (0, 3): 0.02, (3, 3): 0.01,
}

# 放宽后的阈值（exp6配置）
THRESHOLDS = {
    "gf_low": 1.2,
    "gf_high": 1.5,
    "ga_low": 1.0,
    "ga_high": 1.3,
    "cs_rate_high": 0.25,
    "becs_rate_high": 0.25,
    "btts_low": 0.45,
    "btts_high": 0.50,
    "over25_low": 0.45,
    "over25_high": 0.50,
    "form_good": 6,
    "form_bad": 4,
    "fragility_high": 1.5,
    "fragility_low": 1.0,
}

# 频率校准强度
CALIBRATION_STRENGTH = 0.7


# ============================================================
# 数据结构
# ============================================================

@dataclass
class TeamData:
    """球队数据"""
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
    def gf_avg(self) -> float:
        return self.gf / max(self.n, 1)

    @property
    def ga_avg(self) -> float:
        return self.ga / max(self.n, 1)

    @property
    def win_rate(self) -> float:
        return self.win / max(self.n, 1)

    @property
    def cs_rate(self) -> float:
        return self.cs / max(self.n, 1)

    @property
    def becs_rate(self) -> float:
        return self.becs / max(self.n, 1)

    @property
    def btts_rate(self) -> float:
        return self.btts / max(self.n, 1)

    @property
    def over25_rate(self) -> float:
        return self.over25 / max(self.n, 1)

    @property
    def gf_home_avg(self) -> float:
        return self.gf_home / max(self.gf_home_n, 1)

    @property
    def gf_away_avg(self) -> float:
        return self.gf_away / max(self.gf_away_n, 1)

    @property
    def ga_home_avg(self) -> float:
        return self.ga_home / max(self.ga_home_n, 1)

    @property
    def ga_away_avg(self) -> float:
        return self.ga_away / max(self.ga_away_n, 1)

    @property
    def momentum(self) -> float:
        if not self.recent_gd:
            return 0.0
        recent_avg = sum(self.recent_gd) / len(self.recent_gd)
        hist_avg = self.gd / max(self.n, 1)
        return recent_avg - hist_avg

    def form_score(self) -> int:
        recent3 = self.recent[-3:] if len(self.recent) >= 3 else self.recent
        score = 0
        for _, scored, conceded in recent3:
            if scored > conceded:
                score += 3
            elif scored == conceded:
                score += 1
        return score

    def ga_variance(self) -> float:
        recent = self.recent[-5:] if len(self.recent) >= 5 else self.recent
        if len(recent) < 2:
            return 0.0
        ga_list = [r[2] for r in recent]
        mean_ga = sum(ga_list) / len(ga_list)
        variance = sum((g - mean_ga) ** 2 for g in ga_list) / len(ga_list)
        return variance


# ============================================================
# 多场景预测器 v2（exp6配置）
# ============================================================

class MultiScenePredictorV2:
    """多场景博弈预测器 v2（放宽阈值 + 频率校准）"""

    def __init__(self):
        self.th = THRESHOLDS
        self.calibration_strength = CALIBRATION_STRENGTH

    def predict(self, home: TeamData, away: TeamData) -> Dict:
        """预测所有比分的信号"""

        # 1. 场景分析
        scenes = self._analyze_scenes(home, away)

        # 2. 计算原始信号
        raw_signals = self._calc_raw_signals(scenes)

        # 3. 频率校准
        calibrated = self._freq_calibrate(raw_signals)

        # 4. 排序
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

        s1 = {"support": defaultdict(float)}
        if home_attack > th["gf_high"] and away_defense > th["ga_high"]:
            s1["judgment"] = "主攻压制"
            s1["support"]["home_win"] = 0.4
            s1["support"]["home_goals_2plus"] = 0.3
        elif away_attack > th["gf_high"] and home_defense > th["ga_high"]:
            s1["judgment"] = "客攻有威胁"
            s1["support"]["away_win"] = 0.3
            s1["support"]["away_goals_2plus"] = 0.3
        elif home_attack < th["gf_low"] and away_attack < th["gf_low"]:
            s1["judgment"] = "双方低迷"
            s1["support"]["low_score"] = 0.4
            s1["support"]["draw"] = 0.3
        elif home_attack > th["gf_high"] and away_attack > th["gf_high"]:
            s1["judgment"] = "双方互爆"
            s1["support"]["high_score"] = 0.4
        else:
            s1["judgment"] = "均衡"
            s1["support"]["draw"] = 0.2
        scenes["S1"] = s1

        # S2: 近期状态
        home_form = home.form_score()
        away_form = away.form_score()

        s2 = {"support": defaultdict(float)}
        if home_form >= th["form_good"] and away_form <= th["form_bad"]:
            s2["judgment"] = "主强客弱"
            s2["support"]["home_win"] = 0.4
        elif away_form >= th["form_good"] and home_form <= th["form_bad"]:
            s2["judgment"] = "客强主弱"
            s2["support"]["away_win"] = 0.4
        elif home_form <= th["form_bad"] and away_form <= th["form_bad"]:
            s2["judgment"] = "双方低迷"
            s2["support"]["low_score"] = 0.3
            s2["support"]["draw"] = 0.3
        elif home_form >= th["form_good"] and away_form >= th["form_good"]:
            s2["judgment"] = "双方强势"
            s2["support"]["high_score"] = 0.3
        else:
            s2["judgment"] = "状态接近"
            s2["support"]["draw"] = 0.2
        scenes["S2"] = s2

        # S3: 主客场特性
        home_diff = home.gf_home_avg - home.gf_away_avg
        away_diff = away.gf_away_avg - away.gf_home_avg

        s3 = {"support": defaultdict(float)}
        if home_diff > 0.4:
            s3["judgment"] = "主场龙"
            s3["support"]["home_win"] = 0.3
        elif away_diff > 0.3:
            s3["judgment"] = "客场龙"
            s3["support"]["away_win"] = 0.3
        else:
            s3["judgment"] = "主客场一般"
            s3["support"]["draw"] = 0.1
        scenes["S3"] = s3

        # S4: 进球模式
        s4 = {"support": defaultdict(float)}
        if home.becs_rate > th["becs_rate_high"] and away.becs_rate > th["becs_rate_high"]:
            s4["judgment"] = "零零倾向"
            s4["support"]["zero_zero"] = 0.3
            s4["support"]["low_score"] = 0.3
        elif home.over25_rate > th["over25_high"] and away.over25_rate > th["over25_high"]:
            s4["judgment"] = "互爆倾向"
            s4["support"]["high_score"] = 0.4
        elif home.cs_rate > th["cs_rate_high"] and away.becs_rate > th["becs_rate_high"]:
            s4["judgment"] = "主零封倾向"
            s4["support"]["home_clean"] = 0.3
        elif away.cs_rate > th["cs_rate_high"] and home.becs_rate > th["becs_rate_high"]:
            s4["judgment"] = "客零封倾向"
            s4["support"]["away_clean"] = 0.3
        else:
            s4["judgment"] = "常规"
            s4["support"]["draw"] = 0.1
        scenes["S4"] = s4

        # S5: 防守稳定性
        home_fragility = home.ga_avg + home.ga_variance() * 0.3
        away_fragility = away.ga_avg + away.ga_variance() * 0.3

        s5 = {"support": defaultdict(float)}
        if home_fragility > th["fragility_high"] and away_fragility > th["fragility_high"]:
            s5["judgment"] = "双方脆弱"
            s5["support"]["high_score"] = 0.4
        elif home_fragility > th["fragility_high"]:
            s5["judgment"] = "主守脆弱"
            s5["support"]["away_goals_2plus"] = 0.3
        elif away_fragility > th["fragility_high"]:
            s5["judgment"] = "客守脆弱"
            s5["support"]["home_goals_2plus"] = 0.3
        elif home_fragility < th["fragility_low"] and away_fragility < th["fragility_low"]:
            s5["judgment"] = "双方稳固"
            s5["support"]["low_score"] = 0.3
        else:
            s5["judgment"] = "防守一般"
            s5["support"]["draw"] = 0.1
        scenes["S5"] = s5

        # S6: 比赛节奏
        combined_btts = (home.btts_rate + away.btts_rate) / 2
        combined_over25 = (home.over25_rate + away.over25_rate) / 2

        s6 = {"support": defaultdict(float)}
        if combined_btts > th["btts_high"] and combined_over25 > th["over25_high"]:
            s6["judgment"] = "开放型"
            s6["support"]["high_score"] = 0.3
            s6["support"]["btts"] = 0.3
        elif combined_btts < th["btts_low"] and combined_over25 < th["over25_low"]:
            s6["judgment"] = "保守型"
            s6["support"]["low_score"] = 0.3
        else:
            s6["judgment"] = "节奏一般"
            s6["support"]["draw"] = 0.1
        scenes["S6"] = s6

        return scenes

    def _calc_raw_signals(self, scenes: Dict) -> Dict[Tuple[int, int], float]:
        """计算原始信号"""
        signals = defaultdict(float)

        # 汇总场景支持
        total_support = defaultdict(float)
        for scene_name, scene in scenes.items():
            for key, val in scene["support"].items():
                total_support[key] += val

        # 映射到具体比分
        for score in HIST_FREQ.keys():
            h, a = score
            total = h + a
            signal = 0.0

            # home_win 支持
            if h > a:
                signal += total_support.get("home_win", 0) * 0.3

            # away_win 支持
            if a > h:
                signal += total_support.get("away_win", 0) * 0.3

            # draw 支持
            if h == a:
                signal += total_support.get("draw", 0) * 0.5

            # low_score 支持
            if total <= 2:
                signal += total_support.get("low_score", 0) * 0.4

            # high_score 支持
            if total >= 4:
                signal += total_support.get("high_score", 0) * 0.4

            # zero_zero 支持
            if score == (0, 0):
                signal += total_support.get("zero_zero", 0) * 0.5

            # home_clean 支持
            if a == 0 and h > 0:
                signal += total_support.get("home_clean", 0) * 0.4

            # away_clean 支持
            if h == 0 and a > 0:
                signal += total_support.get("away_clean", 0) * 0.4

            # home_goals_2plus 支持
            if h >= 2:
                signal += total_support.get("home_goals_2plus", 0) * 0.3

            # away_goals_2plus 支持
            if a >= 2:
                signal += total_support.get("away_goals_2plus", 0) * 0.3

            # btts 支持
            if h > 0 and a > 0:
                signal += total_support.get("btts", 0) * 0.3

            signals[score] = signal

        return dict(signals)

    def _freq_calibrate(self, raw_signals: Dict) -> Dict[Tuple[int, int], float]:
        """频率校准"""
        calibrated = {}
        alpha = self.calibration_strength

        for score, signal in raw_signals.items():
            freq = HIST_FREQ.get(score, 0.01)
            calibrated[score] = signal * (1 - alpha) + freq * alpha

        return calibrated


# ============================================================
# 便捷函数
# ============================================================

def create_predictor() -> MultiScenePredictorV2:
    """创建预测器实例"""
    return MultiScenePredictorV2()
