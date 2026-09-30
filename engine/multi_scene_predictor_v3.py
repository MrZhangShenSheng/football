# -*- coding: utf-8 -*-
"""
多场景博弈比分预测模型 v3（生产版）

包含：
1. 比分预测器（baseline配置）
2. 置信度计算（6维度）
3. 动态倍率策略（高价值2倍）

验证结果：
- 固定1倍：收益+133.5%，回撤74.6%
- 高价值2倍：收益+269.8%，回撤93.4%

验证脚本：engine/scripts/research/confidence_multiplier_model.py

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

# 历史频率分布
HIST_FREQ = {
    (1, 1): 0.12, (2, 1): 0.09, (1, 0): 0.09, (1, 2): 0.08,
    (0, 1): 0.07, (0, 0): 0.07, (2, 0): 0.07, (2, 2): 0.05,
    (0, 2): 0.05, (3, 0): 0.04, (3, 1): 0.04, (3, 2): 0.03,
    (1, 3): 0.03, (2, 3): 0.02, (4, 0): 0.02, (4, 1): 0.02,
    (0, 3): 0.02, (3, 3): 0.01,
}

# 场景阈值（baseline配置）
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

# 频率校准强度
CALIBRATION_STRENGTH = 0.5

# 倍率策略阈值
MULTIPLIER_ODDS_VALUE_THRESHOLD = 0.5  # 赔率价值高于此值时使用2倍


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
        """近3场状态分：胜+3 平+1 负+0，满分9"""
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
        recent_gd=ts.recent_gd.copy() if hasattr(ts, 'recent_gd') else [],
        recent=ts.recent.copy() if hasattr(ts, 'recent') else [],
    )


# ============================================================
# 比分预测器（带置信度）
# ============================================================

class MultiScenePredictorV3:
    """多场景博弈预测器 v3（带置信度和动态倍率）"""

    def __init__(self):
        self.th = THRESHOLDS
        self.calibration_strength = CALIBRATION_STRENGTH

    def predict(self, home: TeamData, away: TeamData, score_odds: Dict = None) -> Dict:
        """
        预测比分和置信度

        Args:
            home: 主队数据
            away: 客队数据
            score_odds: 比分赔率字典 {(h,a): odds, ...}，用于计算赔率价值

        Returns:
            {
                "sorted_scores": [((h,a), signal), ...],
                "scenes": {...},
                "confidence": {...},
                "multiplier": 1 or 2
            }
        """
        # 1. 场景分析
        scenes = self._analyze_scenes(home, away)

        # 2. 计算信号
        raw_signals = self._calc_raw_signals(scenes)
        calibrated = self._freq_calibrate(raw_signals)
        sorted_scores = sorted(calibrated.items(), key=lambda x: -x[1])

        # 3. 计算置信度
        confidence = self._calc_confidence(scenes, sorted_scores, home, away, score_odds)

        # 4. 计算倍率
        multiplier = self._calc_multiplier(confidence)

        return {
            "sorted_scores": sorted_scores,
            "scenes": scenes,
            "confidence": confidence,
            "multiplier": multiplier,
        }

    def _analyze_scenes(self, home: TeamData, away: TeamData) -> Dict:
        """分析4个场景"""
        scenes = {}
        th = self.th

        # S1: 攻防对比
        s1 = {"support": defaultdict(float), "triggered": False, "direction": None}
        if home.gf_home_avg > th["gf_high"] and away.ga_away_avg > th["ga_high"]:
            s1["support"]["home_win"] = 0.4
            s1["triggered"] = True
            s1["direction"] = "home"
        elif away.gf_away_avg > th["gf_high"] and home.ga_home_avg > th["ga_high"]:
            s1["support"]["away_win"] = 0.3
            s1["triggered"] = True
            s1["direction"] = "away"
        elif home.gf_avg < th["gf_low"] and away.gf_avg < th["gf_low"]:
            s1["support"]["low_score"] = 0.4
            s1["support"]["draw"] = 0.3
            s1["triggered"] = True
            s1["direction"] = "draw"
        elif home.gf_avg > th["gf_high"] and away.gf_avg > th["gf_high"]:
            s1["support"]["high_score"] = 0.4
            s1["triggered"] = True
            s1["direction"] = "high"
        else:
            s1["support"]["draw"] = 0.2
            s1["direction"] = "draw"
        scenes["S1"] = s1

        # S2: 近期状态
        s2 = {"support": defaultdict(float), "triggered": False, "direction": None}
        home_form = home.form_score()
        away_form = away.form_score()
        if home_form >= th["form_good"] and away_form <= th["form_bad"]:
            s2["support"]["home_win"] = 0.4
            s2["triggered"] = True
            s2["direction"] = "home"
        elif away_form >= th["form_good"] and home_form <= th["form_bad"]:
            s2["support"]["away_win"] = 0.4
            s2["triggered"] = True
            s2["direction"] = "away"
        elif home_form <= th["form_bad"] and away_form <= th["form_bad"]:
            s2["support"]["low_score"] = 0.3
            s2["support"]["draw"] = 0.3
            s2["triggered"] = True
            s2["direction"] = "draw"
        else:
            s2["support"]["draw"] = 0.2
            s2["direction"] = "draw"
        scenes["S2"] = s2

        # S3: 零封能力
        s3 = {"support": defaultdict(float), "triggered": False, "direction": None}
        if home.cs_rate > th["cs_rate_high"] and away.becs_rate > th["becs_rate_high"]:
            s3["support"]["home_clean"] = 0.4
            s3["triggered"] = True
            s3["direction"] = "home_clean"
        if away.cs_rate > th["cs_rate_high"] and home.becs_rate > th["becs_rate_high"]:
            s3["support"]["away_clean"] = 0.4
            s3["triggered"] = True
            s3["direction"] = "away_clean" if s3["direction"] is None else "both_clean"
        if home.becs_rate > th["becs_rate_high"] and away.becs_rate > th["becs_rate_high"]:
            s3["support"]["low_score"] = 0.3
            s3["triggered"] = True
            if s3["direction"] is None:
                s3["direction"] = "low"
        scenes["S3"] = s3

        # S4: 大球倾向
        s4 = {"support": defaultdict(float), "triggered": False, "direction": None}
        if home.over25_rate > th["over25_high"] and away.over25_rate > th["over25_high"]:
            s4["support"]["high_score"] = 0.4
            s4["triggered"] = True
            s4["direction"] = "high"
        if home.btts_rate > th["btts_high"] and away.btts_rate > th["btts_high"]:
            s4["support"]["both_score"] = 0.3
            s4["triggered"] = True
            if s4["direction"] is None:
                s4["direction"] = "btts"
        scenes["S4"] = s4

        return scenes

    def _calc_raw_signals(self, scenes: Dict) -> Dict[Tuple, float]:
        """将场景效果映射到具体比分"""
        signals = defaultdict(float)

        effect_mapping = {
            "home_win": {(1, 0): 0.4, (2, 0): 0.3, (2, 1): 0.3},
            "away_win": {(0, 1): 0.4, (0, 2): 0.3, (1, 2): 0.3},
            "draw": {(1, 1): 0.5, (0, 0): 0.3, (2, 2): 0.2},
            "low_score": {(0, 0): 0.4, (1, 0): 0.2, (0, 1): 0.2, (1, 1): 0.2},
            "high_score": {(2, 2): 0.3, (3, 1): 0.2, (2, 3): 0.2, (3, 2): 0.2, (1, 3): 0.1},
            "home_clean": {(1, 0): 0.4, (2, 0): 0.4, (3, 0): 0.2},
            "away_clean": {(0, 1): 0.4, (0, 2): 0.4, (0, 3): 0.2},
            "both_score": {(1, 1): 0.3, (2, 1): 0.2, (1, 2): 0.2, (2, 2): 0.2, (3, 2): 0.1},
        }

        for scene_id, scene in scenes.items():
            for effect, strength in scene["support"].items():
                if effect in effect_mapping:
                    for score, weight in effect_mapping[effect].items():
                        signals[score] += strength * weight

        return dict(signals)

    def _freq_calibrate(self, raw_signals: Dict[Tuple, float]) -> Dict[Tuple, float]:
        """频率校准（不做归一化，与研究脚本一致）"""
        calibrated = {}
        alpha = self.calibration_strength

        # 合并所有可能的比分
        all_scores = set(raw_signals.keys()) | set(HIST_FREQ.keys())

        for score in all_scores:
            raw = raw_signals.get(score, 0)
            hist = HIST_FREQ.get(score, 0)
            calibrated[score] = (1 - alpha) * raw + alpha * hist

        return calibrated

    def _calc_confidence(self, scenes: Dict, sorted_scores: List,
                         home: TeamData, away: TeamData, score_odds: Dict) -> Dict:
        """
        计算6维置信度

        返回:
            {
                "signal_strength": float,      # 信号强度 (0-1)
                "signal_concentration": float, # 信号集中度 (0-1)
                "scene_consistency": float,    # 场景一致性 (0-1)
                "scene_strength": float,       # 场景强度 (0-1)
                "data_sufficiency": float,     # 数据充足度 (0-1)
                "odds_value": float,           # 赔率价值 (0-1)
                "overall": float,              # 综合置信度 (0-1)
            }
        """
        # 1. 信号强度：Top1信号值（归一化到0-1）
        top1_signal = sorted_scores[0][1] if sorted_scores else 0
        signal_strength = min(top1_signal / 0.2, 1.0)  # 0.2视为满分

        # 2. 信号集中度：Top1与Top2的差距
        if len(sorted_scores) >= 2:
            gap = sorted_scores[0][1] - sorted_scores[1][1]
            signal_concentration = min(gap / 0.05, 1.0)  # 0.05视为满分
        else:
            signal_concentration = 1.0

        # 3. 场景一致性：有多少场景支持同一方向
        directions = [s.get("direction") for s in scenes.values() if s.get("triggered")]
        if directions:
            from collections import Counter
            most_common = Counter(directions).most_common(1)[0]
            scene_consistency = most_common[1] / len(directions)
        else:
            scene_consistency = 0.0

        # 4. 场景强度：所有场景支持度之和
        total_strength = sum(
            sum(s["support"].values())
            for s in scenes.values()
        )
        scene_strength = min(total_strength / 1.5, 1.0)  # 1.5视为满分

        # 5. 数据充足度：两队历史场次
        min_n = min(home.n, away.n)
        data_sufficiency = min(min_n / 20, 1.0)  # 20场视为满分

        # 6. 赔率价值：预测概率 vs 隐含概率
        odds_value = 0.5  # 默认中等
        if score_odds and sorted_scores:
            top1_score = sorted_scores[0][0]
            top1_prob = sorted_scores[0][1]
            actual_odds = score_odds.get(top1_score, 0)
            if actual_odds > 0:
                implied_prob = 1 / actual_odds
                # 预测概率 / 隐含概率，越高越有价值
                value_ratio = top1_prob / implied_prob
                odds_value = min(value_ratio, 1.0)

        # 综合置信度（加权平均）
        overall = (
            0.15 * signal_strength +
            0.10 * signal_concentration +
            0.20 * scene_consistency +
            0.20 * scene_strength +
            0.05 * data_sufficiency +
            0.30 * odds_value  # 赔率价值权重最高
        )

        return {
            "signal_strength": signal_strength,
            "signal_concentration": signal_concentration,
            "scene_consistency": scene_consistency,
            "scene_strength": scene_strength,
            "data_sufficiency": data_sufficiency,
            "odds_value": odds_value,
            "overall": overall,
        }

    def _calc_multiplier(self, confidence: Dict) -> int:
        """
        计算投注倍率

        规则：赔率价值 > 0.5 时使用2倍，否则1倍
        """
        if confidence["odds_value"] > MULTIPLIER_ODDS_VALUE_THRESHOLD:
            return 2
        return 1


# ============================================================
# 串关倍率计算
# ============================================================

def calc_parlay_multiplier(match_predictions: List[Dict]) -> int:
    """
    计算串关投注的倍率

    Args:
        match_predictions: 各场比赛的预测结果列表

    Returns:
        倍率 (1 或 2)
    """
    if not match_predictions:
        return 1

    # 取各场赔率价值的平均值
    avg_odds_value = sum(
        p["confidence"]["odds_value"]
        for p in match_predictions
    ) / len(match_predictions)

    if avg_odds_value > MULTIPLIER_ODDS_VALUE_THRESHOLD:
        return 2
    return 1


# ============================================================
# 导出接口
# ============================================================

__all__ = [
    "TeamData",
    "team_stats_to_team_data",
    "MultiScenePredictorV3",
    "calc_parlay_multiplier",
    "HIST_FREQ",
    "THRESHOLDS",
]
