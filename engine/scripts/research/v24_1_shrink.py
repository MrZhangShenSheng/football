# -*- coding: utf-8 -*-
"""v24.1：收缩方向正式检验（v24 诊断导出·判据预注册于 fade-strategy-prereg v24.1）。

v24 发现：模型按自带带在 >=55% 段 dev +14.8pp/+8.2pp（过度自信），净胜球 OLS
斜率 0.54/0.68 < 1（极值过度分化），锐化（γ>1）两段单调恶化。方向应为「收缩」。

三臂（跑前写死）：
  A1 γ 展平扫描：p^γ 归一·γ∈[0.4,1.0]——纯信息性
  A2 基准率收缩：p' = (1−w)·p + w·base（base=fit 段经验 h/d/a 频率）·w∈[0,1]——纯信息性
  A3 对数意见池（主判据）：q ∝ p_model^a · p_mkt^1（a∈[0,1]·a=0 即纯市场基线）
     ——与 CLAUDE.md 铁律4 融合式同构；a 的最优值即「模型对市场的边际价值」

判据：
  ① A1/A2 信息性：若最优点落在 γ=1/w=0 则「模型自身概率无可救」反证；
     落在内点则说明收缩方向真实存在
  ② A3 主判据：fit 段标定 a* → val 段 log-loss 须优于 a=0（纯市场）≥1%（calibrate
     护栏惯例）且逐场配对 bootstrap（2000次·种子 20261009）CI 下限 >0
  ③ 不过 → HAD 方向端对市场无增量定论·提准主线转比分端/伤停轨
  ④ RPS 同报·双指标须同向（不作独立判据）

数据：v2 缓存（fit 4418 场 / val 3582 场·hadHist 体彩独立源 + as-of 预测）
产出：data/04-summaries/v24_1-shrink.json
开发者 sszhang
"""
from __future__ import annotations

import json
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from v11_s4_recalib import FIT_WINDOW, VAL_WINDOW
from v21_shape_policy import load_pre
from v24_sharpen_diag import load_rows, calib, print_calib

ROOT = Path(__file__).resolve().parents[3]
OUT_PATH = ROOT / "data" / "04-summaries" / "v24_1-shrink.json"

GAMMA_GRID = tuple(round(0.4 + 0.05 * i, 2) for i in range(13))    # 0.40 ~ 1.00
W_GRID = tuple(round(0.0 + 0.1 * i, 1) for i in range(11))         # 0.0 ~ 1.0
A_GRID = tuple(round(0.0 + 0.05 * i, 2) for i in range(21))        # 0.00 ~ 1.00
IMPROVE_FLOOR = 0.01          # calibrate 1% 护栏惯例
BOOT_N = 2000
SEED = 20261009


def _norm(p3):
    s = sum(p3)
    return [x / s for x in p3] if s > 0 else [1 / 3] * 3


def t_gamma(mp, mk, g):
    return _norm([max(x, 1e-12) ** g for x in mp])


def t_base(mp, mk, w, base):
    return _norm([(1 - w) * x + w * b for x, b in zip(mp, base)])


def t_pool(mp, mk, a):
    """对数意见池 q ∝ p_model^a · p_mkt^1（a=0 → 纯市场）。"""
    return _norm([max(m, 1e-12) ** a * max(k, 1e-12) for m, k in zip(mp, mk)])


def losses(rows, fn):
    """→ (mean_logloss, mean_rps, per_match_logloss列表)。"""
    lls, rs = [], []
    for mp, mk, out, _, _ in rows:
        p = fn(mp, mk)
        lls.append(-math.log(max(p[out], 1e-12)))
        obs = [1.0 if i == out else 0.0 for i in range(3)]
        cp = co = s = 0.0
        for i in range(2):
            cp += p[i]
            co += obs[i]
            s += (cp - co) ** 2
        rs.append(s / 2.0)
    n = len(lls) or 1
    return sum(lls) / n, sum(rs) / n, lls


