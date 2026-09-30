# -*- coding: utf-8 -*-
r"""比分为何天生离散——4979 场实测的结构分解（大哥 2026-09-30 问）。

问题：为什么比分出现的是离散分布，而不是集中在某个比分上？

拆三层回答（只描述结构，不建模型）：
  ① 边际：单队进球数的分布——进球是稀有事件，单队 90 分钟期望只有 ~1.4 球，
     泊松形状下"最可能的进球数"（1 球）概率也只有 ~1/3。
  ② 乘积：比分 = 主队进球 × 客队进球 两道独立低频题的联合——1/3 × 1/3 ≈ 11%，
     这就是最高频比分 1:1 只能停在 ~12% 的数学根源。
  ③ 条件：即便把比赛缩到"超热主队"（HAD 主胜赔率 <1.35）这种最有把握的子集，
     最高频比分占比也上不去多少——离散是结构，不是信息不足。

用法：python engine/scripts/research/score_dispersion.py
开发者 sszhang
"""
import json
import math
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HIST = (ROOT / "engine" / "cache" / "hist_odds"
        / "crs_hist_2025-10-01_2026-09-28.json")
OUT = ROOT / "data" / "04-summaries" / "2026-09-30-score-dispersion.json"
FAV_ODDS_MAX = 1.35    # "超热主队"切分线


def poisson_p(lam, k):
    return math.exp(-lam) * lam ** k / math.factorial(k)


def main():
    ms = json.loads(HIST.read_text(encoding="utf-8"))["matches"]
    rows = []
    for m in ms:
        s = str(m.get("score") or "")
        had = m.get("had") or {}
        if ":" not in s:
            continue
        try:
            h, a = (int(x) for x in s.split(":")[:2])
            oh = float(had.get("h"))
        except (ValueError, TypeError):
            continue
        if not oh or oh <= 1.0:
            continue
        rows.append({"hg": h, "ag": a, "oddsH": oh})
    n = len(rows)
    print(f"样本 {n} 场（2025-10-01 ~ 2026-09-28·带 HAD 主胜价）\n")

    lam_h = sum(r["hg"] for r in rows) / n
    lam_a = sum(r["ag"] for r in rows) / n
    print("=" * 70)
    print(f"① 单队进球边际分布（实际 vs 泊松拟合·λ主={lam_h:.2f} λ客={lam_a:.2f}）")
    print("=" * 70)
    print(f"  {'进球数':<6}{'主队实际':>10}{'泊松':>10}{'客队实际':>10}{'泊松':>10}")
    for k in range(5):
        ph = sum(1 for r in rows if r["hg"] == k) / n
        pa = sum(1 for r in rows if r["ag"] == k) / n
        print(f"  {k:<8}{ph:>9.1%}{poisson_p(lam_h, k):>10.1%}"
              f"{pa:>10.1%}{poisson_p(lam_a, k):>10.1%}")
    mode_h = Counter(r["hg"] for r in rows).most_common(1)[0]
    print(f"\n  主队最可能进球数 = {mode_h[0]} 球，概率也只有 {mode_h[1]/n:.1%}"
          f"  ← 每道'单队题'的答案上限就只有 ~1/3")

    print("\n" + "=" * 70)
    print("② 比分 = 两道独立低频题的联合（乘积结构）")
    print("=" * 70)
    sc_cnt = Counter((r["hg"], r["ag"]) for r in rows)
    ph1 = sum(1 for r in rows if r["hg"] == 1) / n
    pa1 = sum(1 for r in rows if r["ag"] == 1) / n
    print(f"  P(主队进1球) = {ph1:.1%}，P(客队进1球) = {pa1:.1%}")
    print(f"  若近似独立：P(1:1) ≈ {ph1:.1%}×{pa1:.1%} = {ph1*pa1:.1%}")
    print(f"  实测 P(1:1) = {sc_cnt[(1,1)]/n:.1%}  ← 与乘积吻合，这就是天花板的由来")
    cum = 0
    ranks = sc_cnt.most_common()
    print(f"\n  比分集中度曲线：")
    for k in (1, 3, 6, 10, 20, 31):
        cum_k = sum(c for _, c in ranks[:k]) / n
        print(f"    最常见 {k:>2} 个比分覆盖 {cum_k:5.1%} 的比赛"
              f"（其余 {1-cum_k:5.1%} 散在 {31-k} 个比分上）")

    print("\n" + "=" * 70)
    print(f"③ 条件实验：只看超热主队（主胜赔率<{FAV_ODDS_MAX}）·最有把握的子集")
    print("=" * 70)
    fav = [r for r in rows if r["oddsH"] < FAV_ODDS_MAX]
    if fav:
        fc = Counter((r["hg"], r["ag"]) for r in fav).most_common(5)
        nf = len(fav)
        fav_prob = sum(1 / r["oddsH"] for r in fav) / nf / 1.13   # 去水（体彩 ~13% 抽水）
        print(f"  子样本 {nf} 场·去水主胜市场概率 ≈ {fav_prob:.0%}")
        for (h, a), c in fc:
            print(f"    {h}:{a}  {c:3d} 场  {c/nf:5.1%}")
        top_share = fc[0][1] / nf
        print(f"\n  即便这种一场定胜负的碾压局，最高频比分也只 {top_share:.1%}"
              f"  ← 仍有 {1-top_share:.0%} 的比赛不是它")
        # 分布宽度对比
        uniq_fav = len(set((r['hg'], r['ag']) for r in fav))
        print(f"  子集内实际出现过的比分仍有 {uniq_fav} 种")

    OUT.write_text(json.dumps({
        "ranAt": "2026-09-30", "nMatches": n,
        "lambda": {"home": round(lam_h, 3), "away": round(lam_a, 3)},
        "marginal": {str(k): {
            "homeEmp": round(sum(1 for r in rows if r["hg"] == k) / n, 4),
            "homePois": round(poisson_p(lam_h, k), 4),
            "awayEmp": round(sum(1 for r in rows if r["ag"] == k) / n, 4),
            "awayPois": round(poisson_p(lam_a, k), 4)} for k in range(5)},
        "productCheck": {"pHome1": round(ph1, 4), "pAway1": round(pa1, 4),
                         "product": round(ph1 * pa1, 4),
                         "empirical11": round(sc_cnt[(1, 1)] / n, 4)},
        "concentration": {str(k): round(sum(c for _, c in ranks[:k]) / n, 4)
                          for k in (1, 3, 6, 10, 20, 31)},
        "favSubset": {"oddsMax": FAV_ODDS_MAX, "n": len(fav),
                      "topScores": [[f"{h}:{a}", c, round(c / nf, 4)]
                                    for (h, a), c in fc],
                      "topShare": round(top_share, 4),
                      "nDistinctScores": uniq_fav},
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\n→ {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
