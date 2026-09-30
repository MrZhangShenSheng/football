# -*- coding: utf-8 -*-
"""
预测模型v4 —— 全面重新设计

优化方向：
1. 降低(1,1)预测权重 —— 减少频率校准强度
2. 增加(2,1)预测 —— 调整效果映射
3. 重新设计S1场景 —— 当前设计无效
4. 利用S2状态组合 —— 某些组合命中率极高
5. 修复赔率价值计算 —— 使用正确的公式
"""
import json
import sys
from collections import defaultdict, Counter
from dataclasses import dataclass, field
from itertools import product
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, str(Path("engine/scripts/research")))

import score_family_model as sfm

print("=" * 100)
print("预测模型v4 —— 全面重新设计")
print("=" * 100)

INITIAL_CAPITAL = 1000.0
START_DATE = "2025-12-01"
BASE_UNIT = 2.0


# ============================================================
# 数据加载
# ============================================================

def load_hist():
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
hist = load_hist()

print(f"数据：历史{len(hist)}场")


# ============================================================
# TeamData
# ============================================================

@dataclass
class TeamData:
    n: int = 0
    gf: int = 0
    ga: int = 0
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


def ts_to_td(ts):
    return TeamData(
        n=ts.n, gf=ts.gf, ga=ts.ga,
        cs=ts.cs, becs=ts.becs, btts=ts.btts, over25=ts.over25,
        gf_home=ts.gf_side[0][0], gf_home_n=ts.gf_side[0][1],
        gf_away=ts.gf_side[1][0], gf_away_n=ts.gf_side[1][1],
        ga_home=ts.ga_side[0][0], ga_home_n=ts.ga_side[0][1],
        ga_away=ts.ga_side[1][0], ga_away_n=ts.ga_side[1][1],
        recent=ts.recent.copy() if hasattr(ts, 'recent') else [],
    )


# ============================================================
# 新预测器v4
# ============================================================

# 优化1：降低(1,1)频率，增加(2,1)频率
HIST_FREQ_V4 = {
    (1, 1): 0.10,  # 从0.12降到0.10
    (2, 1): 0.11,  # 从0.09升到0.11（实际出现率更高）
    (1, 0): 0.09,
    (1, 2): 0.09,  # 从0.08升到0.09
    (0, 1): 0.08,  # 从0.07升到0.08
    (0, 0): 0.07,
    (2, 0): 0.07,
    (2, 2): 0.06,  # 从0.05升到0.06
    (0, 2): 0.05,
    (3, 1): 0.05,  # 从0.04升到0.05
    (3, 0): 0.04,
    (3, 2): 0.03,
    (1, 3): 0.03,
    (2, 3): 0.02,
    (4, 0): 0.02,
    (4, 1): 0.02,
    (0, 3): 0.02,
    (3, 3): 0.01,
}

# 优化4：高命中率状态组合
HIGH_HIT_FORM_COMBOS = {
    # (主场状态, 客场状态): 命中率加成
    (3, 5): 0.4,   # 58.3%
    (5, 2): 0.4,   # 54.5%
    (5, 5): 0.35,  # 53.8%
    (2, 2): 0.3,   # 40.0%
    (2, 1): 0.3,   # 40.0%
    (4, 5): 0.25,  # 38.5%
    (2, 5): 0.25,  # 35.7%
    (7, 7): 0.2,   # 33.3%
    (5, 4): 0.2,   # 29.2%
    (1, 3): 0.2,   # 26.1%
    (4, 2): 0.15,  # 25.0%
    (5, 3): 0.15,  # 25.0%
    (1, 2): 0.15,  # 25.0%
    (4, 4): 0.15,  # 22.6%
    (5, 1): 0.15,  # 22.2%
    (4, 1): 0.15,  # 22.2%
    (1, 1): 0.15,  # 21.1%
}

# 客队状态好且主队状态差的组合
AWAY_DOMINANT_COMBOS = {
    (0, 9): 0.3,   # 42.9%
    (1, 7): 0.1,   # 17.1%
    (2, 7): 0.1,   # 18.2%
}


