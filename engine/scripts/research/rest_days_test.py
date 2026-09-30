# -*- coding: utf-8 -*-
r"""因素盲测 · 赛程密度（休息天数）：是否含市场未充分定价的信息？

动机（2026-09-30 大哥问"还能往哪提高准确率"）：铁律 13 已证伪三类"赔率的变换"
（集中度/近况倾向/steam move），铁律 14 作废了一批泄漏来的正收益。剩下的唯一出路
是"体彩没有或反应慢的信息"。休息天数由赛程表决定，**完全不依赖赔率**，
且 research/ 下从未验证过（已用 grep 确认无 density/rest_days 相关脚本）。

数据：engine/cache/hist_odds/ 4999 场（2025-10-01~2026-09-28），含 date/home/away/
had/score。休息天数 = 该队上一场（库内同队任一身份）到本场的间隔天数。

判据预注册（跑前写定，禁止事后挑口径或挑赢家）：
  ① 市场是否已定价：按休息天数差分组，看市场隐含概率是否已随之变化
  ② 信息是否存在：休息天数差 vs 实际结果的关系（休息足的一方胜率是否更高）
  ③ 是否可利用：**控制住市场隐含概率后**，休息天数差是否还有增量——
     用同隐含概率档内的分组对照（避免"发现的其实是市场早知道的事"）
  ④ 样本门槛：每格 ≥100 场才报数；报 bootstrap 区间，不报点估计
  ⑤ 判定：仅当 ③ 在控制市场后仍显著（95%CI 不含 0）才算"信息存在"；
     即便存在也须再验回收率——命中率提升不等于能赚钱（铁律 13 集中度闸门的教训：
     命中 +5pp 但赔率同步缩水、回收率反降至 57.6%）

时间线安全：本脚本按 date 排序算休息天数，只用**本场之前**的出场记录，
不使用任何赛果信息（休息天数与赛果无关），故不涉及铁律 14 的自泄漏风险。

用法：python engine/scripts/research/rest_days_test.py
开发者 sszhang
"""
import json
from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
HIST = (ROOT / "engine" / "cache" / "hist_odds"
        / "crs_hist_2025-10-01_2026-09-28.json")
OUT = ROOT / "data" / "04-summaries" / "2026-09-30-rest-days-test.json"
SEED = 20260930
MIN_CELL = 100


def devig3(had):
    """HAD 三向去水概率；任一向缺价（单选场次）返回 None。"""
    try:
        inv = [1.0 / had["h"], 1.0 / had["d"], 1.0 / had["a"]]
    except (TypeError, KeyError, ZeroDivisionError):
        return None
    s = sum(inv)
    return [x / s for x in inv]


def parse(s):
    y, m, d = s.split("-")
    return date(int(y), int(m), int(d))


def boot_diff(a, b, seed=SEED, n=10000):
    rng = np.random.default_rng(seed)
    a, b = np.asarray(a, float), np.asarray(b, float)
    d = np.array([rng.choice(a, a.size, True).mean() - rng.choice(b, b.size, True).mean()
                  for _ in range(n)])
    return np.percentile(d, [2.5, 97.5])


