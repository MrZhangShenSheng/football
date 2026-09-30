# -*- coding: utf-8 -*-
"""
多场景博弈比分预测模型 v4b（生产版）

配置：A+C(平局+主队)加成，系数1.25
回测结果（2025-10-01 ~ 2026-09-28）：
- 收益率：+341.4%
- 命中率：15.4%（39/253）
- 最大回撤：11.7%

验证脚本：engine/scripts/research/v4b_full_backtest.py
明细文件：engine/scripts/research/v4b_detail_verify.py

作者：sszhang
日期：2026-09-30
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

__all__ = [
    "TeamData",
    "team_stats_to_team_data",
    "PredictorV4b",
    "HIST_FREQ",
    "THRESHOLDS",
    "DRAW_BOOST_COMBOS",
    "HOME_BOOST_COMBOS",
    "BOOST_FACTOR",
]

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

# 场景阈值
THRESHOLDS = {
    "gf_low": 1.0,       # 场均进球低于此值为低产
    "gf_high": 1.8,      # 场均进球高于此值为高产
    "ga_low": 0.8,       # 场均失球低于此值为防守好
    "ga_high": 1.5,      # 场均失球高于此值为防守差
    "cs_rate_high": 0.35,    # 零封率阈值
    "becs_rate_high": 0.35,  # 被零封率阈值
    "btts_high": 0.55,       # BTTS率阈值
    "over25_high": 0.55,     # 大球率阈值
    "form_good": 7,      # 近3场积分>=7为状态好
    "form_bad": 3,       # 近3场积分<=3为状态差
}

# A类：平局加成（高命中率状态组合）
DRAW_BOOST_COMBOS = {
    (3, 5): 0.4,   # 命中率58.3%
    (5, 2): 0.4,   # 命中率54.5%
    (5, 5): 0.35,  # 命中率53.8%
    (2, 2): 0.3,   # 命中率40.0%
    (2, 1): 0.3,   # 命中率40.0%
    (4, 5): 0.25,  # 命中率38.5%
    (2, 5): 0.25,  # 命中率35.7%
    (7, 7): 0.2,   # 命中率33.3%
    (5, 4): 0.2,   # 命中率29.2%
    (1, 3): 0.2,   # 命中率26.1%
    (4, 2): 0.15,  # 命中率25.0%
    (5, 3): 0.15,  # 命中率25.0%
    (1, 2): 0.15,  # 命中率25.0%
    (4, 4): 0.15,  # 命中率22.6%
    (5, 1): 0.15,  # 命中率22.2%
    (4, 1): 0.15,  # 命中率22.2%
    (1, 1): 0.15,  # 命中率21.1%
}

# C类：主队主导加成（主队状态好，客队状态差）
HOME_BOOST_COMBOS = {
    (9, 0): 0.2,   # 主队9分，客队0分
    (9, 1): 0.15,
    (9, 2): 0.15,
    (9, 3): 0.1,
    (7, 0): 0.15,
    (7, 1): 0.1,
    (7, 2): 0.1,
    (7, 3): 0.1,
}

# 加成系数（经回测验证的最佳值）
BOOST_FACTOR = 1.25


# ============================================================
# 数据结构
# ============================================================

@dataclass
class TeamData:
    """球队数据"""
    n: int = 0                    # 总场次
    gf: int = 0                   # 总进球
    ga: int = 0                   # 总失球
    win: int = 0                  # 胜场
    gd: int = 0                   # 净胜球
    cs: int = 0                   # 零封场次
    becs: int = 0                 # 被零封场次
    btts: int = 0                 # 双方都有进球场次
    over25: int = 0               # 大2.5球场次
    gf_home: int = 0              # 主场进球
    gf_home_n: int = 0            # 主场场次
    gf_away: int = 0              # 客场进球
    gf_away_n: int = 0            # 客场场次
    ga_home: int = 0              # 主场失球
    ga_home_n: int = 0            # 主场场次
    ga_away: int = 0              # 客场失球
    ga_away_n: int = 0            # 客场场次
    recent_gd: List[int] = field(default_factory=list)   # 近期净胜球
    recent: List[Tuple] = field(default_factory=list)    # 近期战绩 [(对手, 进球, 失球), ...]

    @property
    def gf_avg(self) -> float:
        """场均进球"""
        return self.gf / max(self.n, 1)

    @property
    def ga_avg(self) -> float:
        """场均失球"""
        return self.ga / max(self.n, 1)

    @property
    def cs_rate(self) -> float:
        """零封率"""
        return self.cs / max(self.n, 1)

    @property
    def becs_rate(self) -> float:
        """被零封率"""
        return self.becs / max(self.n, 1)

    @property
    def btts_rate(self) -> float:
        """双方进球率"""
        return self.btts / max(self.n, 1)

    @property
    def over25_rate(self) -> float:
        """大2.5球率"""
        return self.over25 / max(self.n, 1)

    @property
    def gf_home_avg(self) -> float:
        """主场场均进球"""
        return self.gf_home / max(self.gf_home_n, 1)

    @property
    def gf_away_avg(self) -> float:
        """客场场均进球"""
        return self.gf_away / max(self.gf_away_n, 1)

    @property
    def ga_home_avg(self) -> float:
        """主场场均失球"""
        return self.ga_home / max(self.ga_home_n, 1)

    @property
    def ga_away_avg(self) -> float:
        """客场场均失球"""
        return self.ga_away / max(self.ga_away_n, 1)

    def form_score(self) -> int:
        """
        近3场状态分：胜+3 平+1 负+0，满分9
        """
        recent3 = self.recent[-3:] if len(self.recent) >= 3 else self.recent
        score = 0
        for _, scored, conceded in recent3:
            if scored > conceded:
                score += 3
            elif scored == conceded:
                score += 1
        return score


def team_stats_to_team_data(ts) -> TeamData:
    """
    将 score_family_model.TeamStats 转换为 TeamData
    """
    return TeamData(
        n=ts.n, gf=ts.gf, ga=ts.ga,
        win=getattr(ts, 'win', 0),
        gd=getattr(ts, 'gd', 0),
        cs=ts.cs, becs=ts.becs, btts=ts.btts, over25=ts.over25,
        gf_home=ts.gf_side[0][0], gf_home_n=ts.gf_side[0][1],
        gf_away=ts.gf_side[1][0], gf_away_n=ts.gf_side[1][1],
        ga_home=ts.ga_side[0][0], ga_home_n=ts.ga_side[0][1],
        ga_away=ts.ga_side[1][0], ga_away_n=ts.ga_side[1][1],
        recent_gd=ts.recent_gd.copy() if hasattr(ts, 'recent_gd') else [],
        recent=ts.recent.copy() if hasattr(ts, 'recent') else [],
    )


# ============================================================
# 预测器
# ============================================================

class PredictorV4b:
    """
    v4b比分预测器

    核心改进：
    1. 状态加成：高命中率状态组合时增加平局/主胜比分的信号
    2. 场景分析：4个场景（攻防、状态、零封、大球）
    3. 频率校准：50%原始信号 + 50%历史频率

    使用示例：
        predictor = PredictorV4b()
        sorted_scores = predictor.predict(home_data, away_data)
        top2 = sorted_scores[:2]  # 取Top2比分
    """

    def __init__(self, boost_factor: float = BOOST_FACTOR):
        self.th = THRESHOLDS
        self.boost_factor = boost_factor

    def predict(self, home: TeamData, away: TeamData) -> List[Tuple[Tuple[int, int], float]]:
        """
        预测比分

        Args:
            home: 主队数据
            away: 客队数据

        Returns:
            按信号强度排序的比分列表 [((h, a), signal), ...]
        """
        # 1. 场景分析
        scenes = self._analyze_scenes(home, away)

        # 2. 计算原始信号
        raw_signals = self._calc_raw_signals(scenes)

        # 3. 频率校准（50%原始 + 50%历史）
        calibrated = self._freq_calibrate(raw_signals)

        # 4. 应用状态加成
        boosted = self._apply_boost(calibrated, home, away)

        # 5. 排序返回
        return sorted(boosted.items(), key=lambda x: -x[1])

    def _analyze_scenes(self, home: TeamData, away: TeamData) -> Dict:
        """分析4个场景"""
        scenes = {}
        th = self.th

        # S1: 攻防对比
        s1 = {"support": defaultdict(float)}
        if home.gf_home_avg > th["gf_high"] and away.ga_away_avg > th["ga_high"]:
            s1["support"]["home_win"] = 0.4
        elif away.gf_away_avg > th["gf_high"] and home.ga_home_avg > th["ga_high"]:
            s1["support"]["away_win"] = 0.3
        elif home.gf_avg < th["gf_low"] and away.gf_avg < th["gf_low"]:
            s1["support"]["low_score"] = 0.4
            s1["support"]["draw"] = 0.3
        elif home.gf_avg > th["gf_high"] and away.gf_avg > th["gf_high"]:
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

        # S3: 零封能力
        s3 = {"support": defaultdict(float)}
        if home.cs_rate > th["cs_rate_high"] and away.becs_rate > th["becs_rate_high"]:
            s3["support"]["home_clean"] = 0.4
        if away.cs_rate > th["cs_rate_high"] and home.becs_rate > th["becs_rate_high"]:
            s3["support"]["away_clean"] = 0.4
        if home.becs_rate > th["becs_rate_high"] and away.becs_rate > th["becs_rate_high"]:
            s3["support"]["low_score"] = 0.3
        scenes["S3"] = s3

        # S4: 大球倾向
        s4 = {"support": defaultdict(float)}
        if home.over25_rate > th["over25_high"] and away.over25_rate > th["over25_high"]:
            s4["support"]["high_score"] = 0.4
        if home.btts_rate > th["btts_high"] and away.btts_rate > th["btts_high"]:
            s4["support"]["both_score"] = 0.3
        scenes["S4"] = s4

        return scenes

    def _calc_raw_signals(self, scenes: Dict) -> Dict[Tuple[int, int], float]:
        """计算原始信号"""
        signals = defaultdict(float)

        # 效果到比分的映射
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

    def _freq_calibrate(self, raw: Dict[Tuple[int, int], float]) -> Dict[Tuple[int, int], float]:
        """频率校准：50%原始信号 + 50%历史频率"""
        calibrated = {}
        all_scores = set(raw.keys()) | set(HIST_FREQ.keys())

        for score in all_scores:
            r = raw.get(score, 0)
            f = HIST_FREQ.get(score, 0)
            calibrated[score] = 0.5 * r + 0.5 * f

        return calibrated

    def _apply_boost(self, signals: Dict[Tuple[int, int], float],
                     home: TeamData, away: TeamData) -> Dict[Tuple[int, int], float]:
        """
        应用状态加成

        A类（平局加成）：高命中率状态组合时，增加平局类比分信号
        C类（主队加成）：主队状态好+客队状态差时，增加主胜类比分信号
        """
        boosted = signals.copy()
        form_combo = (home.form_score(), away.form_score())

        # A类：平局加成
        if form_combo in DRAW_BOOST_COMBOS:
            boost = DRAW_BOOST_COMBOS[form_combo] * self.boost_factor
            for score in [(1, 1), (0, 0), (2, 2)]:
                if score in boosted:
                    boosted[score] *= (1 + boost)

        # C类：主队主导加成
        if form_combo in HOME_BOOST_COMBOS:
            boost = HOME_BOOST_COMBOS[form_combo] * self.boost_factor
            for score in [(1, 0), (2, 0), (2, 1), (3, 0), (3, 1)]:
                if score in boosted:
                    boosted[score] *= (1 + boost)

        return boosted


# ============================================================
# 便捷函数
# ============================================================

def predict_score(home: TeamData, away: TeamData, top_k: int = 2) -> List[Tuple[Tuple[int, int], float]]:
    """
    便捷预测函数

    Args:
        home: 主队数据
        away: 客队数据
        top_k: 返回前k个预测

    Returns:
        Top-k预测比分列表
    """
    predictor = PredictorV4b()
    sorted_scores = predictor.predict(home, away)
    return sorted_scores[:top_k]
