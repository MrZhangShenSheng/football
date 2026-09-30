# -*- coding: utf-8 -*-
"""
多场景模型 —— 完整准确验证框架

修复之前的问题：
1. 所有计算必须使用实际记录的赔率
2. 总进球数按竞彩8选项（0/1/2/3/4/5/6/7+）
3. 统一的滚动投注逻辑
4. 完整的月度明细和资金曲线

验证项：
1. 纯比分2串1双选
2. 纯比分2串1单选
3. 纯比分3串1双选
4. 纯总进球数单选（8选项）
5. 比分+总进球数组合

作者：sszhang
日期：2026-09-30
"""
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import product
from pathlib import Path
from typing import Dict, List, Tuple, Optional

sys.path.insert(0, str(Path("engine/scripts/research")))

import score_family_model as sfm

print("=" * 100)
print("多场景模型 —— 完整准确验证框架")
print("=" * 100)

INITIAL_CAPITAL = 1000.0
START_DATE = "2025-12-01"
UNIT = 2.0


# ============================================================
# 数据加载（完整版，包含所有赔率字段）
# ============================================================

def load_hist_complete():
    """加载历史数据，包含比分赔率和总进球数赔率"""
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

            # 比分赔率
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

            # 总进球数赔率（竞彩8选项：0/1/2/3/4/5/6/7+）
            ttg = m.get("ttg") or {}
            total_goals_odds = {}
            for k, v in ttg.items():
                try:
                    key = "7+" if str(k) in ["7+", "7"] and int(str(k).replace("+", "")) >= 7 else str(int(k))
                    total_goals_odds[key] = float(v)
                except (ValueError, TypeError):
                    if str(k) == "7+":
                        try:
                            total_goals_odds["7+"] = float(v)
                        except:
                            pass

            key = (str(m.get("date") or "")[:10], m.get("home"), m.get("away"))
            if key in seen:
                continue
            seen.add(key)

            out.append({
                "date": str(m.get("date") or "")[:10],
                "home_zh": m.get("home"),
                "away_zh": m.get("away"),
                "actual": (h, a),
                "actual_total": h + a,
                "actual_total_option": "7+" if h + a >= 7 else str(h + a),
                "score_odds": score_odds,
                "total_goals_odds": total_goals_odds,
                "has_ttg_odds": len(total_goals_odds) >= 8,
            })

    out.sort(key=lambda x: x["date"])
    return out


# ============================================================
# TeamData（完整版）
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


# ============================================================
# 比分预测器（baseline配置，经过验证）
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
    """比分预测器"""

    def __init__(self):
        self.th = THRESHOLDS
        self.calibration_strength = 0.5

    def predict(self, home: TeamData, away: TeamData) -> List[Tuple[Tuple[int, int], float]]:
        """返回按信号排序的比分列表 [((h,a), signal), ...]"""
        scenes = self._analyze_scenes(home, away)
        raw_signals = self._calc_raw_signals(scenes)
        calibrated = self._freq_calibrate(raw_signals)
        return sorted(calibrated.items(), key=lambda x: -x[1])

    def _analyze_scenes(self, home: TeamData, away: TeamData) -> Dict:
        scenes = {}
        th = self.th

        # S1: 攻防
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

        # S2: 状态
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

        # S3: 零封
        s3 = {"support": defaultdict(float)}
        if home.cs_rate > th["cs_rate_high"] and away.becs_rate > th["becs_rate_high"]:
            s3["support"]["home_clean"] = 0.4
        if away.cs_rate > th["cs_rate_high"] and home.becs_rate > th["becs_rate_high"]:
            s3["support"]["away_clean"] = 0.4
        if home.becs_rate > th["becs_rate_high"] and away.becs_rate > th["becs_rate_high"]:
            s3["support"]["low_score"] = 0.3
        scenes["S3"] = s3

        # S4: 大球
        s4 = {"support": defaultdict(float)}
        if home.over25_rate > th["over25_high"] and away.over25_rate > th["over25_high"]:
            s4["support"]["high_score"] = 0.4
        if home.btts_rate > th["btts_high"] and away.btts_rate > th["btts_high"]:
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

    def _freq_calibrate(self, raw: Dict[Tuple, float]) -> Dict[Tuple, float]:
        calibrated = {}
        for score in HIST_FREQ:
            raw_sig = raw.get(score, 0)
            hist = HIST_FREQ[score]
            calibrated[score] = (1 - self.calibration_strength) * raw_sig + self.calibration_strength * hist
        return calibrated


