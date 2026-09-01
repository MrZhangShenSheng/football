#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""研究：胜平负（市场信号强）vs 比分/进球（独立建模）逻辑分化的数据底子。

A. fd 收盘价 vs 胜平负实际：市场倾向命中率 + 分档校准（验证"赔率大部分体现趋势"）
B. 比分/进球命中率：corpus 方案级 directionHit/scoreHit + backtest DC 比分矩阵 TOP-k
C. 进球影响因素盘点：归因因子分布 + 联赛画像进球结构
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "engine" / "scripts"))
from dc_predict import devig  # noqa: E402

CACHE = ROOT / "engine" / "cache"
SUMM = ROOT / "data" / "04-summaries"

FD_LEAGUES = [
    "england-premier", "england-championship", "spain-laliga", "germany-bundesliga",
    "germany-bundesliga2", "italy-serie-a", "italy-serie-b", "france-ligue1",
    "france-ligue2", "netherlands-eredivisie", "portugal-primeira",
    "belgium-first-a", "turkey-super-lig", "greece-super",
]


def part_a_market_vs_had():
    print("=" * 92)
    print("A. Pinnacle 收盘价 vs 胜平负实际（fd 全联赛 2526+2627）")
    print("=" * 92)
    rows = []
    for lg in FD_LEAGUES:
        for season in ("2526", "2627"):
            p = CACHE / f"odds_{lg}_{season}.json"
            if not p.exists():
                continue
            data = json.loads(p.read_text(encoding="utf-8"))
            for m in data.get("matches", []):
                if m.get("fthg") is None or not m.get("pin_h"):
                    continue
                try:
                    odds = (float(m["pin_h"]), float(m["pin_d"]), float(m["pin_a"]))
                    hg, ag = int(m["fthg"]), int(m["ftag"])
                except (ValueError, TypeError):
                    continue
                probs = devig(odds)
                rows.append({"lg": lg, "probs": probs, "outcome": 0 if hg > ag else (1 if hg == ag else 2)})

    n = len(rows)
    pick_hit = sum(max(range(3), key=lambda i: r["probs"][i]) == r["outcome"] for r in rows)
    print(f"\n样本 {n} 场 | 市场倾向（收盘最低赔项）命中 {pick_hit} 场 = {pick_hit / n:.1%}")

    # 分档校准：去水隐含概率档 vs 实际频率（逐腿）
    bins = [(0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01)]
    print("\n校准表（腿级，隐含概率档 → 实际命中频率）：")
    print(f"{'档位':<12}{'腿数':>6}{'隐含均值':>10}{'实际频率':>10}{'偏差':>8}")
    for lo, hi in bins:
        legs = [(r["probs"][i], i == r["outcome"]) for r in rows for i in range(3) if lo <= r["probs"][i] < hi]
        if not legs:
            continue
        imp = sum(p for p, _ in legs) / len(legs)
        act = sum(h for _, h in legs) / len(legs)
        print(f"{lo:.0%}~{min(hi,1):.0%}{'':<4}{len(legs):>6}{imp:>10.1%}{act:>10.1%}{act - imp:>+8.1%}")

    # 强信号场：市场隐含最高档
    strong = [r for r in rows if max(r["probs"]) >= 0.7]
    s_hit = sum(max(range(3), key=lambda i: r["probs"][i]) == r["outcome"] for r in strong)
    print(f"\n强信号场（隐含≥70%）: {len(strong)} 场，命中 {s_hit} = {s_hit / len(strong):.1%}" if strong else "无强信号场")
    vs = [r for r in rows if 0.4 <= max(r["probs"]) < 0.7]
    v_hit = sum(max(range(3), key=lambda i: r["probs"][i]) == r["outcome"] for r in vs)
    print(f"中信号场（隐含40~70%）: {len(vs)} 场，命中 {v_hit} = {v_hit / len(vs):.1%}" if vs else "")
    ws = [r for r in rows if max(r["probs"]) < 0.4]
    w_hit = sum(max(range(3), key=lambda i: r["probs"][i]) == r["outcome"] for r in ws)
    print(f"混战场（隐含<40%）: {len(ws)} 场，命中 {w_hit} = {w_hit / len(ws):.1%}" if ws else "")


