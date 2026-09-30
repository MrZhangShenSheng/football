# -*- coding: utf-8 -*-
"""
v4b回测数据核对 —— 输出完整明细供人工核验

输出内容：
1. 每票的详细预测和实际结果
2. 赔率来源验证
3. 派彩计算验证
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
print("v4b回测数据核对 —— 完整明细")
print("=" * 100)

INITIAL_CAPITAL = 1000.0
START_DATE = "2025-10-01"
BASE_UNIT = 2.0


# ============================================================
# 数据加载
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
# v4b预测器
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

DRAW_BOOST_COMBOS = {
    (3, 5): 0.4, (5, 2): 0.4, (5, 5): 0.35, (2, 2): 0.3, (2, 1): 0.3,
    (4, 5): 0.25, (2, 5): 0.25, (7, 7): 0.2, (5, 4): 0.2, (1, 3): 0.2,
    (4, 2): 0.15, (5, 3): 0.15, (1, 2): 0.15, (4, 4): 0.15, (5, 1): 0.15,
    (4, 1): 0.15, (1, 1): 0.15,
}

HOME_BOOST_COMBOS = {
    (9, 0): 0.2, (9, 1): 0.15, (9, 2): 0.15, (9, 3): 0.1,
    (7, 0): 0.15, (7, 1): 0.1, (7, 2): 0.1, (7, 3): 0.1,
}

BOOST_FACTOR = 1.25


class PredictorV4b:
    def predict(self, home: TeamData, away: TeamData) -> List[Tuple]:
        scenes = self._scenes(home, away)
        raw = self._signals(scenes)
        cal = {s: 0.5*raw.get(s,0)+0.5*HIST_FREQ.get(s,0) for s in set(raw)|set(HIST_FREQ)}
        boosted = self._apply_boost(cal, home, away)
        return sorted(boosted.items(), key=lambda x: -x[1])

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
        sig = defaultdict(float)
        mapping = {
            "home_win": {(1,0):0.4,(2,0):0.3,(2,1):0.3},
            "away_win": {(0,1):0.4,(0,2):0.3,(1,2):0.3},
            "draw": {(1,1):0.5,(0,0):0.3,(2,2):0.2},
            "low_score": {(0,0):0.4,(1,0):0.2,(0,1):0.2,(1,1):0.2},
            "high_score": {(2,2):0.3,(3,1):0.2,(2,3):0.2,(3,2):0.2},
            "home_clean": {(1,0):0.4,(2,0):0.4,(3,0):0.2},
            "away_clean": {(0,1):0.4,(0,2):0.4,(0,3):0.2},
            "both_score": {(1,1):0.3,(2,1):0.2,(1,2):0.2,(2,2):0.2,(3,2):0.1},
        }
        for sc in scenes.values():
            for eff, st in sc["support"].items():
                if eff in mapping:
                    for score, w in mapping[eff].items():
                        sig[score] += st * w
        return dict(sig)

    def _apply_boost(self, signals, h, a):
        hf, af = h.form_score(), a.form_score()
        boosted = signals.copy()

        # A类：平局加成
        if (hf, af) in DRAW_BOOST_COMBOS:
            boost = DRAW_BOOST_COMBOS[(hf, af)] * BOOST_FACTOR
            for score in [(1,1), (0,0), (2,2)]:
                if score in boosted:
                    boosted[score] *= (1 + boost)

        # C类：主队主导加成
        if (hf, af) in HOME_BOOST_COMBOS:
            boost = HOME_BOOST_COMBOS[(hf, af)] * BOOST_FACTOR
            for score in [(1,0), (2,0), (2,1), (3,0), (3,1)]:
                if score in boosted:
                    boosted[score] *= (1 + boost)

        return boosted


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
predictor = PredictorV4b()
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
            "home": m["home_zh"],
            "away": m["away_zh"],
            "actual": m["actual"],
            "odds": m["score_odds"],
            "pred": pred,
            "home_form": home_data.form_score(),
            "away_form": away_data.form_score(),
        })
    if kind == "L":
        stats[h].add(hg, ag, True, a)
        stats[a].add(ag, hg, False, h)

by_day = defaultdict(list)
for p in match_packs:
    by_day[p["date"]].append(p)

print(f"有效预测包：{len(match_packs)}场，跨越{len(by_day)}天")


# ============================================================
# 输出完整明细
# ============================================================

print("\n" + "=" * 100)
print("完整投注明细（每票）")
print("=" * 100)

capital = INITIAL_CAPITAL
ticket_no = 0
all_tickets = []

for day in sorted(by_day):
    day_packs = by_day[day]
    if len(day_packs) < 2:
        continue

    # 选场
    for p in day_packs:
        p["max_signal"] = p["pred"][0][1]
    selected = sorted(day_packs, key=lambda x: -x["max_signal"])[:2]

    # 每场选Top2比分
    all_picks = []
    for p in selected:
        picks = []
        for score, sig in p["pred"][:2]:
            odds = p["odds"].get(score, 0)
            if odds > 0:
                picks.append({
                    "score": score,
                    "signal": sig,
                    "odds": odds,
                    "actual": p["actual"],
                    "home": p["home"],
                    "away": p["away"],
                    "home_form": p["home_form"],
                    "away_form": p["away_form"],
                })
        if picks:
            all_picks.append(picks)

    if len(all_picks) < 2:
        continue

    # 生成所有组合
    all_bets = list(product(*all_picks))
    cost = len(all_bets) * BASE_UNIT

    if capital < cost:
        continue

    ticket_no += 1
    capital -= cost

    # 检查命中
    payout = 0
    hit_combo = None
    for combo in all_bets:
        all_correct = all(c["score"] == c["actual"] for c in combo)
        if all_correct:
            combo_odds = 1.0
            for c in combo:
                combo_odds *= c["odds"]
            payout = BASE_UNIT * combo_odds
            hit_combo = combo
            break

    capital += payout
    is_hit = payout > 0

    # 记录
    ticket = {
        "no": ticket_no,
        "date": day,
        "cost": cost,
        "payout": payout,
        "is_hit": is_hit,
        "capital_after": capital,
        "matches": selected,
        "picks": all_picks,
        "hit_combo": hit_combo,
    }
    all_tickets.append(ticket)

    # 输出明细
    print(f"\n{'='*80}")
    print(f"票#{ticket_no} | 日期:{day} | 成本:{cost}元 | 派彩:{payout:.1f}元 | {'✅命中' if is_hit else '❌未中'} | 余额:{capital:.1f}元")
    print(f"{'='*80}")

    for i, p in enumerate(selected, 1):
        print(f"\n  场{i}: {p['home']} vs {p['away']}")
        print(f"       状态: 主队{p['home_form']}分 vs 客队{p['away_form']}分")
        print(f"       实际结果: {p['actual'][0]}:{p['actual'][1]}")
        print(f"       预测Top2:")
        for score, sig in p["pred"][:2]:
            odds = p["odds"].get(score, 0)
            is_correct = score == p["actual"]
            mark = " ✅" if is_correct else ""
            print(f"         {score[0]}:{score[1]} 信号{sig:.4f} 赔率{odds}{mark}")

    print(f"\n  投注组合({len(all_bets)}注):")
    for idx, combo in enumerate(all_bets, 1):
        combo_odds = 1.0
        combo_str = []
        for c in combo:
            combo_odds *= c["odds"]
            combo_str.append(f"{c['score'][0]}:{c['score'][1]}@{c['odds']}")
        combo_payout = BASE_UNIT * combo_odds
        is_this = hit_combo and all(c1["score"] == c2["score"] for c1, c2 in zip(combo, hit_combo))
        mark = " ← 命中" if is_this else ""
        print(f"    注{idx}: {' × '.join(combo_str)} = 串关赔率{combo_odds:.2f} 可得{combo_payout:.1f}元{mark}")

    if is_hit:
        print(f"\n  ✅ 命中! 派彩计算: {BASE_UNIT}元 × {hit_combo[0]['odds']} × {hit_combo[1]['odds']} = {payout:.1f}元")

# 汇总
print("\n" + "=" * 100)
print("汇总统计")
print("=" * 100)
print(f"总票数: {len(all_tickets)}")
print(f"命中票数: {sum(1 for t in all_tickets if t['is_hit'])}")
print(f"命中率: {sum(1 for t in all_tickets if t['is_hit'])/len(all_tickets)*100:.1f}%")
print(f"总成本: {sum(t['cost'] for t in all_tickets)}元")
print(f"总派彩: {sum(t['payout'] for t in all_tickets):.1f}元")
print(f"净盈亏: {sum(t['payout'] for t in all_tickets) - sum(t['cost'] for t in all_tickets):.1f}元")
print(f"最终资金: {capital:.1f}元")
print(f"收益率: {(capital - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100:.1f}%")
