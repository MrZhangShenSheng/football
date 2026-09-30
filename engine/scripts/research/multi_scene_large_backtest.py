# -*- coding: utf-8 -*-
"""多场景模型 —— 大规模历史数据回测"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path("engine/scripts/research")))

from multi_scene_model import (
    MultiScenePredictor, TeamData, SCORE_GROUPS
)
import score_family_model as sfm

UNIT = 2.0

print("=" * 100)
print("多场景博弈模型 —— 大规模历史数据回测")
print("=" * 100)


def team_stats_to_team_data(ts) -> TeamData:
    """将 TeamStats 转换为 TeamData"""
    return TeamData(
        n=ts.n,
        gf=ts.gf,
        ga=ts.ga,
        win=ts.win,
        gd=ts.gd,
        cs=ts.cs,
        becs=ts.becs,
        btts=ts.btts,
        over25=ts.over25,
        gf_home=ts.gf_side[0][0],
        gf_home_n=ts.gf_side[0][1],
        gf_away=ts.gf_side[1][0],
        gf_away_n=ts.gf_side[1][1],
        ga_home=ts.ga_side[0][0],
        ga_home_n=ts.ga_side[0][1],
        ga_away=ts.ga_side[1][0],
        ga_away_n=ts.ga_side[1][1],
        recent_gd=ts.recent_gd.copy(),
        recent=ts.recent.copy(),
    )


def load_hist_full():
    """加载历史数据（去重版）"""
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
                "home_zh": m.get("home"),
                "away_zh": m.get("away"),
                "actual": (h, a),
                "odds": odds,
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

print(f"历史比赛数据：{len(hist)} 场")
print(f"联赛库数据：{len(tl)} 场")
print(f"日期范围：{hist[0]['date']} ~ {hist[-1]['date']}")


def run_backtest(cut_date):
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

    results = []

    for r in merged:
        kind, date, h, a, hg, ag = r[0], r[1], r[2], r[3], r[4], r[5]

        if kind == "B":
            idx = r[6]
            m = blind[idx]

            # 检查历史数据量
            if stats[h].n < sfm.MIN_HIST or stats[a].n < sfm.MIN_HIST:
                continue

            # 转换为 TeamData
            home_data = team_stats_to_team_data(stats[h])
            away_data = team_stats_to_team_data(stats[a])

            # 预测
            prediction = predictor.predict(home_data, away_data)

            results.append({
                "date": m["date"],
                "actual": m["actual"],
                "odds": m["odds"],
                "prediction": prediction,
            })

        # 更新统计（联赛数据才更新）
        if kind == "L":
            stats[h].add(hg, ag, True, a)
            stats[a].add(ag, hg, False, h)

    return results


def analyze_results(results):
    """分析回测结果"""
    if not results:
        return None

    # Top-K 命中率
    top_k_hits = {1: 0, 2: 0, 3: 0, 5: 0}
    for r in results:
        pred = r["prediction"]["predictions"]
        sorted_scores = sorted(pred.items(), key=lambda x: -x[1]["signal"])
        for k in top_k_hits.keys():
            top_k_scores = [s for s, _ in sorted_scores[:k]]
            if r["actual"] in top_k_scores:
                top_k_hits[k] += 1

    # 0:0 分析
    zero_zero_high_signal = []  # 信号 > 0.3
    zero_zero_all = []
    for r in results:
        sig = r["prediction"]["predictions"].get((0, 0), {}).get("signal", 0)
        is_hit = r["actual"] == (0, 0)
        odds = r["odds"].get((0, 0), 0)
        zero_zero_all.append((sig, is_hit, odds))
        if sig > 0.3:
            zero_zero_high_signal.append((sig, is_hit, odds))

    # 4+球分析
    four_plus_high_signal = []
    four_plus_all = []
    for r in results:
        sig = r["prediction"]["total_goals"].get("4+", {}).get("signal", 0)
        actual_total = r["actual"][0] + r["actual"][1]
        is_hit = actual_total >= 4
        four_plus_all.append((sig, is_hit))
        if sig > 0.3:
            four_plus_high_signal.append((sig, is_hit))

    return {
        "n": len(results),
        "top1": top_k_hits[1] / len(results),
        "top3": top_k_hits[3] / len(results),
        "top5": top_k_hits[5] / len(results),
        "zero_zero_rate": sum(1 for _, hit, _ in zero_zero_all if hit) / len(zero_zero_all),
        "zero_zero_high_n": len(zero_zero_high_signal),
        "zero_zero_high_hit": sum(1 for _, hit, _ in zero_zero_high_signal if hit) if zero_zero_high_signal else 0,
        "four_plus_rate": sum(1 for _, hit in four_plus_all if hit) / len(four_plus_all),
        "four_plus_high_n": len(four_plus_high_signal),
        "four_plus_high_hit": sum(1 for _, hit in four_plus_high_signal if hit) if four_plus_high_signal else 0,
    }


# ============================================================
# 多切分点回测
# ============================================================

cuts = [
    "2025-07-01",
    "2025-10-01",
    "2026-01-01",
    "2026-04-01",
    "2026-07-01",
]

print("\n" + "=" * 100)
print("多切分点回测结果")
print("=" * 100)

print(f"\n{'切分点':<12} {'样本数':>8} {'Top1':>8} {'Top3':>8} {'Top5':>8} | {'0:0率':>8} {'0:0高信号':>12} | {'4+球率':>8} {'4+高信号':>12}")
print("-" * 120)

all_results = []
for cut in cuts:
    results = run_backtest(cut)
    if results is None:
        print(f"{cut:<12} 样本不足")
        continue

    stats = analyze_results(results)
    all_results.append((cut, results, stats))

    zero_high_str = f"{stats['zero_zero_high_hit']}/{stats['zero_zero_high_n']}" if stats['zero_zero_high_n'] > 0 else "N/A"
    four_high_str = f"{stats['four_plus_high_hit']}/{stats['four_plus_high_n']}" if stats['four_plus_high_n'] > 0 else "N/A"

    print(f"{cut:<12} {stats['n']:>8} {stats['top1']*100:>7.1f}% {stats['top3']*100:>7.1f}% {stats['top5']*100:>7.1f}% | "
          f"{stats['zero_zero_rate']*100:>7.1f}% {zero_high_str:>12} | "
          f"{stats['four_plus_rate']*100:>7.1f}% {four_high_str:>12}")

print("-" * 120)


# ============================================================
# 汇总分析
# ============================================================

print("\n" + "=" * 100)
print("汇总统计")
print("=" * 100)

if all_results:
    total_n = sum(s["n"] for _, _, s in all_results)
    avg_top1 = sum(s["top1"] * s["n"] for _, _, s in all_results) / total_n
    avg_top3 = sum(s["top3"] * s["n"] for _, _, s in all_results) / total_n
    avg_top5 = sum(s["top5"] * s["n"] for _, _, s in all_results) / total_n

    print(f"\n总样本数：{total_n}")
    print(f"平均 Top1 命中率：{avg_top1*100:.1f}%")
    print(f"平均 Top3 命中率：{avg_top3*100:.1f}%")
    print(f"平均 Top5 命中率：{avg_top5*100:.1f}%")


# ============================================================
# 详细分析最大样本切分点
# ============================================================

if all_results:
    # 取最大样本的切分点做详细分析
    cut, results, stats = max(all_results, key=lambda x: x[2]["n"])

    print(f"\n" + "=" * 100)
    print(f"详细分析（切分点={cut}，样本={stats['n']}）")
    print("=" * 100)

    # 按比分组统计
    print(f"\n【按比分组统计】")
    print(f"{'比分组':<15} {'实际出现':>10} {'高信号场次':>12} {'高信号命中':>12} {'命中率':>10}")
    print("-" * 65)

    for group_name, scores in SCORE_GROUPS.items():
        # 该组实际出现次数
        actual_cnt = sum(1 for r in results if r["actual"] in scores)

        # 高信号场次
        high_signal_results = []
        for r in results:
            max_sig = max(r["prediction"]["predictions"].get(s, {}).get("signal", 0) for s in scores)
            if max_sig > 0.3:
                is_hit = r["actual"] in scores
                high_signal_results.append((max_sig, is_hit))

        high_n = len(high_signal_results)
        high_hit = sum(1 for _, hit in high_signal_results if hit)
        hit_rate = high_hit / high_n * 100 if high_n > 0 else 0

        print(f"{group_name:<15} {actual_cnt:>10} {high_n:>12} {high_hit:>12} {hit_rate:>9.1f}%")

    # 场景判断分布
    print(f"\n【场景判断分布】")
    scene_judgments = defaultdict(Counter)
    for r in results:
        for scene_id, detail in r["prediction"]["scene_details"].items():
            scene_judgments[scene_id][detail["judgment"]] += 1

    for scene_id in sorted(scene_judgments.keys()):
        print(f"\n{scene_id}:")
        total = sum(scene_judgments[scene_id].values())
        for judgment, cnt in scene_judgments[scene_id].most_common(5):
            print(f"  {judgment}: {cnt} ({cnt/total*100:.1f}%)")

    # 信号强度分布
    print(f"\n【信号强度分布】")
    all_signals = []
    for r in results:
        for score, info in r["prediction"]["predictions"].items():
            all_signals.append(info["signal"])

    print(f"信号范围：{min(all_signals):.3f} ~ {max(all_signals):.3f}")
    print(f"信号均值：{sum(all_signals)/len(all_signals):.3f}")

    signal_bins = [(-1, 0, "<0"), (0, 0.1, "0-0.1"), (0.1, 0.2, "0.1-0.2"),
                   (0.2, 0.3, "0.2-0.3"), (0.3, 0.5, "0.3-0.5"), (0.5, 1, ">0.5")]
    for low, high, label in signal_bins:
        cnt = sum(1 for s in all_signals if low <= s < high)
        print(f"  {label}: {cnt} ({cnt/len(all_signals)*100:.1f}%)")
