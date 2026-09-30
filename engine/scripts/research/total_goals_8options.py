# -*- coding: utf-8 -*-
"""总进球数预测 —— 按竞彩实际选项分析（0/1/2/3/4/5/6/7+）"""
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, str(Path("engine/scripts/research")))

import score_family_model as sfm

print("=" * 100)
print("总进球数预测 —— 按竞彩实际选项分析")
print("竞彩总进球数选项：0、1、2、3、4、5、6、7+（共8个选项）")
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
    # 新增：总进球数分布
    total_goals_dist: Dict[int, int] = field(default_factory=lambda: defaultdict(int))

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

    def get_total_goals_rate(self, goal: int) -> float:
        """获取某个总进球数的历史出现率"""
        if self.n == 0:
            return 0
        return self.total_goals_dist[goal] / self.n


def ts_to_td(ts, total_goals_dist=None) -> TeamData:
    td = TeamData(
        n=ts.n, gf=ts.gf, ga=ts.ga,
        cs=ts.cs, becs=ts.becs, btts=ts.btts, over25=ts.over25,
        gf_home=ts.gf_side[0][0], gf_home_n=ts.gf_side[0][1],
        gf_away=ts.gf_side[1][0], gf_away_n=ts.gf_side[1][1],
        ga_home=ts.ga_side[0][0], ga_home_n=ts.ga_side[0][1],
        ga_away=ts.ga_side[1][0], ga_away_n=ts.ga_side[1][1],
        recent=ts.recent.copy(),
    )
    if total_goals_dist:
        td.total_goals_dist = total_goals_dist.copy()
    return td


# ============================================================
# 竞彩总进球数预测器（8选项版）
# ============================================================

# 竞彩总进球数选项
TOTAL_GOALS_OPTIONS = [0, 1, 2, 3, 4, 5, 6, "7+"]


def actual_to_option(total: int) -> str:
    """将实际总进球数转换为竞彩选项"""
    if total >= 7:
        return "7+"
    return str(total)


class TotalGoalsPredictor8:
    """竞彩总进球数预测器（8选项）"""

    def __init__(self):
        # 历史总进球数分布（从数据中统计）
        self.hist_dist = {
            "0": 0.063, "1": 0.155, "2": 0.230, "3": 0.227,
            "4": 0.154, "5": 0.105, "6": 0.037, "7+": 0.029
        }

    def predict(self, home: TeamData, away: TeamData) -> Dict:
        """预测总进球数分布"""

        # 预期进球数
        expected_home = (home.gf_home_avg + away.ga_away_avg) / 2
        expected_away = (away.gf_away_avg + home.ga_home_avg) / 2
        expected_total = expected_home + expected_away

        # 使用泊松分布计算各选项概率
        probs = self._calc_poisson_probs(expected_total)

        # 结合历史频率校准
        calibrated = {}
        for opt in ["0", "1", "2", "3", "4", "5", "6", "7+"]:
            raw = probs.get(opt, 0)
            hist = self.hist_dist.get(opt, 0)
            # 混合：70%模型 + 30%历史
            calibrated[opt] = 0.7 * raw + 0.3 * hist

        # 归一化
        total_prob = sum(calibrated.values())
        for opt in calibrated:
            calibrated[opt] /= total_prob

        # 排序
        sorted_opts = sorted(calibrated.items(), key=lambda x: -x[1])

        return {
            "probabilities": calibrated,
            "sorted_options": sorted_opts,
            "expected_total": expected_total,
            "top1": sorted_opts[0][0],
            "top1_prob": sorted_opts[0][1],
        }

    def _calc_poisson_probs(self, lam: float) -> Dict[str, float]:
        """使用泊松分布计算概率"""
        import math
        probs = {}
        for k in range(7):
            # P(X=k) = (λ^k * e^-λ) / k!
            p = (lam ** k) * math.exp(-lam) / math.factorial(k)
            probs[str(k)] = p
        # 7+ = 1 - P(X<=6)
        probs["7+"] = 1 - sum(probs.values())
        return probs


# ============================================================
# 准备数据
# ============================================================

cut = "2025-12-01"

