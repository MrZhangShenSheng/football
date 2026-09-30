# -*- coding: utf-8 -*-
r"""融合系数 a 重校（fusion.json note 预案：backtest 积累 >=100 场后最小化 RPS 重校）。

做法：每个联赛×赛季跑一次 walk_forward（a 任取，DC/市场概率与 a 无关）→ 缓存逐场
p_dc/p_mkt → 在 a 网格上重新融合算 RPS，取最优 a。同时给全局 a 一个总样本口径的最优值。

护栏（沿用 2026-09-15 荷甲立项口径）：
  ① 联赛级 override 仅在 n>=MIN_N 且相对当前 a 的 RPS 改善 >1% 时才建议改动。
  ② 配对 bootstrap 95% 区间上限 <0 才算显著（ΔRPS=最优a − 当前a）。
  ③ 只输出建议 + --apply 才写 fusion.json；比分玩法用 DC 矩阵不受 a 影响，勿据此停 DC。
用法：
  python engine/scripts/research/fusion_retune.py            # 只看建议
  python engine/scripts/research/fusion_retune.py --apply    # 写回 fusion.json
开发者 sszhang
"""
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "engine" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from backtest import rps, walk_forward  # noqa: E402
from backtest_sweep import discover, market_index  # noqa: E402
from common import load_fusion_ab  # noqa: E402
from dc_fit import load_matches  # noqa: E402
from dc_predict import fuse  # noqa: E402

FUSION_PATH = ROOT / "engine" / "cache" / "fusion.json"
OUT = ROOT / "data" / "04-summaries" / "2026-09-30-fusion-retune.json"
A_GRID = [round(x * 0.05, 2) for x in range(0, 13)]   # 0.00 ~ 0.60
B_FIXED = 1.0
MIN_N = 150            # 联赛级建议门槛
IMPROVE_GUARD = 0.01   # 相对改善 >1% 才动（护栏）
N_BOOT = 2000
SEED = 20260930
APPLY = "--apply" in sys.argv


def rps_at(recs, a):
    return float(np.mean([rps(fuse(r["p_dc"], r["p_mkt"], a, B_FIXED), r["outcome"]) for r in recs]))


def series_at(recs, a):
    return np.array([rps(fuse(r["p_dc"], r["p_mkt"], a, B_FIXED), r["outcome"]) for r in recs])


def paired_ci(x, y, rng):
    d = x - y
    idx = rng.integers(0, len(d), size=(N_BOOT, len(d)))
    lo, hi = np.percentile(d[idx].mean(axis=1), [2.5, 97.5])
    return float(d.mean()), float(lo), float(hi)