# ============================================================
# 总进球数预测器（竞彩8选项）
# ============================================================

class TotalGoalsPredictor:
    """总进球数预测器（竞彩8选项：0/1/2/3/4/5/6/7+）"""

    def __init__(self):
        # 历史分布
        self.hist_dist = {
            "0": 0.063, "1": 0.155, "2": 0.230, "3": 0.227,
            "4": 0.154, "5": 0.105, "6": 0.037, "7+": 0.029
        }

    def predict(self, home: TeamData, away: TeamData) -> List[Tuple[str, float]]:
        """返回按概率排序的总进球数选项 [("2", 0.23), ("3", 0.22), ...]"""

        # 预期进球数
        expected_home = (home.gf_home_avg + away.ga_away_avg) / 2
        expected_away = (away.gf_away_avg + home.ga_home_avg) / 2
        expected_total = expected_home + expected_away

        # 泊松分布计算
        probs = {}
        for k in range(8):
            # P(X=k) = e^(-λ) * λ^k / k!
            prob = math.exp(-expected_total) * (expected_total ** k) / math.factorial(k)
            if k < 7:
                probs[str(k)] = prob
            else:
                # 7+: 1 - sum(P(0..6))
                pass
        probs["7+"] = max(0, 1 - sum(probs.values()))

        # 与历史分布混合（30%历史）
        calibrated = {}
        for opt in ["0", "1", "2", "3", "4", "5", "6", "7+"]:
            raw = probs.get(opt, 0)
            hist = self.hist_dist.get(opt, 0)
            calibrated[opt] = 0.7 * raw + 0.3 * hist

        # 归一化
        total = sum(calibrated.values())
        for opt in calibrated:
            calibrated[opt] /= total

        return sorted(calibrated.items(), key=lambda x: -x[1])


# ============================================================
# 加载数据
# ============================================================

zh = {}
for tid, srcs in sfm.load_aliases().items():
    if srcs.get("zh"):
        zh[srcs["zh"]] = tid

tl = sfm.league_timeline()
hist = load_hist_complete()

has_ttg = sum(1 for m in hist if m["has_ttg_odds"])
print(f"数据加载完成：")
print(f"  历史比赛：{len(hist)} 场")
print(f"  联赛库：{len(tl)} 场")
print(f"  有总进球数赔率：{has_ttg} 场")


# ============================================================
# 构建预测包
# ============================================================

# 构建盲测数据
blind = []
for m in hist:
    if m["date"] < START_DATE:
        continue
    hid, aid = zh.get(m["home_zh"]), zh.get(m["away_zh"])
    if hid and aid:
        blind.append({**m, "hid": hid, "aid": aid})

# 合并时间线
merged = [("L", d, h, a, hg, ag, None) for d, h, a, hg, ag in tl]
merged += [("B", m["date"], m["hid"], m["aid"], m["actual"][0], m["actual"][1], i)
           for i, m in enumerate(blind)]
merged.sort(key=lambda r: (r[1], 0 if r[0] == "L" else 1))

# 滚动统计
stats = defaultdict(sfm.TeamStats)
match_packs = []

for r in merged:
    kind, date, h, a, hg, ag = r[0], r[1], r[2], r[3], r[4], r[5]

    if kind == "B":
        idx = r[6]
        m = blind[idx]

        if stats[h].n < sfm.MIN_HIST or stats[a].n < sfm.MIN_HIST:
            continue

        match_packs.append({
            "date": m["date"],
            "actual": m["actual"],
            "actual_total": m["actual_total"],
            "actual_total_option": m["actual_total_option"],
            "score_odds": m["score_odds"],
            "total_goals_odds": m["total_goals_odds"],
            "has_ttg_odds": m["has_ttg_odds"],
            "home_data": ts_to_td(stats[h]),
            "away_data": ts_to_td(stats[a]),
            "home_zh": m["home_zh"],
            "away_zh": m["away_zh"],
        })

    if kind == "L":
        stats[h].add(hg, ag, True, a)
        stats[a].add(ag, hg, False, h)

