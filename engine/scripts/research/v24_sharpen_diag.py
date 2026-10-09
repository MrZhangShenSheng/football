# -*- coding: utf-8 -*-
"""v24 诊断：模型概率分布锐度体检（纯信息性·无转正判据·不改任何生产参数）。

背景缺口：
  · v23 按「市场隐含带」分组发现模型 ≥55% 带低估 15.7pp / <10% 带高估 16.8pp，
    但按 X 分组比较 Y 存在回归伪影嫌疑——须按模型自己的概率分带复核才算实锤。
  · F9 查过模型自带口径（dev≤3pp）但 n=243 且随机30%切分（自标「弱验证·非时间外推」）。
  本诊断用 v2 缓存（841日）两段时间切分补齐：fit 段标定 γ·val 段时间外验证。

五项（跑前写死·纯诊断不作转正判据，发现方向另立预注册实验）：
  A) 模型自带分带校准（核心补缺）：按模型概率值域分带 → n/meanP/realized/dev
  B) 市场隐含带同表（复刻 v23 口径）→ 双口径并列判伪影
  C) 锐度标量：模型三向概率 std vs 市场 devig std（扁平的最直接一行）
  D) γ 幂锐化扫描（p^γ 归一·γ>1=锐化）：fit 段选最优 γ → val 段时间外验证
     log-loss / RPS 双指标 + 市场 devig 基准同报（记忆 model-vs-market-baseline 纪律）
  E) λ 压缩检验（代理口径）：OLS 实际净胜球 ~ 模型期望净胜球·斜率>1=λ被压缩
     ——分辨「输出端锐化贴补丁」与「λ分化根因修复」

数据：engine/cache/strength_chain/pre_days_cache.pkl（v2·HAD三向模型+体彩赔率+真果）
产出：data/04-summaries/v24-sharpen-diag.json
开发者 sszhang
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from v11_s4_recalib import FIT_WINDOW, VAL_WINDOW
from v21_shape_policy import load_pre

ROOT = Path(__file__).resolve().parents[3]
OUT_PATH = ROOT / "data" / "04-summaries" / "v24-sharpen-diag.json"

DIRS = ("h", "d", "a")
BANDS = ((0.0, 0.10, "<10%"), (0.10, 0.20, "10-20%"), (0.20, 0.35, "20-35%"),
         (0.35, 0.55, "35-55%"), (0.55, 1.01, ">=55%"))
GAMMA_GRID = tuple(round(1.0 + 0.1 * i, 1) for i in range(16))   # 1.0 ~ 2.5
MIN_BAND_N = 50


def real_dir(real: str) -> int | None:
    """比分键 → 方向索引（0=主 1=平 2=客）。三其他键 s1sh/s1sd/s1sa 直读。"""
    r = str(real)
    if r.startswith("s1s"):
        return {"s1sh": 0, "s1sd": 1, "s1sa": 2}.get(r)
    if len(r) >= 6 and r[1:3].isdigit() and r[4:6].isdigit():
        g, t = int(r[1:3]), int(r[4:6])
        return 0 if g > t else (1 if g == t else 2)
    return None


def real_diff(real: str) -> int | None:
    """比分键 → 净胜球（三其他键无精确值·返回 None 不入 E 项回归）。"""
    r = str(real)
    if len(r) >= 6 and r[1:3].isdigit() and r[4:6].isdigit():
        return int(r[1:3]) - int(r[4:6])
    return None


def load_rows(pre, window):
    """→ [(p_model3, p_mkt3, outcome, exp_diff|None, real_diff|None)]（概率均已归一）。"""
    lo, hi = window
    rows = []
    for day, cands in pre.items():
        if not (lo <= day <= hi):
            continue
        for c in cands:
            hist, model = c.get("hadHist"), c.get("hadModel") or {}
            if not hist or not all(isinstance(hist.get(k), (int, float)) and hist[k] > 1.0 for k in DIRS):
                continue
            out = real_dir(c["real"])
            if out is None:
                continue
            mp = [model.get(k) for k in DIRS]
            if any(not isinstance(x, (int, float)) or x < 0 for x in mp):
                continue
            sm = sum(mp)
            if sm <= 0:
                continue
            inv = [1.0 / hist[k] for k in DIRS]
            si = sum(inv)
            # E 项代理：模型期望净胜球（cells 为体彩在售格·归一后算期望·口径限制如实）
            cells = c.get("cells") or []
            exp_d = None
            tot_p = sum(x["p"] for x in cells if real_diff(x["mk"]) is not None)
            if tot_p > 0:
                exp_d = sum(x["p"] * real_diff(x["mk"]) for x in cells
                            if real_diff(x["mk"]) is not None) / tot_p
            rows.append(([x / sm for x in mp], [x / si for x in inv], out,
                         exp_d, real_diff(c["real"])))
    return rows


def sharpen(p3, gamma):
    q = [max(x, 1e-12) ** gamma for x in p3]
    s = sum(q)
    return [x / s for x in q]


def log_loss(rows, gamma=1.0, use_market=False):
    tot = 0.0
    for mp, mk, out, _, _ in rows:
        p = mk if use_market else (mp if gamma == 1.0 else sharpen(mp, gamma))
        tot -= math.log(max(p[out], 1e-12))
    return tot / len(rows) if rows else None


def rps(rows, gamma=1.0, use_market=False):
    """三向有序 (h,d,a) 累积 RPS = 1/2·Σ_{i=1..2}(Σ_{j<=i}p − Σ_{j<=i}o)²。"""
    tot = 0.0
    for mp, mk, out, _, _ in rows:
        p = mk if use_market else (mp if gamma == 1.0 else sharpen(mp, gamma))
        obs = [1.0 if i == out else 0.0 for i in range(3)]
        cp = co = s = 0.0
        for i in range(2):
            cp += p[i]
            co += obs[i]
            s += (cp - co) ** 2
        tot += s / 2.0
    return tot / len(rows) if rows else None


def calib(rows, key, gamma=1.0):
    """key='model'|'market'：按该口径概率值域分带，带内 meanP vs realized。"""
    flat = []
    for mp, mk, out, _, _ in rows:
        p = mk if key == "market" else (mp if gamma == 1.0 else sharpen(mp, gamma))
        for i in range(3):
            flat.append((p[i], 1 if i == out else 0))
    table = []
    for lo, hi, name in BANDS:
        b = [x for x in flat if lo <= x[0] < hi]
        if len(b) < MIN_BAND_N:
            continue
        mean_p = sum(x[0] for x in b) / len(b)
        act = sum(x[1] for x in b) / len(b)
        table.append({"band": name, "n": len(b), "meanP": round(mean_p, 4),
                      "realized": round(act, 4), "dev": round(mean_p - act, 4)})
    return table


def spread(rows):
    """C 项：三向概率的组内标准差（扁平度标量）+ 极值均值。"""
    def stats(idx):
        sds, maxes = [], []
        for r in rows:
            p = r[idx]
            m = sum(p) / 3.0
            sds.append(math.sqrt(sum((x - m) ** 2 for x in p) / 3.0))
            maxes.append(max(p))
        n = len(sds) or 1
        return {"meanStd": round(sum(sds) / n, 4), "meanMaxP": round(sum(maxes) / n, 4)}
    return {"model": stats(0), "market": stats(1)}


def ols_slope(rows):
    """E 项：实际净胜球 ~ 模型期望净胜球 OLS 斜率（>1 = 模型λ差被压缩）。"""
    pts = [(r[3], r[4]) for r in rows if r[3] is not None and r[4] is not None]
    n = len(pts)
    if n < 100:
        return {"n": n, "slope": None, "note": "样本不足"}
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    den = sum((p[0] - mx) ** 2 for p in pts)
    if den <= 0:
        return {"n": n, "slope": None, "note": "自变量无方差"}
    slope = sum((p[0] - mx) * (p[1] - my) for p in pts) / den
    return {"n": n, "slope": round(slope, 4), "meanExp": round(mx, 4), "meanReal": round(my, 4),
            "note": "斜率>1=模型期望净胜球幅度不足(λ压缩)·<1=过度分化"}


def print_calib(title, table):
    print(f"── {title} ──", flush=True)
    print(f"{'带':<10}{'n':<9}{'预测':<9}{'实际':<9}{'dev'}", flush=True)
    for b in table:
        print(f"{b['band']:<10}{b['n']:<9}{b['meanP']:<9.4f}{b['realized']:<9.4f}{b['dev']:+.4f}", flush=True)
    print(flush=True)


def main():
    print("══ v24 诊断：模型概率分布锐度体检（纯信息性）══\n", flush=True)
    print("预注册: 脚本头跑前写死·五项 A/B/C/D/E·无转正判据\n", flush=True)
    pre = load_pre()
    print(f"缓存载入: {len(pre)}日\n", flush=True)

    seg_rows = {"fit": load_rows(pre, FIT_WINDOW), "val": load_rows(pre, VAL_WINDOW)}
    for seg, rows in seg_rows.items():
        print(f"{seg}段样本: {len(rows)} 场（{len(rows)*3} 腿）", flush=True)
    print(flush=True)

    result = {"ranAt": "2026-10-09", "preReg": "脚本头跑前写死·纯诊断", "seg": {}}

    # ---- A/B/C 两段分报 ----
    for seg, rows in seg_rows.items():
        if not rows:
            continue
        print(f"════ {seg} 段 ════\n", flush=True)
        a = calib(rows, "model")
        b = calib(rows, "market")
        print_calib(f"A) 模型自带分带校准（{seg}·核心补缺）", a)
        print_calib(f"B) 市场隐含带校准（{seg}·v23 口径）", b)
        sp = spread(rows)
        print(f"C) 锐度标量: 模型 std={sp['model']['meanStd']:.4f} maxP={sp['model']['meanMaxP']:.4f}"
              f" ｜ 市场 std={sp['market']['meanStd']:.4f} maxP={sp['market']['meanMaxP']:.4f}"
              f" → 模型/市场 std 比 {sp['model']['meanStd']/max(sp['market']['meanStd'],1e-9):.3f}\n", flush=True)
        result["seg"][seg] = {"n": len(rows), "calibModel": a, "calibMarket": b, "spread": sp,
                              "lambdaOLS": ols_slope(rows)}
        e = result["seg"][seg]["lambdaOLS"]
        print(f"E) λ压缩检验: n={e['n']} 斜率={e.get('slope')}（{e['note']}）\n", flush=True)

    # ---- D) γ 扫描：fit 标定 → val 时间外验证 ----
    fit_rows, val_rows = seg_rows["fit"], seg_rows["val"]
    print("════ D) γ 幂锐化扫描（fit 标定 → val 时间外验证）════\n", flush=True)
    print(f"{'γ':<7}{'fit logloss':<15}{'fit RPS':<13}{'val logloss':<15}{'val RPS'}", flush=True)
    scan = {}
    for g in GAMMA_GRID:
        fl, fr = log_loss(fit_rows, g), rps(fit_rows, g)
        vl, vr = log_loss(val_rows, g), rps(val_rows, g)
        scan[f"{g:.1f}"] = {"fitLL": round(fl, 6), "fitRPS": round(fr, 6),
                            "valLL": round(vl, 6), "valRPS": round(vr, 6)}
        print(f"{g:<7.1f}{fl:<15.6f}{fr:<13.6f}{vl:<15.6f}{vr:.6f}", flush=True)

    best_g = min(GAMMA_GRID, key=lambda g: scan[f"{g:.1f}"]["fitLL"])
    base = scan["1.0"]
    pick = scan[f"{best_g:.1f}"]
    mkt = {"fitLL": log_loss(fit_rows, use_market=True), "fitRPS": rps(fit_rows, use_market=True),
           "valLL": log_loss(val_rows, use_market=True), "valRPS": rps(val_rows, use_market=True)}

    ll_gain = (base["valLL"] - pick["valLL"]) / base["valLL"]
    rps_gain = (base["valRPS"] - pick["valRPS"]) / base["valRPS"]
    print(f"\nfit 段最优 γ = {best_g:.1f}", flush=True)
    print(f"  val 段 log-loss {base['valLL']:.6f} → {pick['valLL']:.6f}（改善 {ll_gain*100:+.2f}%）", flush=True)
    print(f"  val 段 RPS      {base['valRPS']:.6f} → {pick['valRPS']:.6f}（改善 {rps_gain*100:+.2f}%）", flush=True)
    print(f"\n市场 devig 基准（体彩·含抽水）:", flush=True)
    print(f"  val log-loss {mkt['valLL']:.6f} ｜ val RPS {mkt['valRPS']:.6f}", flush=True)
    print(f"  锐化后模型距市场: log-loss {pick['valLL']-mkt['valLL']:+.6f}"
          f" ｜ RPS {pick['valRPS']-mkt['valRPS']:+.6f}（>0=仍劣于市场）", flush=True)

    print("\n── A') 最优 γ 下模型自带校准（val 段）──", flush=True)
    a_sharp = calib(val_rows, "model", best_g)
    print_calib(f"val 段 γ={best_g:.1f} 后", a_sharp)

    result["gammaScan"] = scan
    result["bestGammaFit"] = best_g
    result["valGain"] = {"logloss": round(ll_gain, 6), "rps": round(rps_gain, 6)}
    result["marketBaseline"] = {k: round(v, 6) for k, v in mkt.items()}
    result["calibModelSharpened"] = a_sharp
    result["note"] = ("纯诊断·无转正判据。γ=1/T 幂锐化为单调变换→不改 argmax 选场选格（铁律13同构），"
                      "其决策价值在『下注与否』门槛而非『下注哪个』。拟上生产须另立预注册实验。")
    OUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"归档 {OUT_PATH.relative_to(ROOT)}", flush=True)


if __name__ == "__main__":
    main()