def main():
    rng = np.random.default_rng(SEED)
    per_league, all_recs = {}, []
    for league, season in discover():
        mkt = market_index(league, season)
        matches = load_matches(league, [season]) if mkt else None
        if not matches:
            continue
        a_cur, _ = load_fusion_ab(league)
        recs = walk_forward(matches, mkt, a_cur, B_FIXED)
        if not recs:
            continue
        per_league.setdefault(league, []).extend(recs)
        all_recs += recs
    print(f"样本：{len(per_league)} 联赛 · 合计 {len(all_recs)} 场\n")

    curve = {a: rps_at(all_recs, a) for a in A_GRID}
    a_best_global = min(curve, key=curve.get)
    print("① 全样本 RPS 随 a（0=纯市场）")
    print("   " + "  ".join(f"a={a:.2f}:{curve[a]:.4f}" for a in A_GRID[:7]))
    print("   " + "  ".join(f"a={a:.2f}:{curve[a]:.4f}" for a in A_GRID[7:]))
    fus_pre = json.loads(FUSION_PATH.read_text(encoding="utf-8"))
    a_default = float(fus_pre.get("a", 0.4))   # 基线取当前全局默认（与"最优a"比恒为0，无意义）
    m, lo, hi = paired_ci(series_at(all_recs, a_best_global), series_at(all_recs, a_default), rng)
    print(f"   全样本最优 a={a_best_global:.2f}（RPS {curve[a_best_global]:.4f}）vs 当前默认 "
          f"a={a_default:.2f}（RPS {curve.get(a_default, rps_at(all_recs, a_default)):.4f}）"
          f" ΔRPS {m:+.4f} [{lo:+.4f}, {hi:+.4f}]"
          f"{' 显著更优✅' if hi < 0 else ' 分不开' if lo <= 0 else ' 显著更差'}")

    print(f"\n② 分联赛最优 a（n≥{MIN_N} 且改善>{IMPROVE_GUARD:.0%} 才建议改动）")
    fus = json.loads(FUSION_PATH.read_text(encoding="utf-8"))
    ov = dict(fus.get("leagueOverrides") or {})
    suggest, rows = {}, []
    for lg, recs in sorted(per_league.items(), key=lambda kv: -len(kv[1])):
        a_cur, _ = load_fusion_ab(lg)
        cur_rps = rps_at(recs, a_cur)
        best_a = min(A_GRID, key=lambda a: rps_at(recs, a))
        best_rps = rps_at(recs, best_a)
        gain = (cur_rps - best_rps) / cur_rps if cur_rps else 0.0
        _, _, hi_l = paired_ci(series_at(recs, best_a), series_at(recs, a_cur), rng)
        act = ""
        if len(recs) >= MIN_N and gain > IMPROVE_GUARD and hi_l < 0 and abs(best_a - a_cur) > 1e-9:
            suggest[lg] = best_a
            act = "→ 建议改"
        rows.append({"league": lg, "n": len(recs), "aCur": a_cur, "rpsCur": round(cur_rps, 4),
                     "aBest": best_a, "rpsBest": round(best_rps, 4), "gain": round(gain, 4),
                     "ciHi": round(hi_l, 4), "suggest": bool(act)})
        flag = "" if len(recs) >= MIN_N else " (n小仅记录)"
        print(f"  {lg:<26} n={len(recs):>4}  当前a={a_cur:.2f} RPS {cur_rps:.4f} | "
              f"最优a={best_a:.2f} RPS {best_rps:.4f}  改善{gain:+.1%} {act}{flag}")

    print("\n③ 结论")
    if suggest:
        for lg, a in suggest.items():
            print(f"  {lg}: a {load_fusion_ab(lg)[0]:.2f} → {a:.2f}")
    else:
        print("  无联赛满足 n≥门槛 + 改善>1% + 区间显著 三条，当前配置维持")
    print(f"  全局 a：当前 {fus.get('a')} · 全样本最优 {a_best_global:.2f}"
          f"（{'显著更优→建议下调全局' if hi < 0 else '差异不显著→维持'}）")

    OUT.write_text(json.dumps({
        "ranAt": date.today().isoformat(), "nTotal": len(all_recs),
        "globalCurve": {f"{a:.2f}": round(v, 4) for a, v in curve.items()},
        "aBestGlobal": a_best_global, "globalVsMarket": {"d": round(m, 4), "lo": round(lo, 4), "hi": round(hi, 4)},
        "perLeague": rows, "suggest": suggest, "applied": APPLY,
        "guards": {"minN": MIN_N, "improve": IMPROVE_GUARD, "grid": A_GRID},
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"→ {OUT.relative_to(ROOT)}")

    if APPLY and suggest:
        for lg, a in suggest.items():
            ov.setdefault(lg, {})["a"] = a
        fus["leagueOverrides"] = ov
        fus["lastTuned"] = date.today().isoformat()
        FUSION_PATH.write_text(json.dumps(fus, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"[apply] 已写 fusion.json（{len(suggest)} 个联赛）")
    elif APPLY:
        print("[apply] 无建议改动，未写盘")


if __name__ == "__main__":
    main()
