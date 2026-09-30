# -*- coding: utf-8 -*-
"""总进球数预测分布分析"""
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, str(Path("engine/scripts/research")))

import score_family_model as sfm

print("=" * 100)
print("总进球数预测分布分析")
print("=" * 100)


# ============================================================
# 数据加载
# ============================================================

def load_hist_full():
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
            odds = {}
            for kk, v in crs.items():
                if str(kk).startswith("other") or not v:
                    continue
                try:
                    hh, aa = (int(x) for x in str(kk).split(":")[:2])
                    odds[(hh, aa)] = float(v)
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
                "home_zh": m.get("home"), "away_zh": m.get("away"),
                "actual": (h, a), "odds": odds,
            })
    out.sort(key=lambda x: x["date"])
    return out


zh = {}
for tid, srcs in sfm.load_aliases().items():
    if srcs.get("zh"):
        zh[srcs["zh"]] = tid

tl = sfm.league_timeline()
hist = load_hist_full()

print(f"数据：历史{len(hist)}场，联赛库{len(tl)}场")


# ============================================================
# TeamData
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
        recent_gd=ts.recent_gd.copy(), recent=ts.recent.copy(),
    )


# ============================================================
# 总进球数预测器
# ============================================================

THRESHOLDS = {
    "gf_low": 1.0, "gf_high": 1.8,
    "ga_low": 0.8, "ga_high": 1.5,
    "over25_high": 0.55, "over25_low": 0.40,
    "btts_high": 0.55,
}


class TotalGoalsPredictor:
    """总进球数预测器"""

    def __init__(self):
        self.th = THRESHOLDS

    def predict(self, home: TeamData, away: TeamData) -> Dict:
        """预测总进球数"""
        th = self.th

        # 预期进球数
        expected_home = (home.gf_home_avg + away.ga_away_avg) / 2
        expected_away = (away.gf_away_avg + home.ga_home_avg) / 2
        expected_total = expected_home + expected_away

        # 大球/小球倾向
        over25_tendency = (home.over25_rate + away.over25_rate) / 2

        # 信号计算
        signals = {}

        # 0-1球
        if expected_total < 2.0 and over25_tendency < th["over25_low"]:
            signals["0-1"] = 0.7
        elif expected_total < 2.3:
            signals["0-1"] = 0.4
        else:
            signals["0-1"] = 0.2

        # 2-3球
        if 2.0 <= expected_total <= 3.0:
            signals["2-3"] = 0.6
        elif expected_total < 2.0:
            signals["2-3"] = 0.4
        else:
            signals["2-3"] = 0.3

        # 4+球
        if expected_total > 3.0 and over25_tendency > th["over25_high"]:
            signals["4+"] = 0.7
        elif expected_total > 2.8:
            signals["4+"] = 0.5
        else:
            signals["4+"] = 0.2

        # 选择最高信号
        best = max(signals.items(), key=lambda x: x[1])

        return {
            "prediction": best[0],
            "confidence": best[1],
            "signals": signals,
            "expected_total": expected_total,
            "over25_tendency": over25_tendency,
        }


# ============================================================
# 准备数据
# ============================================================

START_DATE = "2025-12-01"

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
predictor = TotalGoalsPredictor()

results = []

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

        actual_total = m["actual"][0] + m["actual"][1]
        if actual_total <= 1:
            actual_cat = "0-1"
        elif actual_total <= 3:
            actual_cat = "2-3"
        else:
            actual_cat = "4+"

        results.append({
            "date": m["date"],
            "actual": m["actual"],
            "actual_total": actual_total,
            "actual_cat": actual_cat,
            "prediction": pred["prediction"],
            "confidence": pred["confidence"],
            "signals": pred["signals"],
            "expected_total": pred["expected_total"],
        })

    if kind == "L":
        stats[h].add(hg, ag, True, a)
        stats[a].add(ag, hg, False, h)

print(f"有效预测：{len(results)}场")


# ============================================================
# 分析1：实际分布 vs 预测分布
# ============================================================

print("\n" + "=" * 100)
print("1. 实际分布 vs 预测分布")
print("=" * 100)

actual_dist = Counter(r["actual_cat"] for r in results)
pred_dist = Counter(r["prediction"] for r in results)

print(f"\n{'类别':>10} {'实际出现':>10} {'实际占比':>10} {'预测次数':>10} {'预测占比':>10} {'偏差':>10}")
print("-" * 70)

for cat in ["0-1", "2-3", "4+"]:
    actual_cnt = actual_dist.get(cat, 0)
    actual_pct = actual_cnt / len(results) * 100
    pred_cnt = pred_dist.get(cat, 0)
    pred_pct = pred_cnt / len(results) * 100
    diff = pred_pct - actual_pct
    print(f"{cat:>10} {actual_cnt:>10} {actual_pct:>9.1f}% {pred_cnt:>10} {pred_pct:>9.1f}% {diff:>+9.1f}%")


# ============================================================
# 分析2：各类别预测的命中情况
# ============================================================

print("\n" + "=" * 100)
print("2. 各类别预测的命中情况")
print("=" * 100)

