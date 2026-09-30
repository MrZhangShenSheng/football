# -*- coding: utf-8 -*-
"""
预测模型v4 —— 对照实验

确保baseline与之前验证一致（+133.5%），然后测试改进版本
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
print("预测模型v4 —— 对照实验")
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
# 原始Baseline预测器（已验证+133.5%）
# ============================================================

HIST_FREQ = {
    (1, 1): 0.12, (2, 1): 0.09, (1, 0): 0.09, (1, 2): 0.08,
    (0, 1): 0.07, (0, 0): 0.07, (2, 0): 0.07, (2, 2): 0.05,
    (0, 2): 0.05, (3, 0): 0.04, (3, 1): 0.04, (3, 2): 0.03,
    (1, 3): 0.03, (2, 3): 0.02, (4, 0): 0.02, (4, 1): 0.02,
    (0, 3): 0.02, (3, 3): 0.01,
}

TH = {
    "gf_low": 1.0, "gf_high": 1.8,
    "ga_low": 0.8, "ga_high": 1.5,
    "cs_rate_high": 0.35, "becs_rate_high": 0.35,
    "btts_high": 0.55, "over25_high": 0.55,
    "form_good": 7, "form_bad": 3,
}


class BaselinePredictor:
    """原始baseline预测器"""

    def predict(self, home, away):
        scenes = self._scenes(home, away)
        raw = self._signals(scenes)
        cal = {s: 0.5*raw.get(s,0)+0.5*HIST_FREQ.get(s,0)
               for s in set(raw)|set(HIST_FREQ)}
        return sorted(cal.items(), key=lambda x: -x[1])

    def _scenes(self, h, a):
        sc = {}
        s1 = {"support": defaultdict(float)}
        if h.gf_home_avg > TH["gf_high"] and a.ga_away_avg > TH["ga_high"]:
            s1["support"]["home_win"] = 0.4
        elif a.gf_away_avg > TH["gf_high"] and h.ga_home_avg > TH["ga_high"]:
            s1["support"]["away_win"] = 0.3
        elif h.gf_avg < TH["gf_low"] and a.gf_avg < TH["gf_low"]:
            s1["support"]["low_score"] = 0.4
            s1["support"]["draw"] = 0.3
        elif h.gf_avg > TH["gf_high"] and a.gf_avg > TH["gf_high"]:
            s1["support"]["high_score"] = 0.4
        else:
            s1["support"]["draw"] = 0.2
        sc["S1"] = s1

        s2 = {"support": defaultdict(float)}
        hf, af = h.form_score(), a.form_score()
        if hf >= TH["form_good"] and af <= TH["form_bad"]:
            s2["support"]["home_win"] = 0.4
        elif af >= TH["form_good"] and hf <= TH["form_bad"]:
            s2["support"]["away_win"] = 0.4
        elif hf <= TH["form_bad"] and af <= TH["form_bad"]:
            s2["support"]["low_score"] = 0.3
            s2["support"]["draw"] = 0.3
        else:
            s2["support"]["draw"] = 0.2
        sc["S2"] = s2

        s3 = {"support": defaultdict(float)}
        if h.cs_rate > TH["cs_rate_high"] and a.becs_rate > TH["becs_rate_high"]:
            s3["support"]["home_clean"] = 0.4
        if a.cs_rate > TH["cs_rate_high"] and h.becs_rate > TH["becs_rate_high"]:
            s3["support"]["away_clean"] = 0.4
        if h.becs_rate > TH["becs_rate_high"] and a.becs_rate > TH["becs_rate_high"]:
            s3["support"]["low_score"] = 0.3
        sc["S3"] = s3

        s4 = {"support": defaultdict(float)}
        if h.over25_rate > TH["over25_high"] and a.over25_rate > TH["over25_high"]:
            s4["support"]["high_score"] = 0.4
        if h.btts_rate > TH["btts_high"] and a.btts_rate > TH["btts_high"]:
            s4["support"]["both_score"] = 0.3
        sc["S4"] = s4

        return sc

    def _signals(self, scenes):
        signals = defaultdict(float)
        effect_map = {
            "home_win": {(1,0):0.4, (2,0):0.3, (2,1):0.3},
            "away_win": {(0,1):0.4, (0,2):0.3, (1,2):0.3},
            "draw": {(1,1):0.5, (0,0):0.3, (2,2):0.2},
            "low_score": {(0,0):0.4, (1,0):0.2, (0,1):0.2, (1,1):0.2},
            "high_score": {(2,2):0.3, (3,1):0.2, (2,3):0.2, (3,2):0.2, (4,1):0.1},
            "home_clean": {(1,0):0.4, (2,0):0.4, (3,0):0.2},
            "away_clean": {(0,1):0.4, (0,2):0.4, (0,3):0.2},
            "both_score": {(1,1):0.3, (2,1):0.2, (1,2):0.2, (2,2):0.2, (3,2):0.1},
        }
        for scene in scenes.values():
            for effect, strength in scene["support"].items():
                if effect in effect_map:
                    for score, weight in effect_map[effect].items():
                        signals[score] += strength * weight
        return dict(signals)


# ============================================================
# 改进版预测器v4a: 增强(2,1)预测
# ============================================================

HIST_FREQ_V4A = {
    (1, 1): 0.11,  # 微降
    (2, 1): 0.10,  # 升高
    (1, 0): 0.09,
    (1, 2): 0.08,
    (0, 1): 0.07,
    (0, 0): 0.07,
    (2, 0): 0.07,
    (2, 2): 0.05,
    (0, 2): 0.05,
    (3, 0): 0.04, (3, 1): 0.04, (3, 2): 0.03,
    (1, 3): 0.03, (2, 3): 0.02, (4, 0): 0.02, (4, 1): 0.02,
    (0, 3): 0.02, (3, 3): 0.01,
}


class PredictorV4a(BaselinePredictor):
    """v4a: 调整历史频率，增强(2,1)"""

    def predict(self, home, away):
        scenes = self._scenes(home, away)
        raw = self._signals(scenes)
        # 使用调整后的频率
        cal = {s: 0.5*raw.get(s,0)+0.5*HIST_FREQ_V4A.get(s,0)
               for s in set(raw)|set(HIST_FREQ_V4A)}
        return sorted(cal.items(), key=lambda x: -x[1])


# ============================================================
# 改进版预测器v4b: 状态组合加成
# ============================================================

HIGH_HIT_FORMS = {
    (3, 5), (5, 2), (5, 5), (2, 2), (2, 1), (4, 5), (2, 5),
    (7, 7), (5, 4), (1, 3), (4, 2), (5, 3), (1, 2), (4, 4),
    (5, 1), (4, 1), (1, 1),
}


class PredictorV4b(BaselinePredictor):
    """v4b: 高命中状态组合加成"""

    def predict(self, home, away):
        scenes = self._scenes(home, away)
        raw = self._signals(scenes)
        cal = {s: 0.5*raw.get(s,0)+0.5*HIST_FREQ.get(s,0)
               for s in set(raw)|set(HIST_FREQ)}

        # 状态组合加成
        hf, af = home.form_score(), away.form_score()
        if (hf, af) in HIGH_HIT_FORMS:
            # 高命中组合：提升平局类比分
            for score in [(1, 1), (0, 0), (2, 2)]:
                if score in cal:
                    cal[score] *= 1.3

        return sorted(cal.items(), key=lambda x: -x[1])


# ============================================================
# 改进版预测器v4c: 调整效果映射，增加(2,1)
# ============================================================

class PredictorV4c(BaselinePredictor):
    """v4c: 调整效果映射"""

    def _signals(self, scenes):
        signals = defaultdict(float)
        # 修改效果映射，增加(2,1)的权重
        effect_map = {
            "home_win": {(1,0):0.3, (2,0):0.25, (2,1):0.35, (3,1):0.1},  # 增加(2,1)
            "away_win": {(0,1):0.3, (0,2):0.25, (1,2):0.35, (1,3):0.1},  # 增加(1,2)
            "draw": {(1,1):0.4, (0,0):0.3, (2,2):0.2, (3,3):0.1},
            "low_score": {(0,0):0.4, (1,0):0.25, (0,1):0.25, (1,1):0.1},
            "high_score": {(2,2):0.2, (3,1):0.2, (2,3):0.15, (3,2):0.2, (2,1):0.15, (1,2):0.1},
            "home_clean": {(1,0):0.35, (2,0):0.35, (3,0):0.2, (4,0):0.1},
            "away_clean": {(0,1):0.35, (0,2):0.35, (0,3):0.2, (0,4):0.1},
            "both_score": {(1,1):0.2, (2,1):0.25, (1,2):0.25, (2,2):0.15, (3,2):0.1, (2,3):0.05},
        }
        for scene in scenes.values():
            for effect, strength in scene["support"].items():
                if effect in effect_map:
                    for score, weight in effect_map[effect].items():
                        signals[score] += strength * weight
        return dict(signals)


# ============================================================
# 改进版预测器v4d: 降低校准强度
# ============================================================

class PredictorV4d(BaselinePredictor):
    """v4d: 降低频率校准强度到0.3"""

    def predict(self, home, away):
        scenes = self._scenes(home, away)
        raw = self._signals(scenes)
        # 校准强度从0.5降到0.3
        cal = {s: 0.7*raw.get(s,0)+0.3*HIST_FREQ.get(s,0)
               for s in set(raw)|set(HIST_FREQ)}
        return sorted(cal.items(), key=lambda x: -x[1])


# ============================================================
# 改进版预测器v4e: 组合改进
# ============================================================

class PredictorV4e(BaselinePredictor):
    """v4e: 组合改进（v4a + v4b + v4c）"""

    def predict(self, home, away):
        scenes = self._scenes(home, away)
        raw = self._signals_v4c(scenes)

        # 使用调整后的频率（v4a）
        cal = {s: 0.5*raw.get(s,0)+0.5*HIST_FREQ_V4A.get(s,0)
               for s in set(raw)|set(HIST_FREQ_V4A)}

        # 状态组合加成（v4b）
        hf, af = home.form_score(), away.form_score()
        if (hf, af) in HIGH_HIT_FORMS:
            for score in [(1, 1), (0, 0), (2, 2)]:
                if score in cal:
                    cal[score] *= 1.3

        return sorted(cal.items(), key=lambda x: -x[1])

    def _signals_v4c(self, scenes):
        signals = defaultdict(float)
        effect_map = {
            "home_win": {(1,0):0.3, (2,0):0.25, (2,1):0.35, (3,1):0.1},
            "away_win": {(0,1):0.3, (0,2):0.25, (1,2):0.35, (1,3):0.1},
            "draw": {(1,1):0.4, (0,0):0.3, (2,2):0.2, (3,3):0.1},
            "low_score": {(0,0):0.4, (1,0):0.25, (0,1):0.25, (1,1):0.1},
            "high_score": {(2,2):0.2, (3,1):0.2, (2,3):0.15, (3,2):0.2, (2,1):0.15, (1,2):0.1},
            "home_clean": {(1,0):0.35, (2,0):0.35, (3,0):0.2, (4,0):0.1},
            "away_clean": {(0,1):0.35, (0,2):0.35, (0,3):0.2, (0,4):0.1},
            "both_score": {(1,1):0.2, (2,1):0.25, (1,2):0.25, (2,2):0.15, (3,2):0.1, (2,3):0.05},
        }
        for scene in scenes.values():
            for effect, strength in scene["support"].items():
                if effect in effect_map:
                    for score, weight in effect_map[effect].items():
                        signals[score] += strength * weight
        return dict(signals)


# ============================================================
# 数据准备
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
match_packs = []

for r in merged:
    kind, date, h, a, hg, ag = r[0], r[1], r[2], r[3], r[4], r[5]
    if kind == "B":
        idx = r[6]
        m = blind[idx]
        if stats[h].n >= sfm.MIN_HIST and stats[a].n >= sfm.MIN_HIST:
            match_packs.append({
                "date": m["date"],
                "actual": m["actual"],
                "odds": m["score_odds"],
                "home": ts_to_td(stats[h]),
                "away": ts_to_td(stats[a]),
            })
    if kind == "L":
        stats[h].add(hg, ag, True, a)
        stats[a].add(ag, hg, False, h)

by_day = defaultdict(list)
for p in match_packs:
    by_day[p["date"]].append(p)

print(f"有效预测包：{len(match_packs)}场，跨越{len(by_day)}天")


# ============================================================
# 回测函数
# ============================================================

def backtest(predictor, name):
    """2串1双选回测"""
    capital = INITIAL_CAPITAL
    total_cost = 0
    total_payout = 0
    n_tickets = 0
    n_hits = 0
    min_capital = capital
    pred_dist = defaultdict(int)
    hit_dist = defaultdict(int)

    for day in sorted(by_day):
        day_packs = by_day[day]
        if len(day_packs) < 2:
            continue

        # 预测
        for p in day_packs:
            pred = predictor.predict(p["home"], p["away"])
            p["pred"] = pred
            p["max_sig"] = pred[0][1] if pred else 0

        # 选场
        selected = sorted(day_packs, key=lambda x: -x["max_sig"])[:2]

        # 选比分
        all_picks = []
        for p in selected:
            picks = []
            for score, sig in p["pred"][:2]:
                odds = p["odds"].get(score, 999)
                picks.append((score, odds, p["actual"]))
                pred_dist[score] += 1
            all_picks.append(picks)

        # 投注
        all_bets = list(product(*all_picks))
        cost = len(all_bets) * BASE_UNIT

        if capital < cost:
            continue

        capital -= cost
        total_cost += cost
        n_tickets += 1

        # 计算派彩
        payout = 0
        for combo in all_bets:
            if all(score == actual for score, odds, actual in combo):
                combo_odds = 1.0
                for score, odds, actual in combo:
                    combo_odds *= odds
                payout += BASE_UNIT * combo_odds
                for score, odds, actual in combo:
                    hit_dist[score] += 1

        capital += payout
        total_payout += payout
        if payout > 0:
            n_hits += 1
        min_capital = min(min_capital, capital)

    roi = (capital - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    drawdown = (INITIAL_CAPITAL - min_capital) / INITIAL_CAPITAL * 100

    print(f"\n【{name}】")
    print(f"  最终资金: {capital:.1f}元, 收益率: {roi:+.1f}%, 命中: {n_hits}/{n_tickets}, 回撤: {drawdown:.1f}%")
    print(f"  预测分布Top5: {sorted(pred_dist.items(), key=lambda x:-x[1])[:5]}")
    print(f"  命中分布: {sorted(hit_dist.items(), key=lambda x:-x[1])[:5]}")

    return {"name": name, "capital": capital, "roi": roi, "hits": n_hits, "tickets": n_tickets}


# ============================================================
# 运行测试
# ============================================================

print("\n" + "=" * 100)
print("对照实验")
print("=" * 100)

results = []
results.append(backtest(BaselinePredictor(), "Baseline（+133.5%验证）"))
results.append(backtest(PredictorV4a(), "v4a: 调整频率"))
results.append(backtest(PredictorV4b(), "v4b: 状态加成"))
results.append(backtest(PredictorV4c(), "v4c: 调整效果映射"))
results.append(backtest(PredictorV4d(), "v4d: 降低校准强度"))
results.append(backtest(PredictorV4e(), "v4e: 组合改进"))

print("\n" + "=" * 100)
print("结果汇总")
print("=" * 100)
print(f"\n{'方案':<25} {'最终资金':>12} {'收益率':>10} {'命中':>10}")
print("-" * 60)
for r in sorted(results, key=lambda x: -x["roi"]):
    print(f"{r['name']:<25} {r['capital']:>12.1f} {r['roi']:>+9.1f}% {r['hits']:>4}/{r['tickets']}")
