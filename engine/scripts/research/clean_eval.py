# -*- coding: utf-8 -*-
r"""干净数据重测（2026-09-30 建议 1）：多场景 v2/v3/v4b 规则 vs 市场/经验频率基线。
时间线一律走 common.strict_merged（预测某场时只见其日期前 ≥2 天的联赛赛果）。

预注册判据（写代码前锁定，事后不改）：
  ① 单场：top1/top2 命中率、top1 单注回收率（每场押 1 注模型第一选，中则回该比分赔率）——
     同批场次与"市场最低赔率比分"做配对 bootstrap，差值 95% 区间下限 >0 才算赢市场。
  ② 分歧场：模型第一选 ≠ 市场第一选的场次上，模型命中率须高于市场（偏离是信号不是噪声）。
  ③ 滚动选型：每月只用此前月份成绩挑 top1 回收率最高的方案（含市场）用于当月——
     挑选不偷看未来；区间下限 >0 地赢"永远跟市场"才算有可用信号。
  局限：v4b 状态组合表出自泄漏诊断、无法用历史重推，只能固定；加成系数在 BOOST_GRID
  内由③滚动择优（0=不加成）。所有方案只在体彩池内有价的比分里排名（可买才可比）。
用法：python engine/scripts/research/clean_eval.py
开发者 sszhang
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engine" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import score_family_model as sfm  # noqa: E402
from common import load_aliases, strict_merged  # noqa: E402
import engine.multi_scene_predictor as ms2  # noqa: E402
import engine.multi_scene_predictor_v3 as ms3  # noqa: E402
import engine.predictor_v4b as v4b  # noqa: E402

START_DATE = "2025-10-01"          # 同 v4b 回测起点
MIN_POOL = 20                       # CRS 池有效项下限（同 v4b 回测装载口径）
N_BOOT = 2000
SEED = 20260930
MIN_PRIOR_MONTHS = 2                # 滚动选型至少要有 2 个月历史成绩才开始
BOOST_GRID = (0.0, 0.5, 1.0, 1.25, 2.0)
MARKET = "市场最低赔率"
FREQ = "经验频率"
V4B_PROD = "v4b×1.25"               # 生产版配置
OUT = ROOT / "data" / "04-summaries" / "2026-09-30-clean-eval.json"


def load_hist():
    """体彩历史已完赛场（带 CRS 池）→ [{date, home, away, actual, pool}]，同 v4b 回测装载口径。"""
    out, seen = [], set()
    for p in sorted((ROOT / "engine/cache/hist_odds").glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for m in d.get("matches", []):
            sc = str(m.get("score") or "")
            if ":" not in sc:
                continue
            try:
                actual = tuple(int(x) for x in sc.split(":")[:2])
            except ValueError:
                continue
            pool = {}
            for k, v in (m.get("crs") or {}).items():
                try:
                    h, a = (int(x) for x in str(k).split(":")[:2])
                    o = float(v)
                except (ValueError, TypeError):
                    continue
                if o > 1.0:
                    pool[(h, a)] = o
            key = (str(m.get("date") or "")[:10], m.get("home"), m.get("away"))
            if len(pool) < MIN_POOL or key in seen:
                continue
            seen.add(key)
            out.append({"date": key[0], "home": key[1], "away": key[2], "actual": actual, "pool": pool})
    out.sort(key=lambda x: x["date"])
    return out


def keep(pred, pool):
    """模型比分序列 [((h,a), signal)] → 仅保留体彩池内可买比分（保序）。"""
    seq = (tuple(int(x) for x in sc) for sc, _ in pred)
    return [s for s in seq if s in pool]


def build_rows():
    zh = {s["zh"]: tid for tid, s in load_aliases().items() if s.get("zh")}
    blind = [dict(m, hid=zh.get(m["home"]), aid=zh.get(m["away"]))
             for m in load_hist() if m["date"] >= START_DATE]
    blind = [m for m in blind if m["hid"] and m["aid"]]
    merged = strict_merged(sfm.league_timeline(),
                           [(m["date"], m["hid"], m["aid"], m["actual"][0], m["actual"][1], i)
                            for i, m in enumerate(blind)])
    stats, freq = defaultdict(sfm.TeamStats), Counter()
    p2, p3 = ms2.MultiScenePredictorV2(), ms3.MultiScenePredictorV3()
    p4 = {b: v4b.PredictorV4b(boost_factor=b) for b in BOOST_GRID}
    rows = []
    for kind, d, h, a, hg, ag, idx in merged:
        if kind == "L":
            stats[h].add(hg, ag, True, a)
            stats[a].add(ag, hg, False, h)
            freq[(hg, ag)] += 1
            continue
        if stats[h].n < sfm.MIN_HIST or stats[a].n < sfm.MIN_HIST:
            continue
        m = blind[idx]
        pool = m["pool"]
        ranks = {MARKET: sorted(pool, key=lambda s: (pool[s], s)),
                 FREQ: sorted(pool, key=lambda s: (-freq[s], pool[s], s))}
        ranks["多场景v2"] = keep(p2.predict(ms2.team_stats_to_team_data(stats[h]),
                                          ms2.team_stats_to_team_data(stats[a]))["sorted_scores"], pool)
        ranks["多场景v3"] = keep(p3.predict(ms3.team_stats_to_team_data(stats[h]),
                                          ms3.team_stats_to_team_data(stats[a]),
                                          score_odds=pool)["sorted_scores"], pool)
        td_h, td_a = v4b.team_stats_to_team_data(stats[h]), v4b.team_stats_to_team_data(stats[a])
        for b, pr in p4.items():
            ranks[f"v4b×{b:g}"] = keep(pr.predict(td_h, td_a), pool)
        rows.append({"date": d, "actual": m["actual"], "pool": pool, "ranks": ranks})
    return rows, len(blind)


def arrays(rows, name):
    """→ (top1命中, top2命中, top1单注回款, top1比分) 逐场数组。"""
    h1, h2, pay, t1s = [], [], [], []
    for r in rows:
        rk, act = r["ranks"][name], r["actual"]
        t1 = rk[0] if rk else None
        h1.append(float(t1 == act))
        h2.append(float(act in rk[:2]))
        pay.append(r["pool"][t1] if t1 == act else 0.0)
        t1s.append(t1)
    return np.array(h1), np.array(h2), np.array(pay), t1s


def paired_ci(x, y, rng, n_boot=N_BOOT):
    """配对 bootstrap：mean(x−y) 的 95% 区间。"""
    d = x - y
    idx = rng.integers(0, len(d), size=(n_boot, len(d)))
    lo, hi = np.percentile(d[idx].mean(axis=1), [2.5, 97.5])
    return float(d.mean()), float(lo), float(hi)


def fmt_ci(t, pct=True):
    k = 100 if pct else 1
    return f"{t[0] * k:+.1f}pp [{t[1] * k:+.1f}, {t[2] * k:+.1f}]"


def main():
    rows, n_blind = build_rows()
    n = len(rows)
    rng = np.random.default_rng(SEED)
    names = list(rows[0]["ranks"])
    arr = {nm: arrays(rows, nm) for nm in names}
    mh1, mh2, mpay, mt1 = arr[MARKET]
    print(f"干净重测：体彩可映射场 {n_blind}，过历史门槛可评 {n} 场（{rows[0]['date']} ~ {rows[-1]['date']}）")

    print("\n① 单场准确率与单注回收（差值=方案−市场，[95%区间]）")
    print(f"{'方案':<12}{'top1':>7}{'top2':>7}{'回收率':>8}   {'top1差':<24}{'回收率差'}")
    summary = {}
    for nm in names:
        h1, h2, pay, _ = arr[nm]
        d1 = paired_ci(h1, mh1, rng) if nm != MARKET else None
        dr = paired_ci(pay, mpay, rng) if nm != MARKET else None
        summary[nm] = {"top1": h1.mean(), "top2": h2.mean(), "recovery": pay.mean(),
                       "dTop1": d1, "dRecovery": dr}
        print(f"{nm:<12}{h1.mean():>7.1%}{h2.mean():>7.1%}{pay.mean():>8.1%}   "
              f"{(fmt_ci(d1) if d1 else '—'):<24}{fmt_ci(dr) if dr else '—'}")

    print("\n② 分歧场（方案第一选 ≠ 市场第一选）上的命中率")
    disagree = {}
    for nm in names:
        if nm == MARKET:
            continue
        h1, _, pay, t1 = arr[nm]
        mask = np.array([a != b for a, b in zip(t1, mt1)])
        k = int(mask.sum())
        if k == 0:
            continue
        ci = paired_ci(h1[mask], mh1[mask], rng)
        disagree[nm] = {"n": k, "share": k / n, "model": float(h1[mask].mean()),
                        "market": float(mh1[mask].mean()), "diff": ci}
        print(f"  {nm:<12} 分歧 {k:>4} 场({k / n:.0%})  方案 {h1[mask].mean():.1%} vs 市场 {mh1[mask].mean():.1%}"
              f"  差 {fmt_ci(ci)}")

    print("\n③ 滚动选型（每月只看此前月份的 top1 回收率挑方案）")
    months = sorted({r["date"][:7] for r in rows})
    mon_of = np.array([r["date"][:7] for r in rows])
    acc = {nm: [0.0, 0] for nm in names}
    wf_pay, wf_mkt, picks = [], [], []
    for i, mon in enumerate(months):
        sel = mon_of == mon
        if i >= MIN_PRIOR_MONTHS:
            best = max(names, key=lambda nm: (acc[nm][0] / acc[nm][1] if acc[nm][1] else 0.0,
                                              nm == MARKET))
            picks.append((mon, best, float(arr[best][2][sel].mean()), float(mpay[sel].mean())))
            wf_pay.append(arr[best][2][sel])
            wf_mkt.append(mpay[sel])
        for nm in names:
            acc[nm][0] += float(arr[nm][2][sel].sum())
            acc[nm][1] += int(sel.sum())
    for mon, best, r_best, r_mkt in picks:
        print(f"  {mon}: 选 {best:<12} 当月回收 {r_best:.1%}  （市场 {r_mkt:.1%}）")
    wf_pay, wf_mkt = np.concatenate(wf_pay), np.concatenate(wf_mkt)
    wf_ci = paired_ci(wf_pay, wf_mkt, rng)
    print(f"  滚动选型合计回收 {wf_pay.mean():.1%} vs 永远跟市场 {wf_mkt.mean():.1%}，差 {fmt_ci(wf_ci)}")

    beat = [nm for nm in names if nm != MARKET
            and summary[nm]["dRecovery"][1] > 0 and summary[nm]["dTop1"][1] > 0]
    verdict = ("有方案在单场命中率与回收率上都显著赢市场：" + "、".join(beat)) if beat else \
        "无任何方案在单场命中率与回收率上显著赢市场（差值 95% 区间下限均 ≤0）"
    wf_verdict = "滚动选型显著赢市场" if wf_ci[1] > 0 else "滚动选型未能显著赢市场"
    print(f"\n结论：{verdict}；{wf_verdict}")

    OUT.write_text(json.dumps({
        "date": "2026-09-30", "method": "common.strict_merged(lag=2) + 预注册判据①②③（见 clean_eval.py 头部）",
        "nBlindMapped": n_blind, "nEval": n, "period": [rows[0]["date"], rows[-1]["date"]],
        "single": summary, "disagree": disagree,
        "walkForward": {"picks": picks, "recovery": float(wf_pay.mean()),
                        "marketRecovery": float(wf_mkt.mean()), "diff": wf_ci},
        "verdict": verdict, "walkForwardVerdict": wf_verdict,
    }, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    print(f"→ {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
