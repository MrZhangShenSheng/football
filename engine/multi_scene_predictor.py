# -*- coding: utf-8 -*-
"""多场景博弈比分预测模型 v2（生产版）

最优配置：baseline
验证结果：滚动投注 1000→1615.9 元，收益 +61.6%，命中17次（8.0%）

配置参数：
- 阈值：gf_low=1.0, gf_high=1.8, ga_low=0.8, ga_high=1.5
- 零封阈值：cs_rate_high=0.35, becs_rate_high=0.35
- 大球阈值：btts_high=0.55, over25_high=0.55
- 状态阈值：form_good=7, form_bad=3
- 频率校准强度：0.5

验证脚本：engine/scripts/research/multi_scene_optimize_v2.py

作者：sszhang
日期：2026-09-30
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

# ============================================================
# 配置常量
# ============================================================

# 阈值配置（baseline 最优配置）
THRESHOLDS = {
    "gf_low": 1.0,
    "gf_high": 1.8,
    "ga_low": 0.8,
    "ga_high": 1.5,
    "cs_rate_high": 0.35,
    "becs_rate_high": 0.35,
    "btts_high": 0.55,
    "over25_high": 0.55,
    "form_good": 7,
    "form_bad": 3,
}

# 历史频率（用于校准）
HIST_FREQ = {
    (1, 1): 0.12, (2, 1): 0.09, (1, 0): 0.09, (1, 2): 0.08,
    (0, 1): 0.07, (0, 0): 0.07, (2, 0): 0.07, (2, 2): 0.05,
    (0, 2): 0.05, (3, 0): 0.04, (3, 1): 0.04, (3, 2): 0.03,
    (1, 3): 0.03, (2, 3): 0.02, (4, 0): 0.02, (4, 1): 0.02,
    (0, 3): 0.02, (3, 3): 0.01,
}

# 频率校准强度
CALIBRATION_STRENGTH = 0.5


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

    def form_score(self) -> int:
        """近3场状态分：胜+3 平+1 负+0"""
        recent3 = self.recent[-3:] if len(self.recent) >= 3 else self.recent
        score = 0
        for _, scored, conceded in recent3:
            if scored > conceded:
                score += 3
            elif scored == conceded:
                score += 1
        return score


def team_stats_to_team_data(ts) -> TeamData:
    """将 sfm.TeamStats 转换为 TeamData"""
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
# 多场景预测器 v2（生产版）
# ============================================================

class MultiScenePredictorV2:
    """多场景博弈预测器 v2（baseline 最优配置）"""

    def __init__(self):
        self.th = THRESHOLDS
        self.calibration_strength = CALIBRATION_STRENGTH

    def predict(self, home: TeamData, away: TeamData) -> Dict:
        """预测所有比分的信号

        返回:
            {
                "predictions": {(h,a): signal, ...},
                "sorted_scores": [((h,a), signal), ...],
                "scenes": {...}
            }
        """
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
        """分析各场景，返回场景支持度"""
        scenes = {}
        th = self.th

        # S1: 攻防对比
        s1 = {"support": defaultdict(float)}
        home_attack = home.gf_home_avg
        away_attack = away.gf_away_avg
        home_defense = home.ga_home_avg
        away_defense = away.ga_away_avg

        if home_attack > th["gf_high"] and away_defense > th["ga_high"]:
            s1["support"]["home_win"] = 0.4
        elif away_attack > th["gf_high"] and home_defense > th["ga_high"]:
            s1["support"]["away_win"] = 0.3
        elif home_attack < th["gf_low"] and away_attack < th["gf_low"]:
            s1["support"]["low_score"] = 0.4
            s1["support"]["draw"] = 0.3
        elif home_attack > th["gf_high"] and away_attack > th["gf_high"]:
            s1["support"]["high_score"] = 0.4
        else:
            s1["support"]["draw"] = 0.2
        scenes["S1"] = s1

        # S2: 近期状态
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

        # S3: 零封特征
        s3 = {"support": defaultdict(float)}
        if home.cs_rate > th["cs_rate_high"] and away.becs_rate > th["becs_rate_high"]:
            s3["support"]["home_clean"] = 0.4
        if away.cs_rate > th["cs_rate_high"] and home.becs_rate > th["becs_rate_high"]:
            s3["support"]["away_clean"] = 0.4
        if home.becs_rate > th["becs_rate_high"] and away.becs_rate > th["becs_rate_high"]:
            s3["support"]["low_score"] = 0.3
        scenes["S3"] = s3

        # S4: 大球特征
        s4 = {"support": defaultdict(float)}
        if home.over25_rate > th["over25_high"] and away.over25_rate > th["over25_high"]:
            s4["support"]["high_score"] = 0.4
        if home.btts_rate > th["btts_high"] and away.btts_rate > th["btts_high"]:
            s4["support"]["both_score"] = 0.3
        scenes["S4"] = s4

        return scenes

    def _calc_raw_signals(self, scenes: Dict) -> Dict[Tuple, float]:
        """从场景支持度计算各比分的原始信号"""
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

    def _freq_calibrate(self, raw_signals: Dict[Tuple, float]) -> Dict[Tuple, float]:
        """频率校准：将原始信号与历史频率混合"""
        calibrated = {}
        strength = self.calibration_strength

        # 归一化原始信号
        total_raw = sum(raw_signals.values()) or 1.0

        for score, freq in HIST_FREQ.items():
            raw = raw_signals.get(score, 0) / total_raw
            # 混合：(1-strength)*raw + strength*freq
            calibrated[score] = (1 - strength) * raw + strength * freq

        # 归一化
        total = sum(calibrated.values()) or 1.0
        for score in calibrated:
            calibrated[score] /= total

        return calibrated


# ============================================================
# 便捷函数
# ============================================================

_predictor = None

def get_predictor() -> MultiScenePredictorV2:
    """获取预测器单例"""
    global _predictor
    if _predictor is None:
        _predictor = MultiScenePredictorV2()
    return _predictor


def predict_scores(home: TeamData, away: TeamData) -> List[Tuple[Tuple[int, int], float]]:
    """预测比分，返回按信号排序的列表"""
    predictor = get_predictor()
    result = predictor.predict(home, away)
    return result["sorted_scores"]
