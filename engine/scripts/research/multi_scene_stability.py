# -*- coding: utf-8 -*-
"""多场景模型 —— 多切分点稳定性验证"""
import json
import sys
from collections import Counter, defaultdict
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path("engine/scripts/research")))

from multi_scene_model import MultiScenePredictor, TeamData
import score_family_model as sfm

UNIT = 2.0

print("=" * 100)
print("多场景模型 —— 多切分点稳定性验证")
print("=" * 100)


def team_stats_to_team_data(ts) -> TeamData:
    return TeamData(
        n=ts.n, gf=ts.gf, ga=ts.ga, win=ts.win, gd=ts.gd,
        cs=ts.cs, becs=ts.becs, btts=ts.btts, over25=ts.over25,
        gf_home=ts.gf_side[0][0], gf_home_n=ts.gf_side[0][1],
        gf_away=ts.gf_side[1][0], gf_away_n=ts.gf_side[1][1],
        ga_home=ts.ga_side[0][0], ga_home_n=ts.ga_side[0][1],
        ga_away=ts.ga_side[1][0], ga_away_n=ts.ga_side[1][1],
        recent_gd=ts.recent_gd.copy(), recent=ts.recent.copy(),
    )


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


# 加载数据
zh = {}
for tid, srcs in sfm.load_aliases().items():
    if srcs.get("zh"):
        zh[srcs["zh"]] = tid

tl = sfm.league_timeline()
hist = load_hist_full()

print(f"历史数据：{len(hist)} 场")
print(f"联赛库：{len(tl)} 场")


def run_backtest_for_cut(cut_date):
    """运行单个切分点的回测"""
    # 构建盲测数据
    blind = []
    for m in hist:
        if m["date"] < cut_date:
            continue
        hid, aid = zh.get(m["home_zh"]), zh.get(m["away_zh"])
        if hid and aid:
            blind.append({**m, "hid": hid, "aid": aid})

    if len(blind) < 100:
        return None

    # 合并时间线
    merged = [("L", d, h, a, hg, ag, None) for d, h, a, hg, ag in tl]
    merged += [("B", m["date"], m["hid"], m["aid"], m["actual"][0], m["actual"][1], i)
               for i, m in enumerate(blind)]
    merged.sort(key=lambda r: (r[1], 0 if r[0] == "L" else 1))

    # 滚动统计
    stats = defaultdict(sfm.TeamStats)
    predictor = MultiScenePredictor()
    match_packs = []

    for r in merged:
        kind, date, h, a, hg, ag = r[0], r[1], r[2], r[3], r[4], r[5]

        if kind == "B":
            idx = r[6]
            m = blind[idx]

            if stats[h].n < sfm.MIN_HIST or stats[a].n < sfm.MIN_HIST:
                continue

            home_data = team_stats_to_team_data(stats[h])
            away_data = team_stats_to_team_data(stats[a])
            prediction = predictor.predict(home_data, away_data)

            pred = prediction["predictions"]
            sorted_scores = sorted(pred.items(), key=lambda x: -x[1]["signal"])
            max_signal = sorted_scores[0][1]["signal"] if sorted_scores else 0

            match_packs.append({
                "date": m["date"],
                "actual": m["actual"],
                "odds": m["odds"],
                "sorted_scores": sorted_scores,
                "max_signal": max_signal,
            })

        if kind == "L":
            stats[h].add(hg, ag, True, a)
            stats[a].add(ag, hg, False, h)

    # 按日期分组
    by_day = defaultdict(list)
    for p in match_packs:
        by_day[p["date"]].append(p)

    # 模拟串关
    results = {}

    for strategy_name, n_legs, k_picks in [
        ("2串1-单选", 2, 1),
        ("2串1-双选", 2, 2),
        ("3串1-双选", 3, 2),
    ]:
        total_cost = 0.0
        total_payout = 0.0
        n_tickets = 0
        n_hits = 0

        for day in sorted(by_day):
            day_packs = by_day[day]
            if len(day_packs) < n_legs:
                continue

            selected = sorted(day_packs, key=lambda p: -p["max_signal"])[:n_legs]

            all_picks = []
            for p in selected:
                picks = []
                for score, info in p["sorted_scores"][:k_picks]:
                    odds = p["odds"].get(score, 999)
                    picks.append((score, odds, p["actual"]))
                all_picks.append(picks)

            all_bets = list(product(*all_picks))
            cost = len(all_bets) * UNIT
            payout = 0.0

            for combo in all_bets:
                all_correct = all(score == actual for score, odds, actual in combo)
                if all_correct:
                    combo_odds = 1.0
                    for score, odds, actual in combo:
                        combo_odds *= odds
                    payout += UNIT * combo_odds

            total_cost += cost
            total_payout += payout
            n_tickets += 1
            if payout > 0:
                n_hits += 1

        profit = (total_payout - total_cost) / total_cost if total_cost > 0 else -1
        results[strategy_name] = {
            "n_tickets": n_tickets,
            "n_hits": n_hits,
            "total_cost": total_cost,
            "total_payout": total_payout,
            "profit": profit,
        }

    return {
        "n_blind": len(blind),
        "n_packs": len(match_packs),
        "results": results,
    }


