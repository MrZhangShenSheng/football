# -*- coding: utf-8 -*-
"""
倍率模型探索 v3 —— 直接复用准确验证框架

先运行基准验证确认一致，再探索倍率策略
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
print("倍率模型探索 v3 —— 直接复用准确验证框架")
print("=" * 100)

INITIAL_CAPITAL = 1000.0
START_DATE = "2025-12-01"
BASE_UNIT = 2.0


# ============================================================
# 完全复制 complete_accurate_validation.py 的核心逻辑
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
            if len(odds) < 20:
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


# ============================================================
# 直接从 complete_accurate_validation.py 复制
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
        n=ts.n, gf=ts.gf, ga=ts.ga, win=ts.win, gd=ts.gd,
        cs=ts.cs, becs=ts.becs, btts=ts.btts, over25=ts.over25,
        gf_home=ts.gf_side[0][0], gf_home_n=ts.gf_side[0][1],
        gf_away=ts.gf_side[1][0], gf_away_n=ts.gf_side[1][1],
        ga_home=ts.ga_side[0][0], ga_home_n=ts.ga_side[0][1],
        ga_away=ts.ga_side[1][0], ga_away_n=ts.ga_side[1][1],
        recent_gd=ts.recent_gd.copy() if hasattr(ts, 'recent_gd') else [],
        recent=ts.recent.copy() if hasattr(ts, 'recent') else [],
    )


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
        self.calibration_strength = 0.5

    def predict(self, home: TeamData, away: TeamData) -> Dict:
        """返回预测结果，包含场景信息用于置信度判断"""
        scenes = self._analyze_scenes(home, away)
        raw_signals = self._calc_raw_signals(scenes)
        calibrated = self._freq_calibrate(raw_signals)
        sorted_scores = sorted(calibrated.items(), key=lambda x: -x[1])
        return {
            "sorted_scores": sorted_scores,
            "scenes": scenes,
        }

    def _analyze_scenes(self, home: TeamData, away: TeamData) -> Dict:
        scenes = {}
        th = self.th

        # S1: 攻防
        s1 = {"support": defaultdict(float), "triggered": False}
        if home.gf_home_avg > th["gf_high"] and away.ga_away_avg > th["ga_high"]:
            s1["support"]["home_win"] = 0.4
            s1["triggered"] = True
        elif away.gf_away_avg > th["gf_high"] and home.ga_home_avg > th["ga_high"]:
            s1["support"]["away_win"] = 0.3
            s1["triggered"] = True
        elif home.gf_avg < th["gf_low"] and away.gf_avg < th["gf_low"]:
            s1["support"]["low_score"] = 0.4
            s1["support"]["draw"] = 0.3
            s1["triggered"] = True
        elif home.gf_avg > th["gf_high"] and away.gf_avg > th["gf_high"]:
            s1["support"]["high_score"] = 0.4
            s1["triggered"] = True
        else:
            s1["support"]["draw"] = 0.2
        scenes["S1"] = s1

        # S2: 状态
        s2 = {"support": defaultdict(float), "triggered": False}
        home_form = home.form_score()
        away_form = away.form_score()
        if home_form >= th["form_good"] and away_form <= th["form_bad"]:
            s2["support"]["home_win"] = 0.4
            s2["triggered"] = True
        elif away_form >= th["form_good"] and home_form <= th["form_bad"]:
            s2["support"]["away_win"] = 0.4
            s2["triggered"] = True
        elif home_form <= th["form_bad"] and away_form <= th["form_bad"]:
            s2["support"]["low_score"] = 0.3
            s2["support"]["draw"] = 0.3
            s2["triggered"] = True
        else:
            s2["support"]["draw"] = 0.2
        scenes["S2"] = s2

        # S3: 零封
        s3 = {"support": defaultdict(float), "triggered": False}
        if home.cs_rate > th["cs_rate_high"] and away.becs_rate > th["becs_rate_high"]:
            s3["support"]["home_clean"] = 0.4
            s3["triggered"] = True
        if away.cs_rate > th["cs_rate_high"] and home.becs_rate > th["becs_rate_high"]:
            s3["support"]["away_clean"] = 0.4
            s3["triggered"] = True
        if home.becs_rate > th["becs_rate_high"] and away.becs_rate > th["becs_rate_high"]:
            s3["support"]["low_score"] = 0.3
            s3["triggered"] = True
        scenes["S3"] = s3

        # S4: 大球
        s4 = {"support": defaultdict(float), "triggered": False}
        if home.over25_rate > th["over25_high"] and away.over25_rate > th["over25_high"]:
            s4["support"]["high_score"] = 0.4
            s4["triggered"] = True
        if home.btts_rate > th["btts_high"] and away.btts_rate > th["btts_high"]:
            s4["support"]["both_score"] = 0.3
            s4["triggered"] = True
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
        alpha = self.calibration_strength
        for score, sig in raw.items():
            hist = HIST_FREQ.get(score, 0.01)
            calibrated[score] = (1 - alpha) * sig + alpha * hist
        for score, freq in HIST_FREQ.items():
            if score not in calibrated:
                calibrated[score] = alpha * freq
        return calibrated


# ============================================================
# 数据准备
# ============================================================

# 重新加载数据（修复bug）
def load_hist_complete_fixed():
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
            if len(score_odds) < 20:  # 修复：使用score_odds而不是odds
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


hist = load_hist_complete_fixed()
print(f"历史数据：{len(hist)}场")

blind = []
for m in hist:
    if m["date"] < START_DATE:
        continue
    hid, aid = zh.get(m["home_zh"]), zh.get(m["away_zh"])
    if hid and aid:
        blind.append({**m, "hid": hid, "aid": aid})

print(f"盲测样本：{len(blind)}场")

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
            "home_data": home_data,
            "away_data": away_data,
        })
    if kind == "L":
        stats[h].add(hg, ag, True, a)
        stats[a].add(ag, hg, False, h)

by_day = defaultdict(list)
for p in match_packs:
    by_day[p["date"]].append(p)

print(f"有效预测包：{len(match_packs)}场，跨越{len(by_day)}天")


# ============================================================
# 基准验证：确认与 complete_accurate_validation 结果一致
# ============================================================

print("\n" + "=" * 100)
print("0. 基准验证（应与 complete_accurate_validation 结果一致）")
print("=" * 100)


def run_2x1_double(by_day, unit=BASE_UNIT):
    """2串1双选"""
    capital = INITIAL_CAPITAL
    total_cost = 0
    total_payout = 0
    n_tickets = 0
    n_hits = 0
    min_capital = capital
    hit_details = []

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
        hit_combo = None
        for combo in all_bets:
            if all(score == actual for score, odds, actual in combo):
                combo_odds = 1.0
                for score, odds, actual in combo:
                    combo_odds *= odds
                payout += unit * combo_odds
                hit_combo = combo

        capital += payout
        total_payout += payout
        if payout > 0:
            n_hits += 1
            hit_details.append({
                "date": day,
                "cost": cost,
                "payout": payout,
                "combo": hit_combo,
            })

        min_capital = min(min_capital, capital)

    return {
        "final": capital,
        "profit": (capital - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100,
        "n_tickets": n_tickets,
        "n_hits": n_hits,
        "cost": total_cost,
        "payout": total_payout,
        "max_drawdown": (INITIAL_CAPITAL - min_capital) / INITIAL_CAPITAL * 100,
        "hit_details": hit_details,
    }


baseline = run_2x1_double(by_day)
print(f"2串1双选基准：最终{baseline['final']:.1f}元，收益{baseline['profit']:+.1f}%，命中{baseline['n_hits']}/{baseline['n_tickets']}")
print(f"  成本{baseline['cost']:.0f}，派彩{baseline['payout']:.1f}，回撤{baseline['max_drawdown']:.1f}%")


# ============================================================
# 置信度特征分析
# ============================================================

print("\n" + "=" * 100)
print("1. 分析哪些特征与命中率相关")
print("=" * 100)

# 提取所有命中的日期
hit_dates = set(h["date"] for h in baseline["hit_details"])

# 分析每票的特征
ticket_features = []
for day in sorted(by_day):
    day_packs = by_day[day]
    if len(day_packs) < 2:
        continue

    for p in day_packs:
        p["max_signal"] = p["prediction"]["sorted_scores"][0][1]
    selected = sorted(day_packs, key=lambda x: -x["max_signal"])[:2]

    # 特征1：Top1信号强度均值
    avg_signal = sum(p["max_signal"] for p in selected) / 2

    # 特征2：Top1-Top2信号差
    signal_gaps = []
    for p in selected:
        scores = p["prediction"]["sorted_scores"]
        if len(scores) >= 2:
            signal_gaps.append(scores[0][1] - scores[1][1])
    avg_gap = sum(signal_gaps) / len(signal_gaps) if signal_gaps else 0

    # 特征3：场景触发数
    triggered_count = 0
    for p in selected:
        for scene_id, scene in p["prediction"]["scenes"].items():
            if scene.get("triggered", False):
                triggered_count += 1

    # 特征4：数据充足度（历史场次）
    avg_n = sum(p["home_data"].n + p["away_data"].n for p in selected) / 4

    # 特征5：预测比分类型
    top1_scores = [p["prediction"]["sorted_scores"][0][0] for p in selected]
    all_11 = all(s == (1, 1) for s in top1_scores)
    has_00 = any(s == (0, 0) for s in top1_scores)

    is_hit = day in hit_dates

    ticket_features.append({
        "date": day,
        "is_hit": is_hit,
        "avg_signal": avg_signal,
        "avg_gap": avg_gap,
        "triggered_count": triggered_count,
        "avg_n": avg_n,
        "all_11": all_11,
        "has_00": has_00,
    })

# 统计各特征与命中率的关系
print("\n【信号强度与命中率】")
for low, high, label in [(0, 0.1, "<0.1"), (0.1, 0.15, "0.1-0.15"), (0.15, 0.2, "0.15-0.2"), (0.2, 1, ">0.2")]:
    subset = [t for t in ticket_features if low <= t["avg_signal"] < high]
    if subset:
        hit_rate = sum(1 for t in subset if t["is_hit"]) / len(subset) * 100
        print(f"  {label}: {len(subset)}票，命中率{hit_rate:.1f}%")

print("\n【信号差与命中率】")
for low, high, label in [(0, 0.02, "<0.02"), (0.02, 0.04, "0.02-0.04"), (0.04, 0.06, "0.04-0.06"), (0.06, 1, ">0.06")]:
    subset = [t for t in ticket_features if low <= t["avg_gap"] < high]
    if subset:
        hit_rate = sum(1 for t in subset if t["is_hit"]) / len(subset) * 100
        print(f"  {label}: {len(subset)}票，命中率{hit_rate:.1f}%")

print("\n【场景触发数与命中率】")
for count in range(0, 9):
    subset = [t for t in ticket_features if t["triggered_count"] == count]
    if subset:
        hit_rate = sum(1 for t in subset if t["is_hit"]) / len(subset) * 100
        print(f"  {count}个场景: {len(subset)}票，命中率{hit_rate:.1f}%")

print("\n【预测(1,1)与命中率】")
all_11_subset = [t for t in ticket_features if t["all_11"]]
not_11_subset = [t for t in ticket_features if not t["all_11"]]
if all_11_subset:
    print(f"  两场都预测(1,1): {len(all_11_subset)}票，命中率{sum(1 for t in all_11_subset if t['is_hit'])/len(all_11_subset)*100:.1f}%")
if not_11_subset:
    print(f"  不全是(1,1): {len(not_11_subset)}票，命中率{sum(1 for t in not_11_subset if t['is_hit'])/len(not_11_subset)*100:.1f}%")

print("\n【含(0,0)预测与命中率】")
has_00_subset = [t for t in ticket_features if t["has_00"]]
no_00_subset = [t for t in ticket_features if not t["has_00"]]
if has_00_subset:
    print(f"  含(0,0)预测: {len(has_00_subset)}票，命中率{sum(1 for t in has_00_subset if t['is_hit'])/len(has_00_subset)*100:.1f}%")
if no_00_subset:
    print(f"  不含(0,0): {len(no_00_subset)}票，命中率{sum(1 for t in no_00_subset if t['is_hit'])/len(no_00_subset)*100:.1f}%")


# ============================================================
# 倍率策略模拟
# ============================================================

print("\n" + "=" * 100)
print("2. 倍率策略模拟")
print("=" * 100)


def run_with_multiplier(by_day, get_multiplier):
    """带倍率的2串1双选"""
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

        for p in day_packs:
            p["max_signal"] = p["prediction"]["sorted_scores"][0][1]
        selected = sorted(day_packs, key=lambda x: -x["max_signal"])[:2]

        # 计算倍率
        multiplier = get_multiplier(selected)
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

    return {
        "final": capital,
        "profit": (capital - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100,
        "n_tickets": n_tickets,
        "n_hits": n_hits,
        "cost": total_cost,
        "payout": total_payout,
        "max_drawdown": (INITIAL_CAPITAL - min_capital) / INITIAL_CAPITAL * 100,
    }


# 策略定义
strategies = {
    "固定1倍": lambda packs: 1,
    "固定0.5倍": lambda packs: 0.5,
    "高信号2倍": lambda packs: 2 if sum(p["max_signal"] for p in packs)/2 > 0.15 else 1,
    "低信号0.5倍": lambda packs: 0.5 if sum(p["max_signal"] for p in packs)/2 < 0.12 else 1,
    "含00则2倍": lambda packs: 2 if any(p["prediction"]["sorted_scores"][0][0] == (0,0) for p in packs) else 1,
    "全11则2倍": lambda packs: 2 if all(p["prediction"]["sorted_scores"][0][0] == (1,1) for p in packs) else 1,
    "场景多则2倍": lambda packs: 2 if sum(sum(1 for s in p["prediction"]["scenes"].values() if s.get("triggered")) for p in packs) >= 4 else 1,
    "场景少则2倍": lambda packs: 2 if sum(sum(1 for s in p["prediction"]["scenes"].values() if s.get("triggered")) for p in packs) <= 2 else 1,
}

print(f"\n{'策略':<20} {'最终资金':>12} {'收益率':>10} {'命中':>12} {'回撤':>8}")
print("-" * 70)

for name, get_mult in strategies.items():
    result = run_with_multiplier(by_day, get_mult)
    print(f"{name:<20} {result['final']:>12.1f} {result['profit']:>+9.1f}% {result['n_hits']:>4}/{result['n_tickets']:<6} {result['max_drawdown']:>7.1f}%")


print("\n" + "=" * 100)
print("完成")
print("=" * 100)