for cat in ["0-1", "2-3", "4+"]:
    pred_this = [r for r in results if r["prediction"] == cat]
    if not pred_this:
        continue

    hits = sum(1 for r in pred_this if r["actual_cat"] == cat)
    hit_rate = hits / len(pred_this) * 100

    # 实际分布
    actual_in_pred = Counter(r["actual_cat"] for r in pred_this)

    print(f"\n【预测={cat}】共{len(pred_this)}场")
    print(f"  命中率：{hits}/{len(pred_this)} = {hit_rate:.1f}%")
    print(f"  实际分布：", end="")
    for c in ["0-1", "2-3", "4+"]:
        cnt = actual_in_pred.get(c, 0)
        pct = cnt / len(pred_this) * 100
        print(f"{c}:{cnt}({pct:.1f}%) ", end="")
    print()


# ============================================================
# 分析3：按信号强度分析
# ============================================================

print("\n" + "=" * 100)
print("3. 按信号强度分析命中率")
print("=" * 100)

for cat in ["0-1", "2-3", "4+"]:
    pred_this = [r for r in results if r["prediction"] == cat]
    if not pred_this:
        continue

    print(f"\n【{cat}】")

    # 按置信度分bin
    bins = [(0.6, 1.0, "高(>0.6)"), (0.4, 0.6, "中(0.4-0.6)"), (0.0, 0.4, "低(<0.4)")]

    for low, high, label in bins:
        bin_results = [r for r in pred_this if low <= r["confidence"] < high]
        if not bin_results:
            continue

        hits = sum(1 for r in bin_results if r["actual_cat"] == cat)
        hit_rate = hits / len(bin_results) * 100

        print(f"  {label}: {len(bin_results)}场, 命中{hits}({hit_rate:.1f}%)")


# ============================================================
# 分析4：预期进球数 vs 实际进球数
# ============================================================

print("\n" + "=" * 100)
print("4. 预期进球数 vs 实际进球数")
print("=" * 100)

# 按预期进球数分bin
exp_bins = [(0, 2.0, "<2.0"), (2.0, 2.5, "2.0-2.5"), (2.5, 3.0, "2.5-3.0"), (3.0, 10, ">3.0")]

print(f"\n{'预期范围':>12} {'样本数':>10} {'平均实际':>10} {'0-1球率':>10} {'2-3球率':>10} {'4+球率':>10}")
print("-" * 75)

for low, high, label in exp_bins:
    bin_results = [r for r in results if low <= r["expected_total"] < high]
    if not bin_results:
        continue

    avg_actual = sum(r["actual_total"] for r in bin_results) / len(bin_results)

    cat_dist = Counter(r["actual_cat"] for r in bin_results)
    rate_01 = cat_dist.get("0-1", 0) / len(bin_results) * 100
    rate_23 = cat_dist.get("2-3", 0) / len(bin_results) * 100
    rate_4p = cat_dist.get("4+", 0) / len(bin_results) * 100

    print(f"{label:>12} {len(bin_results):>10} {avg_actual:>10.2f} {rate_01:>9.1f}% {rate_23:>9.1f}% {rate_4p:>9.1f}%")


# ============================================================
# 分析5：混淆矩阵
# ============================================================

print("\n" + "=" * 100)
print("5. 混淆矩阵")
print("=" * 100)

print(f"\n{'':>12} {'实际0-1':>10} {'实际2-3':>10} {'实际4+':>10} {'合计':>10} {'精确率':>10}")
print("-" * 75)

for pred_cat in ["0-1", "2-3", "4+"]:
    pred_this = [r for r in results if r["prediction"] == pred_cat]

    cnt_01 = sum(1 for r in pred_this if r["actual_cat"] == "0-1")
    cnt_23 = sum(1 for r in pred_this if r["actual_cat"] == "2-3")
    cnt_4p = sum(1 for r in pred_this if r["actual_cat"] == "4+")
    total = len(pred_this)

    # 精确率
    if pred_cat == "0-1":
        precision = cnt_01 / total * 100 if total > 0 else 0
    elif pred_cat == "2-3":
        precision = cnt_23 / total * 100 if total > 0 else 0
    else:
        precision = cnt_4p / total * 100 if total > 0 else 0

    print(f"{'预测'+pred_cat:>12} {cnt_01:>10} {cnt_23:>10} {cnt_4p:>10} {total:>10} {precision:>9.1f}%")

# 召回率
print("-" * 75)
for actual_cat in ["0-1", "2-3", "4+"]:
    actual_this = [r for r in results if r["actual_cat"] == actual_cat]
    hits = sum(1 for r in actual_this if r["prediction"] == actual_cat)
    recall = hits / len(actual_this) * 100 if actual_this else 0
    print(f"{'召回率'+actual_cat:>12} {recall:>9.1f}%")


# ============================================================
# 分析6：总进球数详细分布
# ============================================================

print("\n" + "=" * 100)
print("6. 总进球数详细分布（0-7+球）")
print("=" * 100)

total_dist = Counter(r["actual_total"] for r in results)

print(f"\n{'总进球':>10} {'出现次数':>10} {'占比':>10} {'累计占比':>12}")
print("-" * 50)

cumulative = 0
for goals in range(8):
    cnt = total_dist.get(goals, 0)
    pct = cnt / len(results) * 100
    cumulative += pct
    print(f"{goals:>10} {cnt:>10} {pct:>9.1f}% {cumulative:>11.1f}%")

# 7+球
cnt_7p = sum(cnt for g, cnt in total_dist.items() if g >= 7)
pct_7p = cnt_7p / len(results) * 100
print(f"{'7+':>10} {cnt_7p:>10} {pct_7p:>9.1f}% {'100.0':>11}%")


print("\n" + "=" * 100)
print("分析完成")
print("=" * 100)