# 同时跑族模型基准
def run_baseline_for_cut(cut_date, gap_threshold=0.05):
    """族模型基准回测"""
    blind = []
    for m in hist:
        if m["date"] < cut_date:
            continue
        hid, aid = zh.get(m["home_zh"]), zh.get(m["away_zh"])
        if hid and aid:
            blind.append({**m, "hid": hid, "aid": aid})

    if len(blind) < 100:
        return None

    merged = [("L", d, h, a, hg, ag, None) for d, h, a, hg, ag in tl]
    merged += [("B", m["date"], m["hid"], m["aid"], m["actual"][0], m["actual"][1], i)
               for i, m in enumerate(blind)]
    merged.sort(key=lambda r: (r[1], 0 if r[0] == "L" else 1))

    stats = defaultdict(sfm.TeamStats)
    X_tr, y_tr, X_bl, meta = [], [], [], []
    tot_g = tot_n = 0

    for r in merged:
        kind, date, h, a, hg, ag = r[0], r[1], r[2], r[3], r[4], r[5]
        lg_gf = (tot_g / tot_n) if tot_n >= 50 else 2.6
        fv_h = stats[h].vector(0, lg_gf)
        fv_a = stats[a].vector(1, lg_gf)

        if kind == "L" and date < cut_date and fv_h[12] >= sfm.MIN_HIST and fv_a[12] >= sfm.MIN_HIST:
            X_tr.append(sfm.feature_row((fv_h, fv_a)))
            y_tr.append(sfm.CLASSES.index(sfm.family_of(hg, ag)))
        elif kind == "B":
            meta.append(blind[r[6]])
            X_bl.append(sfm.feature_row((fv_h, fv_a)))

        if kind == "L":
            stats[h].add(hg, ag, True)
            stats[a].add(ag, hg, False)
            tot_g += hg + ag
            tot_n += 1

    if len(X_tr) < 100 or len(X_bl) < 50:
        return None

    import numpy as np
    model = sfm.train_softmax(np.array(X_tr), np.array(y_tr), len(sfm.CLASSES))
    P = sfm.predict_proba(model, np.array(X_bl))

    fam_dist = defaultdict(Counter)
    for r in merged:
        if r[0] == "L" and r[1] < cut_date:
            fam_dist[sfm.family_of(r[4], r[5])][(r[4], r[5])] += 1

    packs = []
    for i, m in enumerate(meta):
        model_probs = {}
        for ci, cname in enumerate(sfm.CLASSES):
            tot = sum(fam_dist.get(cname, {}).values()) or 1
            for s, c in fam_dist.get(cname, {}).items():
                model_probs[s] = model_probs.get(s, 0.0) + P[i][ci] * (c / tot)
        mdl_sorted = sorted(model_probs.items(), key=lambda kv: -kv[1])
        gap = mdl_sorted[0][1] - mdl_sorted[1][1] if len(mdl_sorted) >= 2 else 0
        packs.append({
            "date": m["date"], "actual": m["actual"], "odds": m["odds"],
            "model_sorted": mdl_sorted, "gap": gap,
        })

    by_day = defaultdict(list)
    for p in packs:
        by_day[p["date"]].append(p)

    total_cost = 0.0
    total_payout = 0.0
    n_tickets = 0
    n_hits = 0

    for day in sorted(by_day):
        day_packs = by_day[day]
        if len(day_packs) < 2:
            continue

        selected = sorted(day_packs, key=lambda p: -p["gap"])[:2]
        p1, p2 = selected[0], selected[1]

        k1 = 1 if p1["gap"] > gap_threshold else 2
        k2 = 1 if p2["gap"] > gap_threshold else 2

        picks1 = [(s, p1["odds"].get(s, 999)) for s, prob in p1["model_sorted"][:k1]]
        picks2 = [(s, p2["odds"].get(s, 999)) for s, prob in p2["model_sorted"][:k2]]

        all_bets = list(product(picks1, picks2))
        cost = len(all_bets) * UNIT
        payout = 0.0

        for (s1, o1), (s2, o2) in all_bets:
            if s1 == p1["actual"] and s2 == p2["actual"]:
                payout += UNIT * o1 * o2

        total_cost += cost
        total_payout += payout
        n_tickets += 1
        if payout > 0:
            n_hits += 1

    profit = (total_payout - total_cost) / total_cost if total_cost > 0 else -1
    return {
        "n_tickets": n_tickets,
        "n_hits": n_hits,
        "total_cost": total_cost,
        "total_payout": total_payout,
        "profit": profit,
    }