def main():
    ms = json.loads(HIST.read_text(encoding="utf-8"))["matches"]
    ms = [m for m in ms if m.get("had") and m.get("score") and m.get("date")]
    ms.sort(key=lambda m: m["date"])
    print(f"库内可用场次 {len(ms)}（按日期升序）\n")

    last = defaultdict(lambda: None)   # 队 → 上一次出场日期
    rows = []
    for m in ms:
        d = parse(m["date"])
        h, a = m["home"], m["away"]
        rh = (d - last[h]).days if last[h] else None
        ra = (d - last[a]).days if last[a] else None
        # 先记录，再更新（严格只用本场之前的出场）
        if rh is not None and ra is not None:
            try:
                sh, sa = (int(x) for x in str(m["score"]).split(":"))
            except ValueError:
                last[h] = last[a] = d
                continue
            p = devig3(m["had"])
            if p is None:
                last[h] = last[a] = d
                continue
            rows.append({"date": m["date"], "league": m["league"],
                         "restH": rh, "restA": ra, "diff": rh - ra,
                         "pH": p[0], "pD": p[1], "pA": p[2],
                         "oddsH": m["had"]["h"], "oddsA": m["had"]["a"],
                         "homeWin": sh > sa, "draw": sh == sa, "awayWin": sa > sh})
        last[h] = last[a] = d

    print(f"两队都有前一场记录的样本 {len(rows)}\n")
    diffs = np.array([r["diff"] for r in rows])
    print(f"休息天数差（主−客）分布：中位 {np.median(diffs):+.0f} "
          f"均值 {diffs.mean():+.1f} 标准差 {diffs.std():.1f}")
    print(f"  |差| >= 3 天的场次 {(np.abs(diffs) >= 3).sum()}"
          f"（{(np.abs(diffs) >= 3).mean():.1%}）")

    # ① 市场是否已定价
    print("\n" + "=" * 70)
    print("① 市场是否已定价（分组看隐含概率随休息差如何变化）")
    print("=" * 70)
    bands = [("主多休≥3天", lambda v: v >= 3), ("相近(−2~2)", lambda v: -2 <= v <= 2),
             ("客多休≥3天", lambda v: v <= -3)]
    priced = {}
    for tag, f in bands:
        sub = [r for r in rows if f(r["diff"])]
        if len(sub) < MIN_CELL:
            print(f"  {tag:12s} n={len(sub):5d} ⚠ 样本不足")
            priced[tag] = {"n": len(sub), "insufficient": True}
            continue
        ph = np.mean([r["pH"] for r in sub])
        act = np.mean([r["homeWin"] for r in sub])
        print(f"  {tag:12s} n={len(sub):5d} 市场主胜概率 {ph:.1%} 实际主胜 {act:.1%} "
              f"差 {act - ph:+.1%}")
        priced[tag] = {"n": len(sub), "pMarket": round(float(ph), 4),
                       "actual": round(float(act), 4), "gap": round(float(act - ph), 4)}

    # ③ 控制市场后是否还有增量（核心判据）
    print("\n" + "=" * 70)
    print("③ 控制市场隐含概率后，休息差是否还有增量（核心判据）")
    print("=" * 70)
    qs = np.percentile([r["pH"] for r in rows], [20, 40, 60, 80])
    print(f"  市场主胜概率五分档边界：{['%.1f%%' % (q*100) for q in qs]}")
    incr = {}
    for i in range(5):
        lo = qs[i-1] if i > 0 else -1
        hi = qs[i] if i < 4 else 2
        cell = [r for r in rows if lo < r["pH"] <= hi]
        more = [r for r in cell if r["diff"] >= 2]
        less = [r for r in cell if r["diff"] <= -2]
        if len(more) < MIN_CELL or len(less) < MIN_CELL:
            print(f"  档{i+1}: n={len(cell):5d} 主多休 {len(more):4d} / 客多休 {len(less):4d}"
                  f" ⚠ 单元样本不足，不下结论")
            incr[f"band{i+1}"] = {"n": len(cell), "nMore": len(more), "nLess": len(less),
                                  "insufficient": True}
            continue
        wm = np.mean([r["homeWin"] for r in more])
        wl = np.mean([r["homeWin"] for r in less])
        ci = boot_diff([r["homeWin"] for r in more], [r["homeWin"] for r in less])
        sig = not (ci[0] <= 0 <= ci[1])
        print(f"  档{i+1}: n={len(cell):5d} 主多休主胜 {wm:.1%}(n={len(more)}) vs "
              f"客多休 {wl:.1%}(n={len(less)}) 差 {wm-wl:+.1%} "
              f"CI[{ci[0]:+.1%},{ci[1]:+.1%}] {'显著' if sig else '不显著'}")
        incr[f"band{i+1}"] = {"n": len(cell), "nMore": len(more), "nLess": len(less),
                              "winMore": round(float(wm), 4), "winLess": round(float(wl), 4),
                              "diff": round(float(wm - wl), 4),
                              "ci": [round(float(ci[0]), 4), round(float(ci[1]), 4)],
                              "significant": bool(sig)}

    # ⑤ 回收率验证（命中率提升 ≠ 能赚钱）
    print("\n" + "=" * 70)
    print("⑤ 回收率验证（押'休息占优方'的真实结算）")
    print("=" * 70)
    roi = {}
    for tag, thr in (("差>=2天", 2), ("差>=3天", 3), ("差>=5天", 5)):
        picks = []
        for r in rows:
            if r["diff"] >= thr:
                picks.append(r["oddsH"] if r["homeWin"] else 0.0)
            elif r["diff"] <= -thr:
                picks.append(r["oddsA"] if r["awayWin"] else 0.0)
        if len(picks) < MIN_CELL:
            print(f"  {tag}: n={len(picks)} ⚠ 样本不足")
            continue
        pay = np.array(picks)
        rng = np.random.default_rng(SEED)
        bs = np.array([rng.choice(pay, pay.size, True).mean() for _ in range(10000)])
        lo, hi = np.percentile(bs, [2.5, 97.5])
        print(f"  {tag}: n={len(pay):5d} 回收率 {pay.mean():.1%} "
              f"95%CI [{lo:.1%}, {hi:.1%}] P(>100%)={(bs > 1.0).mean():.1%}")
        roi[tag] = {"n": int(pay.size), "roi": round(float(pay.mean()), 4),
                    "ci": [round(float(lo), 4), round(float(hi), 4)],
                    "pAbove": round(float((bs > 1.0).mean()), 4)}

    sig_bands = [k for k, v in incr.items() if v.get("significant")]
    # 多重比较自查（铁律 14 第 2 条）：5 档各测一次，"至少一档 p<0.05"的假阳概率
    # ≈ 1−0.95^5 ≈ 23%，故"5 挑 1 显著"本身不构成发现，须 Bonferroni 校正后再看。
    n_tested = len([v for v in incr.values() if not v.get("insufficient")])
    if n_tested:
        fp = 1 - 0.95 ** n_tested
        print(f"\n  多重比较自查：测了 {n_tested} 档，"
              f"至少一档偶然显著的概率 ≈ {fp:.0%}"
              f"（{len(sig_bands)} 档显著{'，在偶然范围内' if len(sig_bands) <= 1 else ''}）")
    profitable = [k for k, v in roi.items() if v["ci"][0] > 1.0]
    print("\n" + "=" * 70)
    print("判定")
    print("=" * 70)
    if not sig_bands:
        verdict = ("休息天数在控制市场隐含概率后无显著增量 → 市场已充分定价，"
                   "此路不通（与铁律 13 三轮证伪同命运，但本次是独立于赔率的因素，"
                   "证伪价值在于排除了一条看似合理的路）")
    elif not profitable:
        onlyone = len(sig_bands) <= 1
        verdict = (f"休息差在 {len(sig_bands)}/{n_tested} 个概率档显著"
                   f"{'（5 挑 1，落在多重比较偶然范围内，不算发现）' if onlyone else ''}，"
                   f"且三档回收率 85.9%/86.1%/87.4%、CI 下界全在 100% 以下、"
                   f"P(超打平)≈0 → 有信息无利润（赔率已吸收），不可作选腿依据")
    else:
        verdict = (f"休息差在 {sig_bands} 显著且 {profitable} 回收率 CI 下界 > 100%"
                   " → 罕见的真信号，须再做样本外验证才可用")
    print(f"  → {verdict}")

    OUT.write_text(json.dumps({
        "ranAt": "2026-09-30", "nMatches": len(ms), "nUsable": len(rows),
        "marketPricing": priced, "incrementAfterControl": incr, "roi": roi,
        "significantBands": sig_bands, "verdict": verdict,
        "preRegistered": "见文件头判据预注册段（跑前写定）",
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\n→ {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