blind = []
for m in hist:
    if m["date"] < cut:
        continue
    hid, aid = zh.get(m["home_zh"]), zh.get(m["away_zh"])
    if hid and aid:
        blind.append({**m, "hid": hid, "aid": aid})

merged = [("L", d, h, a, hg, ag, None) for d, h, a, hg, ag in tl]
merged += [("B", m["date"], m["hid"], m["aid"], m["actual"][0], m["actual"][1], i)
           for i, m in enumerate(blind)]
merged.sort(key=lambda r: (r[1], 0 if r[0] == "L" else 1))

# 扩展 TeamStats 以记录总进球数分布
class TeamStatsExt:
    def __init__(self):
        self.base = sfm.TeamStats()
        self.total_goals_dist = defaultdict(int)

    def add(self, scored, conceded, at_home, opp):
        self.base.add(scored, conceded, at_home, opp)
        total = scored + conceded
        self.total_goals_dist[min(total, 7)] += 1

stats = defaultdict(TeamStatsExt)
predictor = TotalGoalsPredictor8()
results = []

for r in merged:
    kind, date, h, a, hg, ag = r[0], r[1], r[2], r[3], r[4], r[5]

    if kind == "B":
        idx = r[6]
        m = blind[idx]

        if stats[h].base.n < sfm.MIN_HIST or stats[a].base.n < sfm.MIN_HIST:
            continue

        home_data = ts_to_td(stats[h].base, stats[h].total_goals_dist)
        away_data = ts_to_td(stats[a].base, stats[a].total_goals_dist)
        pred = predictor.predict(home_data, away_data)

        actual_total = hg + ag
        actual_opt = actual_to_option(actual_total)

        results.append({
            "date": m["date"],
            "actual": m["actual"],
            "actual_total": actual_total,
            "actual_opt": actual_opt,
            "prediction": pred,
        })

    if kind == "L":
        stats[h].add(hg, ag, True, a)
        stats[a].add(ag, hg, False, h)

print(f"有效预测：{len(results)}场")


# ============================================================
# 分析
# ============================================================

print("\n" + "=" * 100)
print("1. 实际总进球数分布（竞彩8选项）")
print("=" * 100)

actual_dist = Counter(r["actual_opt"] for r in results)
print(f"\n{'选项':>6} {'出现次数':>10} {'占比':>10} {'累计':>10}")
print("-" * 45)
cumsum = 0
for opt in ["0", "1", "2", "3", "4", "5", "6", "7+"]:
    cnt = actual_dist.get(opt, 0)
    pct = cnt / len(results) * 100
    cumsum += pct
    print(f"{opt:>6} {cnt:>10} {pct:>9.1f}% {cumsum:>9.1f}%")


print("\n" + "=" * 100)
print("2. 预测分布 vs 实际分布")
print("=" * 100)

pred_dist = Counter(r["prediction"]["top1"] for r in results)
print(f"\n{'选项':>6} {'实际出现':>10} {'实际占比':>10} {'预测次数':>10} {'预测占比':>10} {'偏差':>10}")
print("-" * 70)
for opt in ["0", "1", "2", "3", "4", "5", "6", "7+"]:
    actual_cnt = actual_dist.get(opt, 0)
    actual_pct = actual_cnt / len(results) * 100
    pred_cnt = pred_dist.get(opt, 0)
    pred_pct = pred_cnt / len(results) * 100
    diff = pred_pct - actual_pct
    mark = ""
    if diff > 10:
        mark = "过多"
    elif diff < -5:
        mark = "不足"
    print(f"{opt:>6} {actual_cnt:>10} {actual_pct:>9.1f}% {pred_cnt:>10} {pred_pct:>9.1f}% {diff:>+9.1f}% {mark}")


print("\n" + "=" * 100)
print("3. 各选项的预测命中率")
print("=" * 100)

print(f"\n{'预测':>6} {'预测次数':>10} {'命中':>10} {'命中率':>10} {'平均概率':>10}")
print("-" * 55)

for opt in ["0", "1", "2", "3", "4", "5", "6", "7+"]:
    pred_results = [r for r in results if r["prediction"]["top1"] == opt]
    if not pred_results:
        print(f"{opt:>6} {0:>10} {'-':>10} {'-':>10} {'-':>10}")
        continue

    hits = sum(1 for r in pred_results if r["actual_opt"] == opt)
    hit_rate = hits / len(pred_results) * 100
    avg_prob = sum(r["prediction"]["top1_prob"] for r in pred_results) / len(pred_results) * 100

    print(f"{opt:>6} {len(pred_results):>10} {hits:>10} {hit_rate:>9.1f}% {avg_prob:>9.1f}%")


