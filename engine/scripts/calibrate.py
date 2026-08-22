#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""融合系数重校（闭环 P2-C / I1）：语料 n≥100 → 网格搜 a/b 最小化平均 RPS → 写回 fusion.json。

护栏（设计文档 docs/2026-08-22-learning-loop-design.html §三）：
- 门槛：已回填 n ≥ 100（corpus.json readiness.calibrateReady）
- a 封顶 0.6（防小样本把模型权重顶上天）；b 固定 1.0
- 改善 < 1% 不动（只记录结论）
- 旧值进 fusion_history.json（可回滚）

用法：
  python calibrate.py        # 门槛检查 → 重校或跳过
"""
import json
import math
from datetime import date
from pathlib import Path

from common import log, ROOT
from corpus import CALIBRATE_MIN_N

CACHE = ROOT / "engine" / "cache"
FUSION = CACHE / "fusion.json"
FUSION_HISTORY = CACHE / "fusion_history.json"
CORPUS = ROOT / "data" / "04-summaries" / "corpus.json"

A_GRID = [round(0.05 * i, 2) for i in range(13)]  # 0.05 ~ 0.60
A_CAP = 0.60
IMPROVE_MIN = 0.01  # RPS 改善 <1% 不动


def rps(probs: list[float], oi: int) -> float:
    o = [0.0, 0.0, 0.0]
    o[oi] = 1.0
    return 0.5 * sum((sum(probs[:k + 1]) - sum(o[:k + 1])) ** 2 for k in range(2))


def devig(odds: list[float]) -> list[float]:
    inv = [1 / o for o in odds]
    s = sum(inv)
    return [i / s for i in inv]


def fuse_logpool(p_dc: list[float], p_mkt: list[float], a: float, b: float = 1.0) -> list[float]:
    """与 dc_predict.fuse 同式：log-odds 意见池。"""
    s = a + b
    a, b = a / s, b / s
    z = [a * math.log(max(p, 1e-12)) + b * math.log(max(m, 1e-12)) for p, m in zip(p_dc, p_mkt)]
    mx = max(z)
    e = [math.exp(v - mx) for v in z]
    t = sum(e)
    return [v / t for v in e]


def load_pairs() -> list[tuple[list[float], list[float], int]]:
    """语料 → [(p_dc, p_mkt, outcome_idx)]。市场锚 = p_final 在 dc_used=false 场的值；
    dc 场无独立市场列 → 用 p_final 兼作两侧（此时融合对比退化为常数，跳过该场）。"""
    if not CORPUS.exists():
        return []
    c = json.loads(CORPUS.read_text(encoding="utf-8"))
    pairs = []
    for r in c.get("records", []):
        if not r.get("result") or "-" not in str(r["result"]):
            continue
        try:
            hg, ag = (int(x) for x in str(r["result"]).split("-"))
        except ValueError:
            continue
        oi = 0 if hg > ag else (1 if hg == ag else 2)
        p_mkt = r.get("p_final")
        p_dc = r.get("p_dc") or r.get("dc")
        if p_mkt and len(p_mkt) == 3 and p_dc and len(p_dc) == 3:
            pairs.append((p_dc, p_mkt, oi))
    return pairs


def main() -> None:
    if not CORPUS.exists():
        log("calibrate", "缺 corpus.json（先跑 corpus.py）")
        return
    c = json.loads(CORPUS.read_text(encoding="utf-8"))
    n_result = c.get("readiness", {}).get("n_result", 0)
    if n_result < CALIBRATE_MIN_N:
        log("calibrate", f"门槛未达：已回填 {n_result}/{CALIBRATE_MIN_N}，跳过（差 {CALIBRATE_MIN_N - n_result} 条）")
        return
    pairs = load_pairs()
    if len(pairs) < CALIBRATE_MIN_N:
        log("calibrate", f"含 p_dc+p_final 双列的已回填仅 {len(pairs)} 场（缺 p_dc 列，新轮预测起补采），跳过")
        return

    fus = json.loads(FUSION.read_text(encoding="utf-8")) if FUSION.exists() else {"a": 0.4, "b": 1.0}
    a_old = fus["a"]

    def mean_rps(a: float) -> float:
        return sum(rps(fuse_logpool(pdc, pmkt, a), oi) for pdc, pmkt, oi in pairs) / len(pairs)

    results = [(round(a, 2), mean_rps(a)) for a in A_GRID]
    results.sort(key=lambda kv: kv[1])
    a_best, rps_best = results[0]
    rps_old = mean_rps(a_old)
    improve = (rps_old - rps_best) / rps_old

    log("calibrate", f"n={len(pairs)} · 当前 a={a_old} RPS={rps_old:.4f} · 最优 a={a_best} RPS={rps_best:.4f}（改善 {improve:+.2%}）")
    if improve < IMPROVE_MIN:
        log("calibrate", f"改善 <{IMPROVE_MIN:.0%}，不升级（护栏）")
        return
    if a_best > A_CAP:
        log("calibrate", f"最优 a={a_best} 超封顶 {A_CAP}，拒绝（防小样本过拟合）")
        return
    # 写回 + 历史
    history = json.loads(FUSION_HISTORY.read_text(encoding="utf-8")) if FUSION_HISTORY.exists() else []
    history.append({"date": date.today().isoformat(), "a_old": a_old, "a_new": a_best,
                    "n": len(pairs), "rps_old": round(rps_old, 4), "rps_new": round(rps_best, 4),
                    "reason": f"语料 {len(pairs)} 场网格搜索最优"})
    FUSION_HISTORY.write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    fus.update({"a": a_best, "b": 1.0, "lastTuned": date.today().isoformat(), "tunedN": len(pairs),
                "note": f"calibrate.py 网格搜索（历史见 fusion_history.json，共 {len(history)} 次调整）"})
    FUSION.write_text(json.dumps(fus, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log("calibrate", f"✅ 融合系数已重校 a {a_old}→{a_best}（RPS {rps_old:.4f}→{rps_best:.4f}），旧值入 fusion_history.json")


if __name__ == "__main__":
    main()
