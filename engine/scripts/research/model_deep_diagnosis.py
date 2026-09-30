# -*- coding: utf-8 -*-
"""
预测模型深度诊断

分析问题：
1. 预测准确性：Top1/Top2/Top4命中率
2. 信号区分度：不同信号强度的命中率差异
3. 场景有效性：各场景的预测价值
4. 赔率价值计算：是否合理
5. 命中比分特征：什么样的比分容易命中
"""
import json
import sys
from collections import defaultdict, Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, str(Path("engine/scripts/research")))

import score_family_model as sfm

print("=" * 100)
print("预测模型深度诊断")
print("=" * 100)

START_DATE = "2025-12-01"


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
# 当前预测器
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


class CurrentPredictor:
    """当前预测器"""

    def predict(self, home, away, odds=None):
        scenes = self._scenes(home, away)
        raw = self._signals(scenes)
        cal = {s: 0.5*raw.get(s,0)+0.5*HIST_FREQ.get(s,0)
               for s in set(raw)|set(HIST_FREQ)}
        sorted_scores = sorted(cal.items(), key=lambda x: -x[1])

        # 计算各种指标
        top1 = sorted_scores[0] if sorted_scores else (None, 0)
        top2 = sorted_scores[1] if len(sorted_scores) > 1 else (None, 0)

        # 赔率相关
        implied_prob = 1/odds.get(top1[0], 100) if odds and top1[0] else 0

        return {
            "sorted_scores": sorted_scores,
            "scenes": scenes,
            "top1_signal": top1[1],
            "signal_gap": top1[1] - top2[1] if top2[0] else 0,
            "implied_prob": implied_prob,
            "odds_value": top1[1] / implied_prob if implied_prob > 0 else 0,
        }

    def _scenes(self, h, a):
        sc = {}

        # S1: 攻防
        s1 = {"support": defaultdict(float), "name": "攻防", "triggered": None}
        if h.gf_home_avg > TH["gf_high"] and a.ga_away_avg > TH["ga_high"]:
            s1["support"]["home_win"] = 0.4
            s1["triggered"] = "主攻强+客守弱"
        elif a.gf_away_avg > TH["gf_high"] and h.ga_home_avg > TH["ga_high"]:
            s1["support"]["away_win"] = 0.3
            s1["triggered"] = "客攻强+主守弱"
        elif h.gf_avg < TH["gf_low"] and a.gf_avg < TH["gf_low"]:
            s1["support"]["low_score"] = 0.4
            s1["support"]["draw"] = 0.3
            s1["triggered"] = "双方低产"
        elif h.gf_avg > TH["gf_high"] and a.gf_avg > TH["gf_high"]:
            s1["support"]["high_score"] = 0.4
            s1["triggered"] = "双方高产"
        else:
            s1["support"]["draw"] = 0.2
            s1["triggered"] = "均衡"
        sc["S1"] = s1

        # S2: 状态
        s2 = {"support": defaultdict(float), "name": "状态", "triggered": None}
        hf, af = h.form_score(), a.form_score()
        if hf >= TH["form_good"] and af <= TH["form_bad"]:
            s2["support"]["home_win"] = 0.4
            s2["triggered"] = f"主状态好({hf})客差({af})"
        elif af >= TH["form_good"] and hf <= TH["form_bad"]:
            s2["support"]["away_win"] = 0.4
            s2["triggered"] = f"客状态好({af})主差({hf})"
        elif hf <= TH["form_bad"] and af <= TH["form_bad"]:
            s2["support"]["low_score"] = 0.3
            s2["support"]["draw"] = 0.3
            s2["triggered"] = f"双方状态差({hf},{af})"
        else:
            s2["support"]["draw"] = 0.2
            s2["triggered"] = f"状态接近({hf},{af})"
        sc["S2"] = s2

        # S3: 零封
        s3 = {"support": defaultdict(float), "name": "零封", "triggered": None}
        triggers = []
        if h.cs_rate > TH["cs_rate_high"] and a.becs_rate > TH["becs_rate_high"]:
            s3["support"]["home_clean"] = 0.4
            triggers.append("主零封强")
        if a.cs_rate > TH["cs_rate_high"] and h.becs_rate > TH["becs_rate_high"]:
            s3["support"]["away_clean"] = 0.4
            triggers.append("客零封强")
        if h.becs_rate > TH["becs_rate_high"] and a.becs_rate > TH["becs_rate_high"]:
            s3["support"]["low_score"] = 0.3
            triggers.append("双方被零封高")
        s3["triggered"] = "+".join(triggers) if triggers else None
        sc["S3"] = s3

        # S4: 大球
        s4 = {"support": defaultdict(float), "name": "大球", "triggered": None}
        triggers = []
        if h.over25_rate > TH["over25_high"] and a.over25_rate > TH["over25_high"]:
            s4["support"]["high_score"] = 0.4
            triggers.append("双方大球率高")
        if h.btts_rate > TH["btts_high"] and a.btts_rate > TH["btts_high"]:
            s4["support"]["both_score"] = 0.3
            triggers.append("双方BTTS高")
        s4["triggered"] = "+".join(triggers) if triggers else None
        sc["S4"] = s4

        return sc

    def _signals(self, scenes):
        signals = defaultdict(float)
        mapping = {
            "home_win": {(1,0):0.4,(2,0):0.3,(2,1):0.3},
            "away_win": {(0,1):0.4,(0,2):0.3,(1,2):0.3},
            "draw": {(1,1):0.5,(0,0):0.3,(2,2):0.2},
            "low_score": {(0,0):0.4,(1,0):0.2,(0,1):0.2,(1,1):0.2},
            "high_score": {(2,2):0.3,(3,1):0.2,(2,3):0.2,(3,2):0.2,(3,0):0.1},
            "home_clean": {(1,0):0.4,(2,0):0.4,(3,0):0.2},
            "away_clean": {(0,1):0.4,(0,2):0.4,(0,3):0.2},
            "both_score": {(1,1):0.3,(2,1):0.2,(1,2):0.2,(2,2):0.2,(3,2):0.1},
        }
        for sc in scenes.values():
            for eff, strength in sc["support"].items():
                if eff in mapping:
                    for score, w in mapping[eff].items():
                        signals[score] += strength * w
        return dict(signals)