print("\n" + "=" * 100)
print("4. Top2/Top3 命中率分析")
print("=" * 100)

for k in [1, 2, 3]:
    hits = 0
    for r in results:
        top_k = [opt for opt, _ in r["prediction"]["sorted_options"][:k]]
        if r["actual_opt"] in top_k:
            hits += 1
    print(f"  Top{k} 命中率：{hits}/{len(results)} = {hits/len(results)*100:.1f}%")


print("\n" + "=" * 100)
print("5. 按预期进球数分段分析")
print("=" * 100)

expected_bins = [
    (0, 2.0, "<2.0"),
    (2.0, 2.5, "2.0-2.5"),
    (2.5, 3.0, "2.5-3.0"),
    (3.0, 3.5, "3.0-3.5"),
    (3.5, 10, ">3.5"),
]

print(f"\n{'预期范围':>10} {'样本':>8} {'Top1命中':>10} {'Top2命中':>10} {'实际均值':>10}")
print("-" * 55)

for low, high, label in expected_bins:
    bin_results = [r for r in results if low <= r["prediction"]["expected_total"] < high]
    if not bin_results:
        continue

    top1_hits = sum(1 for r in bin_results if r["actual_opt"] == r["prediction"]["top1"])
    top2_hits = sum(1 for r in bin_results
                    if r["actual_opt"] in [opt for opt, _ in r["prediction"]["sorted_options"][:2]])
    actual_avg = sum(r["actual_total"] for r in bin_results) / len(bin_results)

    print(f"{label:>10} {len(bin_results):>8} {top1_hits/len(bin_results)*100:>9.1f}% "
          f"{top2_hits/len(bin_results)*100:>9.1f}% {actual_avg:>9.2f}")


print("\n" + "=" * 100)
print("6. 混淆矩阵（简化版：0-1 / 2-3 / 4-5 / 6+）")
print("=" * 100)

def group_option(opt):
    if opt in ["0", "1"]:
        return "0-1"
    elif opt in ["2", "3"]:
        return "2-3"
    elif opt in ["4", "5"]:
        return "4-5"
    else:
        return "6+"

confusion = defaultdict(lambda: defaultdict(int))
for r in results:
    pred_group = group_option(r["prediction"]["top1"])
    actual_group = group_option(r["actual_opt"])
    confusion[pred_group][actual_group] += 1

print(f"\n{'':>12} {'实际0-1':>10} {'实际2-3':>10} {'实际4-5':>10} {'实际6+':>10} {'合计':>10} {'精确率':>10}")
print("-" * 75)

for pred_group in ["0-1", "2-3", "4-5", "6+"]:
    row = confusion[pred_group]
    total = sum(row.values())
    hit = row.get(pred_group, 0)
    precision = hit / total * 100 if total > 0 else 0
    print(f"预测{pred_group:>6} {row['0-1']:>10} {row['2-3']:>10} {row['4-5']:>10} {row['6+']:>10} "
          f"{total:>10} {precision:>9.1f}%")


print("\n" + "=" * 100)
print("7. 高赔选项分析（0球、1球、6球、7+球）")
print("=" * 100)

high_odds_opts = ["0", "1", "6", "7+"]
print(f"\n{'选项':>6} {'实际出现':>10} {'预测次数':>10} {'预测命中':>10} {'命中率':>10}")
print("-" * 55)

for opt in high_odds_opts:
    actual_cnt = actual_dist.get(opt, 0)
    pred_results = [r for r in results if r["prediction"]["top1"] == opt]
    hits = sum(1 for r in pred_results if r["actual_opt"] == opt) if pred_results else 0
    hit_rate = hits / len(pred_results) * 100 if pred_results else 0

    print(f"{opt:>6} {actual_cnt:>10} {len(pred_results):>10} {hits:>10} {hit_rate:>9.1f}%")


print("\n" + "=" * 100)
print("分析完成")
print("=" * 100)