# 按日期分组
by_day = defaultdict(list)
for p in match_packs:
    by_day[p["date"]].append(p)

print(f"  盲测样本（>={START_DATE}）：{len(blind)} 场")
print(f"  有效预测包：{len(match_packs)} 场")
print(f"  跨越天数：{len(by_day)} 天")


# ============================================================
# 验证函数
# ============================================================

def run_score_parlay(by_day, n_legs: int, k_picks: int, predictor: ScorePredictor) -> Dict:
    """
    比分串关投注模拟
    n_legs: 串关腿数（2/3）
    k_picks: 每场选几个比分（1=单选，2=双选）
    """
    capital = INITIAL_CAPITAL
    monthly = defaultdict(lambda: {"cost": 0, "payout": 0, "hits": 0, "tickets": 0})

    total_cost = 0
    total_payout = 0
    n_tickets = 0
    n_hits = 0
    hit_details = []
    capital_history = [(START_DATE, capital)]
    min_capital = capital

    for day in sorted(by_day):
        day_packs = by_day[day]
        month = day[:7]

        if len(day_packs) < n_legs:
            continue

        # 预测
        for p in day_packs:
            p["score_pred"] = predictor.predict(p["home_data"], p["away_data"])
            p["max_signal"] = p["score_pred"][0][1] if p["score_pred"] else 0

        # 选场
        selected = sorted(day_packs, key=lambda p: -p["max_signal"])[:n_legs]

        # 每场选比分
        all_picks = []
        for p in selected:
            picks = []
            for score, sig in p["score_pred"][:k_picks]:
                # 使用实际记录的赔率
                odds = p["score_odds"].get(score, 0)
                if odds > 0:
                    picks.append((score, odds, p["actual"]))
            if not picks:
                break
            all_picks.append(picks)

        if len(all_picks) < n_legs:
            continue

        # 生成所有投注组合
        all_bets = list(product(*all_picks))
        cost = len(all_bets) * UNIT

        # 检查资金
        if capital < cost:
            continue

        capital -= cost
        total_cost += cost
        monthly[month]["cost"] += cost
        monthly[month]["tickets"] += 1
        n_tickets += 1

        # 计算派彩
        payout = 0.0
        hit_combo = None
        for combo in all_bets:
            all_correct = all(score == actual for score, odds, actual in combo)
            if all_correct:
                combo_odds = 1.0
                for score, odds, actual in combo:
                    combo_odds *= odds
                payout += UNIT * combo_odds
                hit_combo = combo

        capital += payout
        total_payout += payout
        monthly[month]["payout"] += payout

        if payout > 0:
            n_hits += 1
            monthly[month]["hits"] += 1
            hit_details.append({
                "date": day,
                "cost": cost,
                "payout": payout,
                "combo": hit_combo,
            })

        capital_history.append((day, capital))
        min_capital = min(min_capital, capital)

    max_capital = max(c for _, c in capital_history)
    max_drawdown = (max_capital - min_capital) / max_capital if max_capital > 0 else 0

    return {
        "final_capital": capital,
        "total_cost": total_cost,
        "total_payout": total_payout,
        "profit": total_payout - total_cost,
        "roi": (total_payout - total_cost) / total_cost if total_cost > 0 else 0,
        "n_tickets": n_tickets,
        "n_hits": n_hits,
        "hit_rate": n_hits / n_tickets if n_tickets > 0 else 0,
        "monthly": dict(monthly),
        "hit_details": hit_details,
        "min_capital": min_capital,
        "max_drawdown": max_drawdown,
    }


