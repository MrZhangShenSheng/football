# -*- coding: utf-8 -*-
"""v24.2：比分端 模型 vs 市场 正面对撞（判据预注册于 fade-strategy-prereg v24.2）。

缺口：方向端 v24.1 已定论 a*=0（模型零增量）。比分端是实力链本业（V3W-v2 两段
回测 +6.5%/+7.2% 转正曾在此），却从未与市场正面量过——v20.1 只量模型自身校准、
v23 按市场带分组（已被 v24 证为回归伪影口径）。本实验补这一刀。

口径（跑前写死）：
  · 逐场取体彩在售 CRS 格集；真果须落在格集内（否则该场剔除——双方同支撑集公平对拍）
  · 模型 p 与市场 1/odds 各自在该格集上归一（devig 按场归一·同 v23）
  · 在 realized 格上算 log-loss（多分类·格数逐场不同·如实标注）

判据：
  ① 主判据：val 段逐场配对 bootstrap（2000次·种子20261009）模型 vs 市场 CI
  ② 模型优且 CI 下限 >0 → 比分端 α 源实锤（进 Phase2 定位 +EV 格）
  ③ 劣于市场 → 自建模型全端（方向+比分）对市场无增量定论·唯余伤停轨
  ④ 同报对数意见池 a 扫描：a* 为内点 >0 亦证模型含市场未含信息（融合有价值）
  ⑤ 按市场隐含带分报配对差（定位模型强于市场的价格段）
  ⑥ Brier / top1 命中率同报（不作独立判据）

数据：v2 缓存 841 日（cells = 体彩在售格·odds+模型 p·real 真果）
产出：data/04-summaries/v24_2-crs-vs-market.json
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

ROOT = Path(__file__).resolve().parents[3]
OUT_PATH = ROOT / "data" / "04-summaries" / "v24_2-crs-vs-market.json"

A_GRID = tuple(round(0.05 * i, 2) for i in range(21))      # 0.00 ~ 1.00
BOOT_N = 2000
SEED = 20261009
IMPLIED_BANDS = ((0.0, 0.02, "<2%"), (0.02, 0.05, "2-5%"), (0.05, 0.10, "5-10%"),
                 (0.10, 0.20, "10-20%"), (0.20, 1.01, ">=20%"))


def build(pre, window):
    """→ [(p_model_norm, p_mkt_norm, idx_real, n_cells)]（逐场·同支撑集）。"""
    lo, hi = window
    out = []
    for day, cands in pre.items():
        if not (lo <= day <= hi):
            continue
        for c in cands:
            cells = c.get("cells") or []
            if len(cells) < 3:
                continue
            idx = next((i for i, x in enumerate(cells) if x["mk"] == c["real"]), None)
            if idx is None:
                continue                      # 真果不在在售格集 → 剔除（双方同条件）
            pm = [max(x["p"], 0.0) for x in cells]
            pk = [1.0 / x["odds"] for x in cells]
            sm, sk = sum(pm), sum(pk)
            if sm <= 0 or sk <= 0:
                continue
            out.append(([x / sm for x in pm], [x / sk for x in pk], idx, len(cells)))
    return out


def pool(pm, pk, a):
    q = [max(m, 1e-12) ** a * max(k, 1e-12) for m, k in zip(pm, pk)]
    s = sum(q)
    return [x / s for x in q]


def per_match(rows, which, a=None):
    """→ (逐场 log-loss 列表, 逐场 Brier 列表, top1 命中率)。"""
    lls, brs, hits = [], [], 0
    for pm, pk, idx, _ in rows:
        p = pm if which == "model" else (pk if which == "market" else pool(pm, pk, a))
        lls.append(-math.log(max(p[idx], 1e-12)))
        brs.append(sum((x - (1.0 if i == idx else 0.0)) ** 2 for i, x in enumerate(p)))
        if max(range(len(p)), key=lambda i: p[i]) == idx:
            hits += 1
    n = len(rows) or 1
    return lls, brs, hits / n


def boot_ci(diffs):
    """>0 = 模型更优（市场 loss − 模型 loss）。"""
    n = len(diffs)
    if n == 0:
        return [0.0, 0.0]
    rng = random.Random(SEED)
    means = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(BOOT_N))
    return [round(means[int(BOOT_N * 0.025)], 6), round(means[int(BOOT_N * 0.975)], 6)]


def mean(xs):
    return sum(xs) / len(xs) if xs else None


def main():
    print("══ v24.2 比分端 模型 vs 市场 正面对撞 ══\n", flush=True)
    print("预注册: fade-strategy-prereg v24.2（跑前写死）\n", flush=True)
    pre = load_pre()
    segs = {"fit": build(pre, FIT_WINDOW), "val": build(pre, VAL_WINDOW)}
    for s, r in segs.items():
        nc = mean([x[3] for x in r])
        print(f"{s} 段: {len(r)} 场（真果落在售格集内）· 平均格数 {nc:.1f}", flush=True)
    print(flush=True)

    result = {"ranAt": "2026-10-09", "preReg": "fade-strategy-prereg v24.2", "seg": {}}

    for seg, rows in segs.items():
        if not rows:
            continue
        print(f"════ {seg} 段 ════", flush=True)
        ll_m, br_m, hit_m = per_match(rows, "model")
        ll_k, br_k, hit_k = per_match(rows, "market")
        diffs = [k - m for k, m in zip(ll_k, ll_m)]
        ci = boot_ci(diffs)
        print(f"  log-loss: 模型 {mean(ll_m):.6f} ｜ 市场 {mean(ll_k):.6f}"
              f" → 差 {mean(ll_k)-mean(ll_m):+.6f}（>0=模型更优）", flush=True)
        print(f"  配对 bootstrap 95%CI: [{ci[0]:+.6f}, {ci[1]:+.6f}]", flush=True)
        print(f"  Brier:    模型 {mean(br_m):.6f} ｜ 市场 {mean(br_k):.6f}", flush=True)
        print(f"  top1命中: 模型 {hit_m*100:.2f}% ｜ 市场 {hit_k*100:.2f}%", flush=True)
        result["seg"][seg] = {
            "n": len(rows), "meanCells": round(mean([x[3] for x in rows]), 2),
            "llModel": round(mean(ll_m), 6), "llMarket": round(mean(ll_k), 6),
            "llDiff": round(mean(ll_k) - mean(ll_m), 6), "ci95": ci,
            "brierModel": round(mean(br_m), 6), "brierMarket": round(mean(br_k), 6),
            "top1Model": round(hit_m, 6), "top1Market": round(hit_k, 6)}

        # ⑤ 按市场隐含带分报配对差
        print(f"\n  ⑤ 按市场隐含带（真果格的市场隐含概率）分报:", flush=True)
        print(f"  {'带':<10}{'n':<8}{'模型LL':<12}{'市场LL':<12}{'差'}", flush=True)
        bands = []
        for lo, hi, name in IMPLIED_BANDS:
            sel = [i for i, (_, pk, idx, _) in enumerate(rows) if lo <= pk[idx] < hi]
            if len(sel) < 50:
                continue
            lm, lk = mean([ll_m[i] for i in sel]), mean([ll_k[i] for i in sel])
            bands.append({"band": name, "n": len(sel), "llModel": round(lm, 6),
                          "llMarket": round(lk, 6), "diff": round(lk - lm, 6)})
            print(f"  {name:<10}{len(sel):<8}{lm:<12.6f}{lk:<12.6f}{lk-lm:+.6f}", flush=True)
        result["seg"][seg]["byImpliedBand"] = bands
        print(flush=True)

    # ④ 对数意见池 a 扫描（fit 标定 → val 验证）
    print("════ ④ 对数意见池 q∝p_model^a·p_mkt ════", flush=True)
    print(f"{'a':<8}{'fit LL':<14}{'val LL'}", flush=True)
    scan = {}
    for a in A_GRID:
        fl = mean(per_match(segs["fit"], "pool", a)[0])
        vl = mean(per_match(segs["val"], "pool", a)[0])
        scan[f"{a:.2f}"] = {"fitLL": round(fl, 6), "valLL": round(vl, 6)}
        print(f"{a:<8.2f}{fl:<14.6f}{vl:.6f}", flush=True)
    a_best = min(A_GRID, key=lambda a: scan[f"{a:.2f}"]["fitLL"])
    ll_pool, _, _ = per_match(segs["val"], "pool", a_best)
    ll_mk0, _, _ = per_match(segs["val"], "market")
    ci_pool = boot_ci([k - p for k, p in zip(ll_mk0, ll_pool)])
    improve = (mean(ll_mk0) - mean(ll_pool)) / mean(ll_mk0)
    print(f"\n  fit 最优 a* = {a_best:.2f}（内点>0 即证模型含市场未含信息）", flush=True)
    print(f"  val: 纯市场 {mean(ll_mk0):.6f} → 池 a* {mean(ll_pool):.6f}"
          f"（改善 {improve*100:+.2f}%）", flush=True)
    print(f"  配对 CI: [{ci_pool[0]:+.6f}, {ci_pool[1]:+.6f}]", flush=True)

    v = result["seg"]["val"]
    model_wins = v["llDiff"] > 0 and v["ci95"][0] > 0
    pool_wins = a_best > 0 and improve >= 0.01 and ci_pool[0] > 0
    if model_wins:
        verdict = f"比分端模型优于市场（val 差 {v['llDiff']:+.6f}·CI下限 {v['ci95'][0]:+.6f}>0）——α源实锤·进Phase2定位+EV格"
    elif pool_wins:
        verdict = (f"模型单独更差但融合有价值（a*={a_best:.2f}·改善 {improve*100:+.2f}%·CI下限 "
                   f"{ci_pool[0]:+.6f}>0）——比分端融合候选转正另立上线预注册")
    else:
        verdict = ("自建模型全端（方向+比分）对市场无增量——判据③定论·唯余伤停轨（市场未含之新信息）"
                   f"｜比分端 val 差 {v['llDiff']:+.6f}·CI{v['ci95']}·池 a*={a_best:.2f}")
    print(f"\n══ 判定: {verdict} ══", flush=True)

    result["poolScan"] = scan
    result["poolBestFit"] = a_best
    result["poolVal"] = {"llMarket": round(mean(ll_mk0), 6), "llPool": round(mean(ll_pool), 6),
                         "improve": round(improve, 6), "ci95": ci_pool}
    result["criteria"] = {"modelBeatsMarket": model_wins, "poolAddsValue": pool_wins}
    result["verdict"] = verdict
    OUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"归档 {OUT_PATH.relative_to(ROOT)}", flush=True)


if __name__ == "__main__":
    main()