# ============================================================
# 多切分点测试
# ============================================================

cuts = [
    "2025-12-01",
    "2026-01-01",
    "2026-02-01",
    "2026-03-01",
    "2026-04-01",
    "2026-05-01",
    "2026-06-01",
    "2026-07-01",
]

print(f"\n{'切分点':<12} {'盲测':>6} | {'族模型基准':>15} | {'多场景2串1单':>15} | {'多场景2串1双':>15} | {'多场景3串1双':>15}")
print("-" * 105)

all_results = {
    "baseline": [],
    "ms_2x1_single": [],
    "ms_2x1_double": [],
    "ms_3x1_double": [],
}

for cut in cuts:
    # 多场景模型
    ms_result = run_backtest_for_cut(cut)
    if ms_result is None:
        continue

    # 族模型基准
    baseline = run_baseline_for_cut(cut)

    n_blind = ms_result["n_blind"]

    # 族模型
    if baseline:
        b_str = f"{baseline['profit']*100:>+6.1f}%({baseline['n_hits']:>2})"
        mark_b = "✅" if baseline['profit'] > 0 else ""
        all_results["baseline"].append(baseline)
    else:
        b_str = "N/A"
        mark_b = ""

    # 多场景结果
    ms_2x1_s = ms_result["results"].get("2串1-单选", {})
    ms_2x1_d = ms_result["results"].get("2串1-双选", {})
    ms_3x1_d = ms_result["results"].get("3串1-双选", {})

    def fmt(r):
        if not r:
            return "N/A", ""
        profit = r.get("profit", -1)
        hits = r.get("n_hits", 0)
        mark = "✅" if profit > 0 else ""
        return f"{profit*100:>+6.1f}%({hits:>2})", mark

    s_2x1_s, m_2x1_s = fmt(ms_2x1_s)
    s_2x1_d, m_2x1_d = fmt(ms_2x1_d)
    s_3x1_d, m_3x1_d = fmt(ms_3x1_d)

    if ms_2x1_s: all_results["ms_2x1_single"].append(ms_2x1_s)
    if ms_2x1_d: all_results["ms_2x1_double"].append(ms_2x1_d)
    if ms_3x1_d: all_results["ms_3x1_double"].append(ms_3x1_d)

    print(f"{cut:<12} {n_blind:>6} | {b_str:>12}{mark_b:<2} | {s_2x1_s:>12}{m_2x1_s:<2} | {s_2x1_d:>12}{m_2x1_d:<2} | {s_3x1_d:>12}{m_3x1_d:<2}")

print("-" * 105)


# ============================================================
# 汇总统计
# ============================================================

print(f"\n{'='*100}")
print("汇总统计")
print("=" * 100)

def summarize(name, results_list):
    if not results_list:
        return
    n_positive = sum(1 for r in results_list if r.get("profit", -1) > 0)
    total_cost = sum(r.get("total_cost", 0) for r in results_list)
    total_payout = sum(r.get("total_payout", 0) for r in results_list)
    total_hits = sum(r.get("n_hits", 0) for r in results_list)
    total_tickets = sum(r.get("n_tickets", 0) for r in results_list)
    total_profit = (total_payout - total_cost) / total_cost if total_cost > 0 else 0

    mark = "✅" if n_positive == len(results_list) else ""
    print(f"{name:<20} 正收益:{n_positive:>2}/{len(results_list):<2}{mark}  总票:{total_tickets:>5}  命中:{total_hits:>3}  成本:{total_cost:>8.0f}  派彩:{total_payout:>8.0f}  盈利率:{total_profit*100:>+7.2f}%")

summarize("族模型基准", all_results["baseline"])
summarize("多场景2串1单选", all_results["ms_2x1_single"])
summarize("多场景2串1双选", all_results["ms_2x1_double"])
summarize("多场景3串1双选", all_results["ms_3x1_double"])