class PredictorV4:
    """重新设计的预测器v4"""

    def __init__(self, calibration_strength=0.3):  # 优化1：从0.5降到0.3
        self.calibration_strength = calibration_strength

    def predict(self, home: TeamData, away: TeamData, odds: Dict = None) -> Dict:
        # 1. 场景分析（优化3：重新设计S1）
        scenes = self._analyze_scenes(home, away)

        # 2. 计算原始信号（优化2：调整效果映射）
        raw_signals = self._calc_signals(scenes, home, away)

        # 3. 频率校准（优化1：降低校准强度）
        calibrated = self._freq_calibrate(raw_signals)

        # 4. 状态加成（优化4：利用高命中状态组合）
        boosted = self._apply_form_boost(calibrated, home, away)

        sorted_scores = sorted(boosted.items(), key=lambda x: -x[1])

        # 5. 计算置信度（优化5：修复赔率价值）
        confidence = self._calc_confidence(sorted_scores, home, away, odds, scenes)

        return {
            "sorted_scores": sorted_scores,
            "scenes": scenes,
            "confidence": confidence,
        }

    def _analyze_scenes(self, h: TeamData, a: TeamData) -> Dict:
        """优化3：重新设计场景分析"""
        scenes = {}

        # S1: 进攻能力对比（重新设计）
        # 诊断发现：原S1场景无效，改为更细致的进球期望计算
        s1 = {"support": defaultdict(float), "name": None}

        # 计算预期进球
        home_exp_gf = (h.gf_home_avg + a.ga_away_avg) / 2
        away_exp_gf = (a.gf_away_avg + h.ga_home_avg) / 2
        total_exp = home_exp_gf + away_exp_gf

        # 基于预期进球数分配信号
        if total_exp < 2.0:
            s1["support"]["low_total"] = 0.4
            s1["name"] = f"低产({total_exp:.1f})"
        elif total_exp < 2.5:
            s1["support"]["medium_low"] = 0.3
            s1["name"] = f"中低({total_exp:.1f})"
        elif total_exp < 3.0:
            s1["support"]["medium"] = 0.2
            s1["name"] = f"中等({total_exp:.1f})"
        elif total_exp < 3.5:
            s1["support"]["medium_high"] = 0.3
            s1["name"] = f"中高({total_exp:.1f})"
        else:
            s1["support"]["high_total"] = 0.4
            s1["name"] = f"高产({total_exp:.1f})"

        # 主客场优势
        if home_exp_gf > away_exp_gf + 0.5:
            s1["support"]["home_adv"] = 0.3
        elif away_exp_gf > home_exp_gf + 0.3:
            s1["support"]["away_adv"] = 0.25

        scenes["S1"] = s1
        s1["expected"] = (home_exp_gf, away_exp_gf, total_exp)

        # S2: 状态对比
        s2 = {"support": defaultdict(float), "name": None}
        hf, af = h.form_score(), a.form_score()
        s2["home_form"] = hf
        s2["away_form"] = af

        # 检查高命中组合
        form_combo = (hf, af)
        if form_combo in HIGH_HIT_FORM_COMBOS:
            boost = HIGH_HIT_FORM_COMBOS[form_combo]
            s2["support"]["high_hit_combo"] = boost
            s2["name"] = f"高命中组合({hf},{af})"
            s2["is_high_hit"] = True
        elif form_combo in AWAY_DOMINANT_COMBOS:
            boost = AWAY_DOMINANT_COMBOS[form_combo]
            s2["support"]["away_dominant"] = boost
            s2["name"] = f"客队主导({hf},{af})"
            s2["is_away_dom"] = True
        else:
            s2["name"] = f"普通({hf},{af})"
            s2["is_high_hit"] = False

        scenes["S2"] = s2

        # S3: 零封能力
        s3 = {"support": defaultdict(float), "name": None}
        if h.cs_rate > 0.35 and a.becs_rate > 0.35:
            s3["support"]["home_cs"] = 0.3
            s3["name"] = "主零封强"
        elif a.cs_rate > 0.35 and h.becs_rate > 0.35:
            s3["support"]["away_cs"] = 0.3
            s3["name"] = "客零封强"
        scenes["S3"] = s3

        # S4: 比赛开放度
        s4 = {"support": defaultdict(float), "name": None}
        if h.btts_rate > 0.55 and a.btts_rate > 0.55:
            s4["support"]["both_score"] = 0.3
            s4["name"] = "双方进球"
        if h.over25_rate > 0.55 and a.over25_rate > 0.55:
            s4["support"]["over25"] = 0.2
            if s4["name"]:
                s4["name"] += "+大球"
            else:
                s4["name"] = "大球"
        scenes["S4"] = s4

        return scenes

    def _calc_signals(self, scenes: Dict, h: TeamData, a: TeamData) -> Dict[Tuple, float]:
        """优化2：调整效果到比分的映射"""
        signals = defaultdict(float)

        # S1: 基于预期进球的信号
        s1 = scenes["S1"]
        exp = s1.get("expected", (1.5, 1.5, 3.0))
        home_exp, away_exp, total_exp = exp

        for effect, strength in s1["support"].items():
            if effect == "low_total":
                # 低产：0:0, 1:0, 0:1, 1:1
                signals[(0, 0)] += strength * 0.35
                signals[(1, 0)] += strength * 0.25
                signals[(0, 1)] += strength * 0.25
                signals[(1, 1)] += strength * 0.15
            elif effect == "medium_low":
                # 中低：1:1, 1:0, 0:1, 2:0, 0:2
                signals[(1, 1)] += strength * 0.3
                signals[(1, 0)] += strength * 0.2
                signals[(0, 1)] += strength * 0.2
                signals[(2, 0)] += strength * 0.15
                signals[(0, 2)] += strength * 0.15
            elif effect == "medium":
                # 中等：1:1, 2:1, 1:2, 2:0, 0:2
                signals[(1, 1)] += strength * 0.25
                signals[(2, 1)] += strength * 0.25
                signals[(1, 2)] += strength * 0.2
                signals[(2, 0)] += strength * 0.15
                signals[(0, 2)] += strength * 0.15
            elif effect == "medium_high":
                # 中高：2:1, 1:2, 2:2, 3:1, 1:3
                signals[(2, 1)] += strength * 0.3
                signals[(1, 2)] += strength * 0.25
                signals[(2, 2)] += strength * 0.2
                signals[(3, 1)] += strength * 0.15
                signals[(1, 3)] += strength * 0.1
            elif effect == "high_total":
                # 高产：2:2, 3:1, 3:2, 2:3, 4:1
                signals[(2, 2)] += strength * 0.25
                signals[(3, 1)] += strength * 0.2
                signals[(3, 2)] += strength * 0.2
                signals[(2, 3)] += strength * 0.2
                signals[(4, 1)] += strength * 0.15
            elif effect == "home_adv":
                # 主场优势
                signals[(2, 1)] += strength * 0.35
                signals[(2, 0)] += strength * 0.25
                signals[(1, 0)] += strength * 0.25
                signals[(3, 1)] += strength * 0.15
            elif effect == "away_adv":
                # 客场优势
                signals[(1, 2)] += strength * 0.35
                signals[(0, 2)] += strength * 0.25
                signals[(0, 1)] += strength * 0.25
                signals[(1, 3)] += strength * 0.15

        # S2: 状态信号
        s2 = scenes["S2"]
        for effect, strength in s2["support"].items():
            if effect == "high_hit_combo":
                # 高命中组合：增强(1,1)和平局类
                signals[(1, 1)] += strength * 0.4
                signals[(0, 0)] += strength * 0.2
                signals[(2, 2)] += strength * 0.2
                signals[(2, 1)] += strength * 0.1
                signals[(1, 2)] += strength * 0.1
            elif effect == "away_dominant":
                # 客队主导：增强客胜比分
                signals[(0, 1)] += strength * 0.35
                signals[(1, 2)] += strength * 0.3
                signals[(0, 2)] += strength * 0.2
                signals[(1, 3)] += strength * 0.15

        # S3: 零封信号
        s3 = scenes["S3"]
        for effect, strength in s3["support"].items():
            if effect == "home_cs":
                signals[(1, 0)] += strength * 0.4
                signals[(2, 0)] += strength * 0.35
                signals[(3, 0)] += strength * 0.25
            elif effect == "away_cs":
                signals[(0, 1)] += strength * 0.4
                signals[(0, 2)] += strength * 0.35
                signals[(0, 3)] += strength * 0.25

        # S4: 开放度信号
        s4 = scenes["S4"]
        for effect, strength in s4["support"].items():
            if effect == "both_score":
                signals[(1, 1)] += strength * 0.25
                signals[(2, 1)] += strength * 0.25
                signals[(1, 2)] += strength * 0.25
                signals[(2, 2)] += strength * 0.25
            elif effect == "over25":
                signals[(2, 1)] += strength * 0.3
                signals[(1, 2)] += strength * 0.3
                signals[(2, 2)] += strength * 0.2
                signals[(3, 1)] += strength * 0.2

        return dict(signals)

    def _freq_calibrate(self, raw: Dict[Tuple, float]) -> Dict[Tuple, float]:
        """优化1：降低频率校准强度"""
        alpha = self.calibration_strength  # 0.3
        calibrated = {}

        all_scores = set(raw.keys()) | set(HIST_FREQ_V4.keys())
        for score in all_scores:
            r = raw.get(score, 0)
            f = HIST_FREQ_V4.get(score, 0)
            calibrated[score] = (1 - alpha) * r + alpha * f

        return calibrated

    def _apply_form_boost(self, signals: Dict, h: TeamData, a: TeamData) -> Dict:
        """优化4：应用状态组合加成"""
        hf, af = h.form_score(), a.form_score()
        form_combo = (hf, af)

        boosted = signals.copy()

        # 高命中组合：整体提升平局类比分
        if form_combo in HIGH_HIT_FORM_COMBOS:
            boost = HIGH_HIT_FORM_COMBOS[form_combo] * 0.5
            for score in [(1, 1), (0, 0), (2, 2)]:
                if score in boosted:
                    boosted[score] *= (1 + boost)

        # 客队主导组合：提升客胜比分
        if form_combo in AWAY_DOMINANT_COMBOS:
            boost = AWAY_DOMINANT_COMBOS[form_combo] * 0.5
            for score in [(0, 1), (1, 2), (0, 2)]:
                if score in boosted:
                    boosted[score] *= (1 + boost)

        return boosted

    def _calc_confidence(self, sorted_scores: List, h: TeamData, a: TeamData,
                         odds: Dict, scenes: Dict) -> Dict:
        """优化5：修复赔率价值计算"""
        if not sorted_scores:
            return {"signal": 0, "gap": 0, "value": 0, "form_hit": False, "overall": 0}

        top1_score, top1_sig = sorted_scores[0]
        top2_sig = sorted_scores[1][1] if len(sorted_scores) > 1 else 0

        # 信号强度
        signal = min(top1_sig / 0.15, 1.0)

        # 信号差距
        gap = min((top1_sig - top2_sig) / 0.03, 1.0)

        # 赔率价值（优化5：使用正确公式）
        value = 0.5
        if odds and top1_score in odds:
            actual_odds = odds[top1_score]
            implied_prob = 1 / actual_odds
            # 价值 = (预测概率 - 隐含概率) / 隐含概率
            # 如果预测概率高于隐含概率，说明有价值
            value_diff = (top1_sig - implied_prob) / implied_prob if implied_prob > 0 else 0
            # 映射到0-1区间，0表示无价值，1表示高价值
            value = max(0, min(1, 0.5 + value_diff * 2))

        # 状态组合命中率
        form_combo = (h.form_score(), a.form_score())
        form_hit = form_combo in HIGH_HIT_FORM_COMBOS

        # 综合置信度
        overall = 0.3 * signal + 0.2 * gap + 0.3 * value + 0.2 * (1 if form_hit else 0)

        return {
            "signal": signal,
            "gap": gap,
            "value": value,
            "form_hit": form_hit,
            "overall": overall,
        }