def part_b_score_hit():
    print("\n" + "=" * 92)
    print("B. 比分/进球命中率（方案级 corpus + DC 比分矩阵 TOP-k）")
    print("=" * 92)
    corpus = json.loads((SUMM / "corpus.json").read_text(encoding="utf-8"))
    recs = corpus["records"]
    plays = {}
    for r in recs:
        plays.setdefault(r.get("play") or "未知", []).append(r)
    print(f"\ncorpus 方案级（{len(recs)} 条腿，{len(plays)} 种玩法）：")
    print(f"{'玩法':<8}{'腿数':>6}{'方向命中*':>12}{'比分命中*':>12}{'未回填':>8}")
    for play, lst in sorted(plays.items(), key=lambda kv: -len(kv[1])):
        n = len(lst)
        d_known = [r for r in lst if r.get("directionHit") is not None]
        s_known = [r for r in lst if r.get("scoreHit") is not None]
        d_rate = f"{sum(r['directionHit'] for r in d_known) / len(d_known):.0%}" if d_known else "-"
        s_rate = f"{sum(r['scoreHit'] for r in s_known) / len(s_known):.0%}" if s_known else "-"
        n_unknown = sum(1 for r in lst if r.get("directionHit") is None and r.get("scoreHit") is None)
        print(f"{play:<8}{n:>6}{d_rate:>12}{s_rate:>12}{n_unknown:>8}")
    print("* 仅统计已回填赛果的腿；未回填=赛果缺失")

    # DC 比分矩阵 TOP-k：用现有 {league}_dc.json 参数对 2526 已回填赛果逐场算
    # （口径注意：参数含全季拟合，存在轻泄漏，研究参考而非严格评估）
    from backtest import dc_three
    from dc_fit import load_matches
    t1 = t3 = t5 = ttg3 = n = 0
    for lg in FD_LEAGUES:
        dc_path = CACHE / f"{lg}_dc.json"
        if not dc_path.exists():
            continue
        dc = json.loads(dc_path.read_text(encoding="utf-8"))
        for m in load_matches(lg, ["2526"]):
            res = dc_three(dc["teams"], dc["homeAdv"], dc["rho"], m["home"], m["away"])
            if res is None:
                continue
            _, matrix = res
            flat = sorted(((f"{i}-{j}", float(matrix[i, j])) for i in range(7) for j in range(7)),
                          key=lambda kv: -kv[1])
            n += 1
            actual = f"{m['hg']}-{m['ag']}"
            if flat[0][0] == actual:
                t1 += 1
            if any(s == actual for s, _ in flat[:3]):
                t3 += 1
            if any(s == actual for s, _ in flat[:5]):
                t5 += 1
            # 总进球带：比分按概率排序合并出总进球档，取前3档
            tot = m["hg"] + m["ag"]
            tg_rank = []
            for s, _ in flat:
                g = sum(map(int, s.split("-")))
                if g not in tg_rank:
                    tg_rank.append(g)
            if tot in tg_rank[:3]:
                ttg3 += 1
    if n:
        print(f"\nDC 模型比分矩阵（backtest {n} 场，2526）：")
        print(f"  TOP1 命中 {t1 / n:.1%} | TOP3 命中 {t3 / n:.1%} | TOP5 命中 {t5 / n:.1%}")
        print(f"  总进球带（概率前3档）命中 {ttg3 / n:.1%}")


def part_c_goal_factors():
    print("\n" + "=" * 92)
    print("C. 进球影响因素盘点（归因因子 + 联赛画像进球结构）")
    print("=" * 92)
    attr = json.loads((SUMM / "attribution.json").read_text(encoding="utf-8"))
    print("\n归因因子分布（错题归因，F9=校准低估 F5=模型分歧）：")
    for f, s in attr.get("factorStats", {}).items():
        print(f"  {f}: nPrimary={s['nPrimary']} avgProbGap={s['avgProbGap']:.2%}")

    lg_dir = ROOT / "data" / "00-leagues"
    print("\n联赛进球结构（standings 现算）：")
    print(f"{'联赛':<26}{'队数':>4}{'已赛场次':>8}{'场均进球':>9}{'主胜率':>8}{'主队得分占比':>10}")
    for f in sorted(lg_dir.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            st = d.get("standings") or []
            if not st:
                continue
            played = sum(t["played"] for t in st)
            games = played / 2
            goals = sum(t["gf"] for t in st)
            home_w = sum(t["homeRecord"]["w"] for t in st)
            home_pts = sum(t["homeRecord"]["w"] * 3 + t["homeRecord"]["d"] for t in st)
            all_pts = sum(t["won"] * 3 + t["drawn"] for t in st)
            print(f"{f.stem:<26}{len(st):>4}{int(games):>8}{goals / games:>9.2f}{home_w / games:>8.1%}"
                  f"{(home_pts / all_pts if all_pts else 0):>10.1%}")
        except (json.JSONDecodeError, KeyError, TypeError):
            continue


if __name__ == "__main__":
    part_a_market_vs_had()
    part_b_score_hit()
    part_c_goal_factors()