def base_rate(rows):
    cnt = [0, 0, 0]
    for _, _, out, _, _ in rows:
        cnt[out] += 1
    n = len(rows) or 1
    return [c / n for c in cnt]


def boot_ci(diffs):
    """逐场配对差 bootstrap 均值 95% CI（diffs 为 市场loss − 候选loss·>0=候选更优）。"""
    n = len(diffs)
    if n == 0:
        return [0.0, 0.0]
    rng = random.Random(SEED)
    means = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(BOOT_N))
    return [round(means[int(BOOT_N * 0.025)], 6), round(means[int(BOOT_N * 0.975)], 6)]


def main():
    print("══ v24.1 收缩方向正式检验 ══\n", flush=True)
    print("预注册: fade-strategy-prereg v24.1（跑前写死）\n", flush=True)
    pre = load_pre()
    fit_rows = load_rows(pre, FIT_WINDOW)
    val_rows = load_rows(pre, VAL_WINDOW)
    print(f"fit {len(fit_rows)} 场 · val {len(val_rows)} 场\n", flush=True)

    base = base_rate(fit_rows)
    print(f"fit 段经验基准率 h/d/a = {base[0]:.4f}/{base[1]:.4f}/{base[2]:.4f}\n", flush=True)

    result = {"ranAt": "2026-10-09", "preReg": "fade-strategy-prereg v24.1",
              "n": {"fit": len(fit_rows), "val": len(val_rows)}, "baseRate": [round(x, 4) for x in base]}

    # ---- A1 γ 展平 ----
    print("── A1) γ 展平扫描（信息性）──", flush=True)
    print(f"{'γ':<8}{'fit LL':<13}{'val LL':<13}{'val RPS'}", flush=True)
    a1 = {}
    for g in GAMMA_GRID:
        fl, _, _ = losses(fit_rows, lambda m, k, g=g: t_gamma(m, k, g))
        vl, vr, _ = losses(val_rows, lambda m, k, g=g: t_gamma(m, k, g))
        a1[f"{g:.2f}"] = {"fitLL": round(fl, 6), "valLL": round(vl, 6), "valRPS": round(vr, 6)}
        print(f"{g:<8.2f}{fl:<13.6f}{vl:<13.6f}{vr:.6f}", flush=True)
    g_best = min(GAMMA_GRID, key=lambda g: a1[f"{g:.2f}"]["fitLL"])
    print(f"  → fit 最优 γ={g_best:.2f}（val LL {a1[f'{g_best:.2f}']['valLL']:.6f} vs γ=1.00 "
          f"{a1['1.00']['valLL']:.6f}）\n", flush=True)

    # ---- A2 基准率收缩 ----
    print("── A2) 向基准率收缩扫描（信息性）──", flush=True)
    print(f"{'w':<8}{'fit LL':<13}{'val LL':<13}{'val RPS'}", flush=True)
    a2 = {}
    for w in W_GRID:
        fl, _, _ = losses(fit_rows, lambda m, k, w=w: t_base(m, k, w, base))
        vl, vr, _ = losses(val_rows, lambda m, k, w=w: t_base(m, k, w, base))
        a2[f"{w:.1f}"] = {"fitLL": round(fl, 6), "valLL": round(vl, 6), "valRPS": round(vr, 6)}
        print(f"{w:<8.1f}{fl:<13.6f}{vl:<13.6f}{vr:.6f}", flush=True)
    w_best = min(W_GRID, key=lambda w: a2[f"{w:.1f}"]["fitLL"])
    print(f"  → fit 最优 w={w_best:.1f}（val LL {a2[f'{w_best:.1f}']['valLL']:.6f} vs w=0.0 "
          f"{a2['0.0']['valLL']:.6f}）\n", flush=True)

    # ---- A3 对数意见池（主判据）----
    print("── A3) 对数意见池 q∝p_model^a·p_mkt（主判据）──", flush=True)
    print(f"{'a':<8}{'fit LL':<13}{'val LL':<13}{'val RPS'}", flush=True)
    a3 = {}
    for a in A_GRID:
        fl, _, _ = losses(fit_rows, lambda m, k, a=a: t_pool(m, k, a))
        vl, vr, _ = losses(val_rows, lambda m, k, a=a: t_pool(m, k, a))
        a3[f"{a:.2f}"] = {"fitLL": round(fl, 6), "valLL": round(vl, 6), "valRPS": round(vr, 6)}
        print(f"{a:<8.2f}{fl:<13.6f}{vl:<13.6f}{vr:.6f}", flush=True)
    a_best = min(A_GRID, key=lambda a: a3[f"{a:.2f}"]["fitLL"])

    # val 段主判据对拍：a* vs a=0（纯市场）
    _, _, ll_cand = losses(val_rows, lambda m, k: t_pool(m, k, a_best))
    _, _, ll_mkt = losses(val_rows, lambda m, k: t_pool(m, k, 0.0))
    diffs = [mkt - cand for mkt, cand in zip(ll_mkt, ll_cand)]
    ci = boot_ci(diffs)
    mean_mkt = sum(ll_mkt) / len(ll_mkt)
    mean_cand = sum(ll_cand) / len(ll_cand)
    improve = (mean_mkt - mean_cand) / mean_mkt
    rps_mkt = a3["0.00"]["valRPS"]
    rps_cand = a3[f"{a_best:.2f}"]["valRPS"]
    rps_improve = (rps_mkt - rps_cand) / rps_mkt

    print(f"\n  fit 最优 a* = {a_best:.2f}", flush=True)
    print(f"  val log-loss: 纯市场(a=0) {mean_mkt:.6f} → a* {mean_cand:.6f}（改善 {improve*100:+.2f}%）", flush=True)
    print(f"  val RPS:      纯市场 {rps_mkt:.6f} → a* {rps_cand:.6f}（改善 {rps_improve*100:+.2f}%）", flush=True)
    print(f"  逐场配对 bootstrap 95%CI（市场−候选·>0=候选更优）: [{ci[0]:+.6f}, {ci[1]:+.6f}]", flush=True)

    crit_improve = improve >= IMPROVE_FLOOR
    crit_ci = ci[0] > 0
    crit_rps = rps_improve > 0
    passed = crit_improve and crit_ci
    verdict = ("实力链HAD对市场有增量——融合权重 a=" + f"{a_best:.2f}" + " 候选转正(另立上线预注册)"
               if passed else
               "HAD方向端对市场无增量——提准主线转比分端/伤停轨（判据③）")
    print(f"\n  ②改善≥1%: {'过' if crit_improve else '不过'}  CI下限>0: {'过' if crit_ci else '不过'}"
          f"  ④RPS同向: {'过' if crit_rps else '不过'}", flush=True)
    print(f"\n══ 判定: {verdict} ══\n", flush=True)

    print_calib("A3 a* 下 val 段校准（候选口径分带）",
                calib([(t_pool(m, k, a_best), k, o, e, d) for m, k, o, e, d in val_rows], "model"))

    result.update({
        "a1Gamma": a1, "a1BestFit": g_best,
        "a2BaseShrink": a2, "a2BestFit": w_best,
        "a3Pool": a3, "a3BestFit": a_best,
        "mainTest": {"valLLMarket": round(mean_mkt, 6), "valLLCand": round(mean_cand, 6),
                     "improve": round(improve, 6), "ci95": ci,
                     "valRPSMarket": rps_mkt, "valRPSCand": rps_cand,
                     "rpsImprove": round(rps_improve, 6)},
        "criteria": {"improveOver1pct": crit_improve, "ciLowerPositive": crit_ci,
                     "rpsSameDirection": crit_rps},
        "verdict": verdict,
    })
    OUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"归档 {OUT_PATH.relative_to(ROOT)}", flush=True)


if __name__ == "__main__":
    main()