# ============================================================
# 构建预测包
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
predictor = CurrentPredictor()
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
        pred = predictor.predict(home_data, away_data, m["score_odds"])
        match_packs.append({
            "date": m["date"],
            "actual": m["actual"],
            "odds": m["score_odds"],
            "pred": pred,
            "home": home_data,
            "away": away_data,
        })
    if kind == "L":
        stats[h].add(hg, ag, True, a)
        stats[a].add(ag, hg, False, h)

print(f"有效预测包：{len(match_packs)}场")


# ============================================================
# 诊断1：预测准确性
# ============================================================

print("\n" + "=" * 100)
print("诊断1：预测准确性")
print("=" * 100)

top1_hit = top2_hit = top3_hit = top4_hit = 0
for p in match_packs:
    actual = p["actual"]
    scores = [s[0] for s in p["pred"]["sorted_scores"]]
    if actual == scores[0]: top1_hit += 1
    if actual in scores[:2]: top2_hit += 1
    if actual in scores[:3]: top3_hit += 1
    if actual in scores[:4]: top4_hit += 1

n = len(match_packs)
print(f"Top1命中率: {top1_hit}/{n} = {top1_hit/n*100:.1f}%")
print(f"Top2命中率: {top2_hit}/{n} = {top2_hit/n*100:.1f}%")
print(f"Top3命中率: {top3_hit}/{n} = {top3_hit/n*100:.1f}%")
print(f"Top4命中率: {top4_hit}/{n} = {top4_hit/n*100:.1f}%")


# ============================================================
# 诊断2：信号分布与命中率
# ============================================================

print("\n" + "=" * 100)
print("诊断2：信号分布与命中率")
print("=" * 100)

# Top1信号强度分布
signal_bins = [(0, 0.1), (0.1, 0.12), (0.12, 0.14), (0.14, 0.16), (0.16, 0.2), (0.2, 1)]
print("\n【Top1信号强度】")
for low, high in signal_bins:
    subset = [p for p in match_packs if low <= p["pred"]["top1_signal"] < high]
    if subset:
        hits = sum(1 for p in subset if p["actual"] == p["pred"]["sorted_scores"][0][0])
        print(f"  {low:.2f}-{high:.2f}: {len(subset)}场，命中{hits}，命中率{hits/len(subset)*100:.1f}%")

# 信号差分布
print("\n【Top1-Top2信号差】")
gap_bins = [(0, 0.01), (0.01, 0.02), (0.02, 0.03), (0.03, 0.05), (0.05, 1)]
for low, high in gap_bins:
    subset = [p for p in match_packs if low <= p["pred"]["signal_gap"] < high]
    if subset:
        hits = sum(1 for p in subset if p["actual"] == p["pred"]["sorted_scores"][0][0])
        print(f"  {low:.2f}-{high:.2f}: {len(subset)}场，命中{hits}，命中率{hits/len(subset)*100:.1f}%")