# ============================================================
# 回测验证
# ============================================================

print("\n" + "=" * 100)
print("回测验证")
print("=" * 100)

# 准备数据
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

# 测试不同配置
configs = [
    ("v3_baseline", 0.5, False),   # 原配置
    ("v4_cal0.3", 0.3, True),      # 新配置：校准0.3
    ("v4_cal0.2", 0.2, True),      # 新配置：校准0.2
    ("v4_cal0.1", 0.1, True),      # 新配置：校准0.1
    ("v4_cal0.0", 0.0, True),      # 纯场景信号
]

for config_name, cal_strength, use_v4 in configs:
    # 重置统计
    stats = defaultdict(sfm.TeamStats)

    if use_v4:
        predictor = PredictorV4(calibration_strength=cal_strength)
    else:
        # 原v3预测器逻辑
        class OldPredictor:
            def predict(self, h, a, odds=None):
                # 简化的v3逻辑
                HIST_FREQ = {(1,1):0.12,(2,1):0.09,(1,0):0.09,(1,2):0.08,(0,1):0.07,(0,0):0.07,(2,0):0.07,(2,2):0.05}
                signals = defaultdict(float)
                signals[(1,1)] = 0.1
                signals[(2,1)] = 0.08
                signals[(1,0)] = 0.08
                cal = {s: 0.5*signals.get(s,0)+0.5*HIST_FREQ.get(s,0) for s in set(signals)|set(HIST_FREQ)}
                return {"sorted_scores": sorted(cal.items(), key=lambda x:-x[1]), "confidence": {"overall": 0.5}}
        predictor = OldPredictor()

    match_packs = []
    for r in merged:
        kind, date, h, a, hg, ag = r[0], r[1], r[2], r[3], r[4], r[5]
        if kind == "B":
            idx = r[6]
            m = blind[idx]
            if stats[h].n >= sfm.MIN_HIST and stats[a].n >= sfm.MIN_HIST:
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

    # 2串1双选回测
    capital = INITIAL_CAPITAL
    total_cost = 0
    total_payout = 0
    n_tickets = 0
    n_hits = 0
    min_capital = capital

    # 统计预测分布
    pred_dist = Counter()
    hit_dist = Counter()

    for day in sorted(by_day):
        day_packs = by_day[day]
        if len(day_packs) < 2:
            continue

        for p in day_packs:
            p["max_signal"] = p["prediction"]["sorted_scores"][0][1]
        selected = sorted(day_packs, key=lambda x: -x["max_signal"])[:2]

        all_picks = []
        for p in selected:
            picks = []
            for score, sig in p["prediction"]["sorted_scores"][:2]:
                odds_val = p["odds"].get(score, 999)
                picks.append((score, odds_val, p["actual"]))
                pred_dist[score] += 1
            all_picks.append(picks)

        all_bets = list(product(*all_picks))
        cost = len(all_bets) * BASE_UNIT

        if capital < cost:
            continue

        capital -= cost
        total_cost += cost
        n_tickets += 1

        payout = 0
        for combo in all_bets:
            if all(s == a for s, o, a in combo):
                combo_odds = 1.0
                for s, o, a in combo:
                    combo_odds *= o
                    hit_dist[s] += 1
                payout += BASE_UNIT * combo_odds

        capital += payout
        total_payout += payout
        if payout > 0:
            n_hits += 1
        min_capital = min(min_capital, capital)

    profit = (capital - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    drawdown = (INITIAL_CAPITAL - min_capital) / INITIAL_CAPITAL * 100

    print(f"\n【{config_name}】")
    print(f"  最终资金: {capital:.1f}元, 收益率: {profit:+.1f}%, 命中: {n_hits}/{n_tickets}, 回撤: {drawdown:.1f}%")
    print(f"  预测分布Top5: {pred_dist.most_common(5)}")
    print(f"  命中分布: {hit_dist.most_common(5)}")

print("\n" + "=" * 100)
print("完成")
print("=" * 100)
