# -*- coding: utf-8 -*-
"""
倍率模型探索 v2 —— 基于准确验证框架

使用与 complete_accurate_validation.py 完全一致的预测逻辑
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
print("倍率模型探索 v2 —— 基于准确验证框架")
print("=" * 100)

INITIAL_CAPITAL = 1000.0
START_DATE = "2025-12-01"
BASE_UNIT = 2.0


# ============================================================
# 数据加载（与准确验证完全一致）
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
# TeamData（与准确验证完全一致）
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
# 比分预测器（与准确验证完全一致）
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
    """与 complete_accurate_validation.py 完全一致的预测器"""

    def __init__(self):
        self.th = THRESHOLDS
        self.calibration_strength = 0.5

    def predict(self, home: TeamData, away: TeamData) -> Dict:
        scenes = self._analyze_scenes(home, away)
        raw_signals = self._calc_raw_signals(scenes)
        calibrated = self._freq_calibrate(raw_signals)
        sorted_scores = sorted(calibrated.items(), key=lambda x: -x[1])

        # 计算置信度特征
        confidence_features = self._calc_confidence_features(scenes, sorted_scores, home, away)

        return {
            "sorted_scores": sorted_scores,
            "scenes": scenes,
            "confidence": confidence_features,
        }

    def _analyze_scenes(self, home: TeamData, away: TeamData) -> Dict:
        scenes = {}
        th = self.th

        # S1: 攻防
        s1 = {"support": defaultdict(float), "judgment": "均衡"}
        home_attack = home.gf_home_avg
        away_attack = away.gf_away_avg
        home_defense = home.ga_home_avg
        away_defense = away.ga_away_avg

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
            s2["support"]["draw"] = 0.3
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
        if home.becs_rate > th["becs_rate_high"] and away.becs_rate > th["becs_rate_high"]:
            s3["judgment"] = "低产倾向"
            s3["support"]["low_score"] = 0.3
        scenes["S3"] = s3

        # S4: 大球
        s4 = {"support": defaultdict(float), "judgment": "无明显倾向"}
        if home.over25_rate > th["over25_high"] and away.over25_rate > th["over25_high"]:
            s4["judgment"] = "大球倾向"
            s4["support"]["high_score"] = 0.4
        if home.btts_rate > th["btts_high"] and away.btts_rate > th["btts_high"]:
            s4["judgment"] = "双方进球倾向"
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
               ...[truncated 10878 chars]