# ============================================================
# 诊断3：场景触发与命中率
# ============================================================

print("\n" + "=" * 100)
print("诊断3：场景触发与命中率")
print("=" * 100)

for scene_id in ["S1", "S2", "S3", "S4"]:
    print(f"\n【{scene_id}场景】")
    trigger_stats = defaultdict(lambda: {"total": 0, "hit": 0})
    for p in match_packs:
        trigger = p["pred"]["scenes"][scene_id]["triggered"]
        trigger_stats[trigger]["total"] += 1
        if p["actual"] == p["pred"]["sorted_scores"][0][0]:
            trigger_stats[trigger]["hit"] += 1

    for trigger, s in sorted(trigger_stats.items(), key=lambda x: -x[1]["total"]):
        if s["total"] >= 10:
            rate = s["hit"]/s["total"]*100
            print(f"  {trigger or '无触发'}: {s['total']}场，命中{s['hit']}，{rate:.1f}%")


# ============================================================
# 诊断4：预测比分与实际比分分布
# ============================================================

print("\n" + "=" * 100)
print("诊断4：预测 vs 实际比分分布")
print("=" * 100)

# Top1预测分布
pred_dist = Counter(p["pred"]["sorted_scores"][0][0] for p in match_packs)
actual_dist = Counter(p["actual"] for p in match_packs)

print("\n【Top1预测分布 vs 实际分布】")
print(f"{'比分':>10} {'预测次数':>10} {'实际出现':>10} {'命中':>8} {'命中率':>10}")
print("-" * 60)

for score in sorted(pred_dist.keys(), key=lambda x: -pred_dist[x])[:10]:
    pred_count = pred_dist[score]
    actual_count = actual_dist.get(score, 0)
    # 统计该比分被预测为Top1且命中的次数
    hits = sum(1 for p in match_packs
               if p["pred"]["sorted_scores"][0][0] == score and p["actual"] == score)
    rate = hits / pred_count * 100 if pred_count > 0 else 0
    print(f"{str(score):>10} {pred_count:>10} {actual_count:>10} {hits:>8} {rate:>9.1f}%")


# ============================================================
# 诊断5：赔率价值分布
# ============================================================

print("\n" + "=" * 100)
print("诊断5：赔率价值分布")
print("=" * 100)

odds_values = [p["pred"]["odds_value"] for p in match_packs]
print(f"赔率价值范围: {min(odds_values):.2f} - {max(odds_values):.2f}")
print(f"赔率价值均值: {sum(odds_values)/len(odds_values):.2f}")
print(f"赔率价值>0.5的比例: {sum(1 for v in odds_values if v > 0.5)/len(odds_values)*100:.1f}%")
print(f"赔率价值>1.0的比例: {sum(1 for v in odds_values if v > 1.0)/len(odds_values)*100:.1f}%")

print("\n【赔率价值分段命中率】")
ov_bins = [(0, 0.5), (0.5, 1.0), (1.0, 1.5), (1.5, 2.0), (2.0, 100)]
for low, high in ov_bins:
    subset = [p for p in match_packs if low <= p["pred"]["odds_value"] < high]
    if subset:
        hits = sum(1 for p in subset if p["actual"] == p["pred"]["sorted_scores"][0][0])
        print(f"  {low:.1f}-{high:.1f}: {len(subset)}场，命中{hits}，命中率{hits/len(subset)*100:.1f}%")


# ============================================================
# 诊断6：命中场次特征分析
# ============================================================

print("\n" + "=" * 100)
print("诊断6：命中场次特征分析")
print("=" * 100)

hit_packs = [p for p in match_packs if p["actual"] == p["pred"]["sorted_scores"][0][0]]
miss_packs = [p for p in match_packs if p["actual"] != p["pred"]["sorted_scores"][0][0]]

print(f"\n命中场次: {len(hit_packs)}场")
print(f"未命中场次: {len(miss_packs)}场")

# 命中比分分布
hit_scores = Counter(p["actual"] for p in hit_packs)
print(f"\n【命中比分分布Top5】")
for score, count in hit_scores.most_common(5):
    print(f"  {score}: {count}次")

# 命中场次的场景特征
print(f"\n【命中场次的场景触发】")
for scene_id in ["S1", "S2", "S3", "S4"]:
    triggers = Counter(p["pred"]["scenes"][scene_id]["triggered"] for p in hit_packs)
    print(f"  {scene_id}: {triggers.most_common(3)}")


print("\n" + "=" * 100)
print("诊断完成")
print("=" * 100)