def run_total_goals_single(by_day, predictor: TotalGoalsPredictor) -> Dict:
    """
    总进球数单选投注模拟（每场1注，选Top1）
    """
    capital = INITIAL_CAPITAL
    monthly = defaultdict(lambda: {"cost": 0, "payout": 0, "hits": 0, "bets": 0})

    total_cost = 0
    total_payout = 0
    n_bets = 0
    n_hits = 0

    for day in sorted(by_day):
        for p in by_day[day]:
            month = day[:7]

            # 需要有总进球数赔率
            if not p["has_ttg_odds"]:
                continue

            # 预测
            pred = predictor.predict(p["home_data"], p["away_data"])
            top1_option = pred[0][0]  # 如 "2"

            # 获取实际赔率
            odds = p["total_goals_odds"].get(top1_option, 0)
            if odds <= 0:
                continue

            cost = UNIT
            if capital < cost:
                continue

            capital -= cost
            total_cost += cost
            monthly[month]["cost"] += cost
            monthly[month]["bets"] += 1
            n_bets += 1

            # 检查命中
            actual_option = p["actual_total_option"]
            if actual_option == top1_option:
                payout = UNIT * odds
                capital += payout
                total_payout += payout
                monthly[month]["payout"] += payout
                monthly[month]["hits"] += 1
                n_hits += 1

    return {
        "final_capital": capital,
        "total_cost": total_cost,
        "total_payout": total_payout,
        "profit": total_payout - total_cost,
        "roi": (total_payout - total_cost) / total_cost if total_cost > 0 else 0,
        "n_bets": n_bets,
        "n_hits": n_hits,
        "hit_rate": n_hits / n_bets if n_bets > 0 else 0,
        "monthly": dict(monthly),
    }


# ============================================================
# 运行所有验证
# ============================================================

score_predictor = ScorePredictor()
ttg_predictor = TotalGoalsPredictor()

print("\n" + "=" * 100)
print("开始完整验证...")
print("=" * 100)

results = {}

# 1. 纯比分2串1双选
print("\n【1】纯比分2串1双选...")
results["score_2x1_double"] = run_score_parlay(by_day, n_legs=2, k_picks=2, predictor=score_predictor)

# 2. 纯比分2串1单选
print("【2】纯比分2串1单选...")
results["score_2x1_single"] = run_score_parlay(by_day, n_legs=2, k_picks=1, predictor=score_predictor)

# 3. 纯比分3串1双选
print("【3】纯比分3串1双选...")
results["score_3x1_double"] = run_score_parlay(by_day, n_legs=3, k_picks=2, predictor=score_predictor)

# 4. 纯总进球数单选
print("【4】纯总进球数单选（竞彩8选项）...")
results["ttg_single"] = run_total_goals_single(by_day, predictor=ttg_predictor)

print("\n验证完成！")


# ============================================================
# 输出结果
# ============================================================

print("\n" + "=" * 100)
print("完整验证结果汇总（2025-12-01 起始资金1000元）")
print("=" * 100)

print(f"\n{'方案':<25} {'最终资金':>12} {'收益率':>10} {'命中':>15} {'成本':>10} {'派彩':>12} {'回撤':>10}")
print("-" * 100)

for name, r in results.items():
    if "n_tickets" in r:
        hit_str = f"{r['n_hits']}/{r['n_tickets']}({r['hit_rate']*100:.1f}%)"
    else:
        hit_str = f"{r['n_hits']}/{r['n_bets']}({r['hit_rate']*100:.1f}%)"

    drawdown = r.get('max_drawdown', 0)

    print(f"{name:<25} {r['final_capital']:>12.1f} {r['roi']*100:>+9.1f}% {hit_str:>15} {r['total_cost']:>10.0f} {r['total_payout']:>12.1f} {drawdown*100:>9.1f}%")


# 月度明细
print("\n" + "=" * 100)
print("月度明细")
print("=" * 100)

for name in ["score_2x1_double", "ttg_single"]:
    r = results[name]
    print(f"\n【{name}】")
    print(f"{'月份':<10} {'成本':>10} {'派彩':>12} {'盈亏':>12} {'命中':>8}")
    print("-" * 55)

    for month in sorted(r["monthly"].keys()):
        m = r["monthly"][month]
        profit = m["payout"] - m["cost"]
        hits = m.get("hits", 0)
        print(f"{month:<10} {m['cost']:>10.0f} {m['payout']:>12.1f} {profit:>+12.1f} {hits:>8}")


# 命中明细（比分）
print("\n" + "=" * 100)
print("比分2串1双选命中明细")
print("=" * 100)

r = results["score_2x1_double"]
print(f"\n共命中 {r['n_hits']} 票：")
for h in r["hit_details"]:
    scores = [f"{s[0]}" for s in h["combo"]]
    print(f"  {h['date']}: 成本{h['cost']:.0f} 派彩{h['payout']:.1f} 比分{scores}")
