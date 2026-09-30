# -*- coding: utf-8 -*-
"""多场景博弈比分预测模型 v0.1

设计文档：docs/multi_scene_consensus_model_v0.3.md

核心理念：
- 6个独立场景各自判断 → 加权融合 → 输出所有比分的信号强度
- 不用概率预测，用规则+博弈一致性
- 目标市场：体彩竞彩比分+总进球数

作者：sszhang
日期：2026-09-30
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ============================================================
# 常量定义
# ============================================================

# 比分分组
SCORE_GROUPS = {
    "zero_zero":   [(0, 0)],
    "home_clean":  [(1, 0), (2, 0), (3, 0), (4, 0)],
    "away_clean":  [(0, 1), (0, 2), (0, 3), (0, 4)],
    "small_draw":  [(1, 1)],
    "big_draw":    [(2, 2), (3, 3)],
    "home_multi":  [(2, 1), (3, 1), (3, 2), (4, 1), (4, 2)],
    "away_multi":  [(1, 2), (1, 3), (2, 3), (1, 4), (2, 4)],
}

# 比分 -> 组别 映射
SCORE_TO_GROUP = {}
for group, scores in SCORE_GROUPS.items():
    for s in scores:
        SCORE_TO_GROUP[s] = group

# 场景权重矩阵 [S1攻防, S2状态, S3主客, S4模式, S5防守, S6节奏]
SCENE_WEIGHTS = {
    "zero_zero":   [0.15, 0.10, 0.05, 0.35, 0.20, 0.15],
    "home_clean":  [0.25, 0.15, 0.15, 0.20, 0.15, 0.10],
    "away_clean":  [0.25, 0.15, 0.10, 0.20, 0.20, 0.10],
    "small_draw":  [0.20, 0.25, 0.10, 0.15, 0.15, 0.15],
    "big_draw":    [0.15, 0.10, 0.05, 0.30, 0.25, 0.15],
    "home_multi":  [0.25, 0.20, 0.15, 0.15, 0.10, 0.15],
    "away_multi":  [0.25, 0.20, 0.10, 0.15, 0.15, 0.15],
    "other":       [0.20, 0.15, 0.10, 0.20, 0.20, 0.15],
}

# 阈值配置（常规值，后续迭代优化）
THRESHOLDS = {
    # 攻防指标
    "gf_low": 1.0,
    "gf_high": 1.8,
    "ga_low": 0.8,
    "ga_high": 1.5,

    # 零封相关
    "cs_rate_high": 0.35,
    "becs_rate_high": 0.35,

    # 开放度相关
    "btts_low": 0.40,
    "btts_high": 0.55,
    "over25_low": 0.40,
    "over25_high": 0.55,

    # 状态相关
    "form_good": 7,
    "form_bad": 3,
    "momentum_high": 0.5,
    "momentum_low": -0.5,

    # 防守脆弱度
    "fragility_high": 1.8,
    "fragility_low": 0.8,
}


# ============================================================
# 数据结构
# ============================================================

@dataclass
class TeamData:
    """球队数据（从 TeamStats 提取）"""
    n: int = 0
    gf: int = 0
    ga: int = 0
    win: int = 0
    gd: int = 0
    cs: int = 0      # 零封场次
    becs: int = 0    # 被零封场次
    btts: int = 0    # BTTS场次
    over25: int = 0  # 大球场次
    gf_home: int = 0
    gf_home_n: int = 0
    gf_away: int = 0
    gf_away_n: int = 0
    ga_home: int = 0
    ga_home_n: int = 0
    ga_away: int = 0
    ga_away_n: int = 0
    recent_gd: List[int] = field(default_factory=list)  # 近3场净胜
    recent: List[Tuple] = field(default_factory=list)   # 近10场 (opp, scored, conceded)

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
        """动量：近3场净胜均值 - 历史净胜均值"""
        if not self.recent_gd:
            return 0.0
        recent_avg = sum(self.recent_gd) / len(self.recent_gd)
        hist_avg = self.gd / max(self.n, 1)
        return recent_avg - hist_avg

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

    def ga_variance(self) -> float:
        """失球方差（稳定性指标）"""
        recent = self.recent[-5:] if len(self.recent) >= 5 else self.recent
        if len(recent) < 2:
            return 0.0
        ga_list = [r[2] for r in recent]
        mean_ga = sum(ga_list) / len(ga_list)
        variance = sum((g - mean_ga) ** 2 for g in ga_list) / len(ga_list)
        return variance


@dataclass
class SceneOutput:
    """场景输出"""
    scene_id: str
    judgment: str
    confidence: str  # high/medium/low
    evidence: List[str] = field(default_factory=list)
    # 对各比分组的支持度 [-1, 1]，正=支持，负=反对
    support: Dict[str, float] = field(default_factory=dict)


# ============================================================
# 场景分析器
# ============================================================

class SceneS1_OffenseDefense:
    """场景S1：攻防强度对比"""

    def analyze(self, home: TeamData, away: TeamData) -> SceneOutput:
        th = THRESHOLDS
        evidence = []
        support = defaultdict(float)

        # 主队主场攻击力 vs 客队防守
        home_attack = home.gf_home_avg
        away_defense = away.ga_away_avg

        # 客队客场攻击力 vs 主队防守
        away_attack = away.gf_away_avg
        home_defense = home.ga_home_avg

        evidence.append(f"主队主场场均进球{home_attack:.2f}")
        evidence.append(f"客队客场场均进球{away_attack:.2f}")
        evidence.append(f"主队场均失球{home.ga_avg:.2f}")
        evidence.append(f"客队场均失球{away.ga_avg:.2f}")

        judgment = "均衡"
        confidence = "medium"

        # 判断逻辑
        home_strong = home_attack > th["gf_high"] and away_defense > th["ga_high"]
        away_strong = away_attack > th["gf_high"] - 0.3 and home_defense > th["ga_high"]
        home_weak = home_attack < th["gf_low"] or home.gf_avg < th["gf_low"]
        away_weak = away_attack < th["gf_low"] or away.gf_avg < th["gf_low"]

        if home_strong and not away_strong:
            judgment = "主攻压制"
            confidence = "high"
            support["home_clean"] = 0.6
            support["home_multi"] = 0.5
            support["away_clean"] = -0.5
            support["away_multi"] = -0.3

        elif away_strong and not home_strong:
            judgment = "客攻有威胁"
            confidence = "medium"
            support["away_clean"] = 0.4
            support["away_multi"] = 0.5
            support["home_clean"] = -0.3

        elif home_strong and away_strong:
            judgment = "双方互爆"
            confidence = "high"
            support["big_draw"] = 0.5
            support["home_multi"] = 0.4
            support["away_multi"] = 0.4
            support["zero_zero"] = -0.7
            support["home_clean"] = -0.3
            support["away_clean"] = -0.3

        elif home_weak and away_weak:
            judgment = "双方低迷"
            confidence = "high"
            support["zero_zero"] = 0.6
            support["small_draw"] = 0.4
            support["big_draw"] = -0.5
            support["home_multi"] = -0.4
            support["away_multi"] = -0.4

        return SceneOutput(
            scene_id="S1",
            judgment=judgment,
            confidence=confidence,
            evidence=evidence,
            support=dict(support)
        )


class SceneS2_RecentForm:
    """场景S2：近期状态"""

    def analyze(self, home: TeamData, away: TeamData) -> SceneOutput:
        th = THRESHOLDS
        evidence = []
        support = defaultdict(float)

        home_form = home.form_score()
        away_form = away.form_score()
        home_momentum = home.momentum
        away_momentum = away.momentum

        evidence.append(f"主队近3场积分{home_form}")
        evidence.append(f"客队近3场积分{away_form}")
        evidence.append(f"主队动量{home_momentum:.2f}")
        evidence.append(f"客队动量{away_momentum:.2f}")

        judgment = "状态接近"
        confidence = "medium"

        if home_form >= th["form_good"] and away_form <= th["form_bad"]:
            judgment = "主强客弱"
            confidence = "high"
            support["home_clean"] = 0.5
            support["home_multi"] = 0.4
            support["away_clean"] = -0.5
            support["away_multi"] = -0.4

        elif away_form >= th["form_good"] and home_form <= th["form_bad"]:
            judgment = "客强主弱"
            confidence = "high"
            support["away_clean"] = 0.5
            support["away_multi"] = 0.5
            support["home_clean"] = -0.4
            support["home_multi"] = -0.3

        elif home_form <= th["form_bad"] and away_form <= th["form_bad"]:
            judgment = "双方低迷"
            confidence = "medium"
            support["zero_zero"] = 0.4
            support["small_draw"] = 0.5
            support["big_draw"] = -0.3

        elif home_form >= th["form_good"] and away_form >= th["form_good"]:
            judgment = "双方强势"
            confidence = "medium"
            support["home_multi"] = 0.3
            support["away_multi"] = 0.3
            support["big_draw"] = 0.2

        return SceneOutput(
            scene_id="S2",
            judgment=judgment,
            confidence=confidence,
            evidence=evidence,
            support=dict(support)
        )


class SceneS3_HomeAway:
    """场景S3：主客场特性"""

    def analyze(self, home: TeamData, away: TeamData) -> SceneOutput:
        evidence = []
        support = defaultdict(float)

        # 主队主客场差异
        home_diff = home.gf_home_avg - home.gf_away_avg
        # 客队主客场差异
        away_diff = away.gf_away_avg - away.gf_home_avg

        evidence.append(f"主队主场进球{home.gf_home_avg:.2f}客场{home.gf_away_avg:.2f}")
        away_home = away.gf_home_avg if away.gf_home_n > 0 else 0
        evidence.append(f"客队主场进球{away_home:.2f}客场{away.gf_away_avg:.2f}")

        judgment = "主场优势一般"
        confidence = "low"

        if home_diff > 0.5 and away_diff < -0.3:
            judgment = "主场龙客场虫"
            confidence = "medium"
            support["home_clean"] = 0.3
            support["home_multi"] = 0.3
            support["away_clean"] = -0.2

        elif home_diff < 0 and away_diff > 0.3:
            judgment = "客队不惧客场"
            confidence = "medium"
            support["away_clean"] = 0.3
            support["away_multi"] = 0.3
            support["home_clean"] = -0.2

        elif home.gf_home_avg > 2.0 and home.ga_home_avg < 1.0:
            judgment = "主场堡垒"
            confidence = "high"
            support["home_clean"] = 0.5
            support["home_multi"] = 0.4
            support["zero_zero"] = 0.2

        return SceneOutput(
            scene_id="S3",
            judgment=judgment,
            confidence=confidence,
            evidence=evidence,
            support=dict(support)
        )


class SceneS4_ScoringPattern:
    """场景S4：进球模式（核心场景）"""

    def analyze(self, home: TeamData, away: TeamData) -> SceneOutput:
        th = THRESHOLDS
        evidence = []
        support = defaultdict(float)

        home_cs = home.cs_rate
        home_becs = home.becs_rate
        away_cs = away.cs_rate
        away_becs = away.becs_rate
        home_btts = home.btts_rate
        away_btts = away.btts_rate
        home_over25 = home.over25_rate
        away_over25 = away.over25_rate

        evidence.append(f"主队零封率{home_cs:.1%}被零封{home_becs:.1%}")
        evidence.append(f"客队零封率{away_cs:.1%}被零封{away_becs:.1%}")
        evidence.append(f"主队BTTS{home_btts:.1%}大球{home_over25:.1%}")
        evidence.append(f"客队BTTS{away_btts:.1%}大球{away_over25:.1%}")

        judgment = "常规"
        confidence = "low"

        # 0:0 信号
        zero_zero_signal = (
            home_becs > th["becs_rate_high"] and
            away_becs > th["becs_rate_high"] and
            home_cs > th["cs_rate_high"] - 0.05 and
            away_cs > th["cs_rate_high"] - 0.05
        )

        # 互爆信号
        high_score_signal = (
            home_over25 > th["over25_high"] and
            away_over25 > th["over25_high"] and
            home_btts > th["btts_high"] and
            away_btts > th["btts_high"]
        )

        # 主零封信号
        home_clean_signal = (
            home_cs > th["cs_rate_high"] + 0.1 and
            away_becs > th["becs_rate_high"]
        )

        # 客零封信号
        away_clean_signal = (
            away_cs > th["cs_rate_high"] + 0.1 and
            home_becs > th["becs_rate_high"]
        )

        if zero_zero_signal:
            judgment = "零零倾向"
            confidence = "high"
            support["zero_zero"] = 0.7
            support["small_draw"] = 0.3
            support["big_draw"] = -0.5
            support["home_multi"] = -0.4
            support["away_multi"] = -0.4

        elif high_score_signal:
            judgment = "互爆倾向"
            confidence = "high"
            support["big_draw"] = 0.5
            support["home_multi"] = 0.4
            support["away_multi"] = 0.4
            support["zero_zero"] = -0.7
            support["home_clean"] = -0.3
            support["away_clean"] = -0.3

        elif home_clean_signal:
            judgment = "主零封倾向"
            confidence = "medium"
            support["home_clean"] = 0.6
            support["zero_zero"] = 0.2
            support["away_multi"] = -0.4

        elif away_clean_signal:
            judgment = "客零封倾向"
            confidence = "medium"
            support["away_clean"] = 0.6
            support["zero_zero"] = 0.2
            support["home_multi"] = -0.4

        return SceneOutput(
            scene_id="S4",
            judgment=judgment,
            confidence=confidence,
            evidence=evidence,
            support=dict(support)
        )


class SceneS5_DefenseStability:
    """场景S5：防守稳定性"""

    def analyze(self, home: TeamData, away: TeamData) -> SceneOutput:
        th = THRESHOLDS
        evidence = []
        support = defaultdict(float)

        home_ga_var = home.ga_variance()
        away_ga_var = away.ga_variance()

        # 防守脆弱度 = 失球均值 + 失球方差
        home_fragility = home.ga_avg + home_ga_var * 0.3
        away_fragility = away.ga_avg + away_ga_var * 0.3

        evidence.append(f"主队防守脆弱度{home_fragility:.2f}(失球{home.ga_avg:.2f}方差{home_ga_var:.2f})")
        evidence.append(f"客队防守脆弱度{away_fragility:.2f}(失球{away.ga_avg:.2f}方差{away_ga_var:.2f})")

        judgment = "防守一般"
        confidence = "low"

        if home_fragility > th["fragility_high"] and away_fragility > th["fragility_high"]:
            judgment = "双方脆弱"
            confidence = "high"
            support["big_draw"] = 0.5
            support["home_multi"] = 0.4
            support["away_multi"] = 0.4
            support["zero_zero"] = -0.6
            support["home_clean"] = -0.3
            support["away_clean"] = -0.3

        elif home_fragility < th["fragility_low"] and away_fragility < th["fragility_low"]:
            judgment = "双方稳固"
            confidence = "high"
            support["zero_zero"] = 0.5
            support["small_draw"] = 0.4
            support["home_clean"] = 0.3
            support["away_clean"] = 0.3
            support["big_draw"] = -0.4

        elif home_fragility > th["fragility_high"] + 0.3 and away_fragility < th["fragility_low"] + 0.2:
            judgment = "主守脆弱"
            confidence = "medium"
            support["away_multi"] = 0.5
            support["away_clean"] = 0.3
            support["home_clean"] = -0.4

        elif away_fragility > th["fragility_high"] + 0.3 and home_fragility < th["fragility_low"] + 0.2:
            judgment = "客守脆弱"
            confidence = "medium"
            support["home_multi"] = 0.5
            support["home_clean"] = 0.3
            support["away_clean"] = -0.4

        return SceneOutput(
            scene_id="S5",
            judgment=judgment,
            confidence=confidence,
            evidence=evidence,
            support=dict(support)
        )


class SceneS6_MatchTempo:
    """场景S6：比赛节奏"""

    def analyze(self, home: TeamData, away: TeamData) -> SceneOutput:
        th = THRESHOLDS
        evidence = []
        support = defaultdict(float)

        combined_btts = (home.btts_rate + away.btts_rate) / 2
        combined_over25 = (home.over25_rate + away.over25_rate) / 2
        expected_goals = home.gf_avg + away.gf_avg

        evidence.append(f"综合BTTS率{combined_btts:.1%}")
        evidence.append(f"综合大球率{combined_over25:.1%}")
        evidence.append(f"预期总进球{expected_goals:.2f}")

        judgment = "节奏一般"
        confidence = "low"

        if combined_btts > th["btts_high"] and combined_over25 > th["over25_high"]:
            judgment = "开放型"
            confidence = "high"
            support["big_draw"] = 0.4
            support["home_multi"] = 0.4
            support["away_multi"] = 0.4
            support["zero_zero"] = -0.5

        elif combined_btts < th["btts_low"] and combined_over25 < th["over25_low"]:
            judgment = "保守型"
            confidence = "high"
            support["zero_zero"] = 0.5
            support["small_draw"] = 0.4
            support["home_clean"] = 0.3
            support["away_clean"] = 0.3
            support["big_draw"] = -0.4

        elif expected_goals < 2.0:
            judgment = "低产对决"
            confidence = "medium"
            support["zero_zero"] = 0.4
            support["small_draw"] = 0.3
            support["home_clean"] = 0.2
            support["away_clean"] = 0.2

        elif expected_goals > 3.5:
            judgment = "高产对决"
            confidence = "medium"
            support["big_draw"] = 0.3
            support["home_multi"] = 0.3
            support["away_multi"] = 0.3

        return SceneOutput(
            scene_id="S6",
            judgment=judgment,
            confidence=confidence,
            evidence=evidence,
            support=dict(support)
        )


# ============================================================
# 多场景预测器
# ============================================================

class MultiScenePredictor:
    """多场景博弈预测器"""

    def __init__(self):
        self.scenes = [
            SceneS1_OffenseDefense(),
            SceneS2_RecentForm(),
            SceneS3_HomeAway(),
            SceneS4_ScoringPattern(),
            SceneS5_DefenseStability(),
            SceneS6_MatchTempo(),
        ]

    def predict(self, home: TeamData, away: TeamData) -> Dict:
        """预测所有比分的信号强度"""

        # 1. 运行所有场景分析
        scene_outputs = [scene.analyze(home, away) for scene in self.scenes]

        # 2. 计算每个比分组的加权信号
        score_signals = {}

        for score, group in SCORE_TO_GROUP.items():
            weights = SCENE_WEIGHTS.get(group, SCENE_WEIGHTS["other"])

            total_signal = 0.0
            for i, so in enumerate(scene_outputs):
                support = so.support.get(group, 0.0)
                total_signal += weights[i] * support

            # 归一化到 [-1, 1]
            score_signals[score] = max(-1.0, min(1.0, total_signal))

        # 3. 计算总进球信号
        total_goal_signals = self._calc_total_goal_signals(scene_outputs)

        # 4. 排序并生成推荐
        sorted_scores = sorted(score_signals.items(), key=lambda x: -x[1])

        # 5. 确定置信度
        predictions = {}
        for rank, (score, signal) in enumerate(sorted_scores, 1):
            if signal > 0.5:
                conf = "high"
            elif signal > 0.3:
                conf = "medium"
            elif signal > 0.1:
                conf = "low"
            else:
                conf = "none"

            predictions[score] = {
                "signal": round(signal, 3),
                "confidence": conf,
                "rank": rank,
            }

        # 6. 生成下注建议
        recommendations = self._generate_recommendations(predictions, total_goal_signals)

        return {
            "predictions": predictions,
            "total_goals": total_goal_signals,
            "scene_details": {so.scene_id: {"judgment": so.judgment, "confidence": so.confidence}
                             for so in scene_outputs},
            "recommendations": recommendations,
        }

    def _calc_total_goal_signals(self, scene_outputs: List[SceneOutput]) -> Dict:
        """计算总进球数信号"""
        signals = {
            "0-1": 0.0,
            "2-3": 0.0,
            "4+": 0.0,
        }

        for so in scene_outputs:
            # 从比分组支持度推断总进球
            zero_support = so.support.get("zero_zero", 0)
            low_support = so.support.get("small_draw", 0) + so.support.get("home_clean", 0) * 0.5 + so.support.get("away_clean", 0) * 0.5
            high_support = so.support.get("big_draw", 0) + so.support.get("home_multi", 0) * 0.5 + so.support.get("away_multi", 0) * 0.5

            signals["0-1"] += zero_support * 0.5 + low_support * 0.3
            signals["2-3"] += low_support * 0.2  # 2-3球是中间态
            signals["4+"] += high_support * 0.5

        # 归一化
        for k in signals:
            signals[k] = max(-1.0, min(1.0, signals[k] / 3))

        # 添加置信度
        result = {}
        for k, v in signals.items():
            if v > 0.4:
                conf = "high"
            elif v > 0.2:
                conf = "medium"
            else:
                conf = "low"
            result[k] = {"signal": round(v, 3), "confidence": conf}

        return result

    def _generate_recommendations(self, predictions: Dict, total_goals: Dict) -> List[Dict]:
        """生成下注建议"""
        recs = []

        # 比分推荐（信号>0.5）
        for score, info in predictions.items():
            if info["signal"] > 0.5 and info["confidence"] in ["high", "medium"]:
                recs.append({
                    "market": "correct_score",
                    "selection": f"{score[0]}:{score[1]}",
                    "signal": info["signal"],
                    "confidence": info["confidence"],
                })

        # 总进球推荐
        for goal_range, info in total_goals.items():
            if info["signal"] > 0.4 and info["confidence"] in ["high", "medium"]:
                if goal_range == "0-1":
                    selection = "under_1.5"
                elif goal_range == "4+":
                    selection = "over_3.5"
                else:
                    continue
                recs.append({
                    "market": "total_goals",
                    "selection": selection,
                    "signal": info["signal"],
                    "confidence": info["confidence"],
                })

        return recs


# ============================================================
# 工具函数
# ============================================================

def team_stats_to_data(stats) -> TeamData:
    """将 TeamStats 转换为 TeamData"""
    return TeamData(
        n=stats.n,
        gf=stats.gf,
        ga=stats.ga,
        win=stats.win,
        gd=stats.gd,
        cs=stats.cs,
        becs=stats.becs,
        btts=stats.btts,
        over25=stats.over25,
        gf_home=stats.gf_side[0][0],
        gf_home_n=stats.gf_side[0][1],
        gf_away=stats.gf_side[1][0],
        gf_away_n=stats.gf_side[1][1],
        ga_home=stats.ga_side[0][0],
        ga_home_n=stats.ga_side[0][1],
        ga_away=stats.ga_side[1][0],
        ga_away_n=stats.ga_side[1][1],
        recent_gd=list(stats.recent_gd),
        recent=list(stats.recent),
    )


# ============================================================
# 测试
# ============================================================

if __name__ == "__main__":
    # 简单测试
    home = TeamData(
        n=20, gf=30, ga=15, win=12, gd=15,
        cs=8, becs=3, btts=10, over25=12,
        gf_home=18, gf_home_n=10, gf_away=12, gf_away_n=10,
        ga_home=6, ga_home_n=10, ga_away=9, ga_away_n=10,
        recent_gd=[2, 1, 0],
        recent=[("A", 2, 0), ("B", 1, 1), ("C", 3, 1)]
    )

    away = TeamData(
        n=20, gf=20, ga=25, win=6, gd=-5,
        cs=4, becs=8, btts=12, over25=10,
        gf_home=12, gf_home_n=10, gf_away=8, gf_away_n=10,
        ga_home=10, ga_home_n=10, ga_away=15, ga_away_n=10,
        recent_gd=[-1, -2, 0],
        recent=[("X", 1, 2), ("Y", 0, 2), ("Z", 1, 1)]
    )

    predictor = MultiScenePredictor()
    result = predictor.predict(home, away)

    print("=" * 60)
    print("多场景预测结果")
    print("=" * 60)

    print("\n【场景判断】")
    for scene_id, detail in result["scene_details"].items():
        print(f"  {scene_id}: {detail['judgment']} ({detail['confidence']})")

    print("\n【比分信号 Top10】")
    sorted_pred = sorted(result["predictions"].items(), key=lambda x: -x[1]["signal"])
    for score, info in sorted_pred[:10]:
        print(f"  {score[0]}:{score[1]}: signal={info['signal']:.3f} ({info['confidence']})")

    print("\n【总进球信号】")
    for k, v in result["total_goals"].items():
        print(f"  {k}: signal={v['signal']:.3f} ({v['confidence']})")

    print("\n【下注建议】")
    for rec in result["recommendations"]:
        print(f"  {rec['market']}: {rec['selection']} (signal={rec['signal']:.3f}, {rec['confidence']})")
