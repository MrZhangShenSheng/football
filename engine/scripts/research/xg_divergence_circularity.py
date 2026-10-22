# engine/scripts/research/xg_divergence_circularity.py
"""t5 xG 背离循环嫌疑诊断：背离本身有信息，还是只是"总球多"的影子？

== 判据预注册（跑数前冻结·research/README 规矩2）==

背景：freq-tables.json 的 t5 表显示 xG 背离 P75 组总球>=4 的比率 57.9% vs 对照
30.3%（diff +0.276, CI下限 +0.183, n_on=140/559）。但 t5 的 band（总球>=4）与
背离定义 max(|实际进球-xG|)*FINISH_SCALE 可能不独立：进球多的场次，实际进球与
xG 的绝对差天然更大。若如此，t5 的"显著"是构造性的，与 t3 同类，不可用于校准。

三个判据（结果出来前写定，不看结果再挑）：

A. 分层检验（主判据）：按总进球数分层（tot=0,1,2,3,4,5,6+），层内看 P75 组与
   对照组的占比差。若背离只是总球的影子 → 层内 P75 占比应与层外一致（diff≈0），
   且 P75 组在高 tot 层富集。判定：若各 tot 层内 P75 组占比差的加权平均
   |Δ| < 0.05 且无单层 CI 离 0 → 判循环（背离无独立信息）。

B. 相关性量化：corr(背离, 总球) 的 Pearson r。
   判定：|r| >= 0.5 → 强共线，t5 不可独立解读；0.3 <= |r| < 0.5 → 中度，需配合 A 判；
   |r| < 0.3 → 弱，背离可能有独立信息。

C. 正交化对照：改用"xG 总和"做 band 前置的对照组 —— 在 xG 总和相近的场次内
   （|xg_tot - 2.6| < 0.5 窄带），P75 背离组 vs 对照组的总球>=4 比率差。
   若此差仍显著 > 0 → 背离在控制住"预期进球水平"后仍有信息（t5 可救）；
   若落入 CI 跨 0 → 背离无独立信息（t5 判废）。

报告口径：三判据全列，不择优。A 为主判据，C 为救援判据，B 为解释量。
任一判据判循环即 t5 不得进校准通道（即便 n_on 后续补过 150）。

开发者 sszhang
"""
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import quantiles

ROOT = Path(__file__).resolve().parents[3]
HIST_DIR = ROOT / "data" / "04-summaries" / "score-attribution" / "hist"

FINISH_SCALE = 0.5   # 与 attribution_dims.FINISH_SCALE 同值（背离取 offset 绝对值口径）
XG_BAND_CENTER, XG_BAND_HALF = 2.6, 0.5   # 判据C 窄带（联赛场均总球 ±0.5）


def load_e5_bills() -> list[dict]:
    """E5 在场的账单（= fd 提供 xG 的子集，与 freq_stats t5 同池）。"""
    bills = []
    for p in sorted(HIST_DIR.glob("*-attribution.json")):
        bills.extend(json.loads(p.read_text(encoding="utf-8")))
    out = []
    for b in bills:
        if "baseline" not in b:
            continue
        e5 = next((o for o in b.get("offsets", []) if o.get("dim") == "E5.finishing"), None)
        if e5 is None:
            continue
        # 背离度口径与 freq_stats._e5_divergence 一致
        div = max(abs(e5.get("h") or 0.0), abs(e5.get("a") or 0.0))
        # 反解 xG：offset = (实际-xG)*FINISH_SCALE ⟹ xG = 实际 - offset/scale
        hg, ag = b["result"]["hg"] or 0, b["result"]["ag"] or 0
        xg_h = hg - (e5["h"] / FINISH_SCALE)
        xg_a = ag - (e5["a"] / FINISH_SCALE)
        out.append({"div": div, "tot": hg + ag, "band": (hg + ag) >= 4,
                    "xg_tot": xg_h + xg_a, "league": b.get("league"),
                    "match": b.get("match"), "date": b.get("date")})
    return out


def _prop_diff_ci(k1: int, n1: int, k2: int, n2: int) -> tuple:
    """两比例差 + 不合并 SE 正态近似 95% CI（与 freq_stats._diff_ci_lo 同公式）。"""
    if n1 == 0 or n2 == 0:
        return None, None, None
    p1, p2 = k1 / n1, k2 / n2
    se = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
    d = p1 - p2
    return round(d, 4), round(d - 1.96 * se, 4), round(d + 1.96 * se, 4)


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return round(sxy / math.sqrt(sxx * syy), 4)


def main() -> None:
    rows = load_e5_bills()
    vals = sorted(r["div"] for r in rows)
    p75 = round(quantiles(vals, n=4, method="inclusive")[2], 4)
    for r in rows:
        r["hi"] = r["div"] >= p75

    hi = [r for r in rows if r["hi"]]
    lo = [r for r in rows if not r["hi"]]
    print(f"池 n={len(rows)} · P75 阈值={p75} · hi={len(hi)} lo={len(lo)}")
    d, clo, chi = _prop_diff_ci(sum(r['band'] for r in hi), len(hi),
                                sum(r['band'] for r in lo), len(lo))
    print(f"[复现 t5] diff={d} CI=[{clo}, {chi}]\n")

    # ── 判据 A：按总进球数分层，层内看 hi 组占比 ──
    print("=== 判据A 分层检验（层内 hi 组占比 vs 层外）===")
    print(f"{'tot':>5} {'n':>5} {'hi_n':>5} {'hi占比':>8} {'band率hi':>9} {'band率lo':>9}")
    by_tot = defaultdict(list)
    for r in rows:
        by_tot[min(r["tot"], 6)].append(r)
    hi_share = []
    for tot in sorted(by_tot):
        grp = by_tot[tot]
        g_hi = [r for r in grp if r["hi"]]
        g_lo = [r for r in grp if not r["hi"]]
        share = len(g_hi) / len(grp)
        hi_share.append((tot, len(grp), share))
        bh = f"{sum(r['band'] for r in g_hi)/len(g_hi):.3f}" if g_hi else "—"
        bl = f"{sum(r['band'] for r in g_lo)/len(g_lo):.3f}" if g_lo else "—"
        print(f"{tot:>5} {len(grp):>5} {len(g_hi):>5} {share:>8.3f} {bh:>9} {bl:>9}")
    base_share = len(hi) / len(rows)
    print(f"\n全池 hi 占比={base_share:.3f}（无共线则各层应接近此值）")
    print("层内 hi 占比偏离：", ", ".join(
        f"tot{t}:{s - base_share:+.3f}" for t, n, s in hi_share))

    # ── 判据 B：相关性 ──
    r_div_tot = _pearson([r["div"] for r in rows], [float(r["tot"]) for r in rows])
    r_div_xgt = _pearson([r["div"] for r in rows], [r["xg_tot"] for r in rows])
    print(f"\n=== 判据B 相关性 ===")
    print(f"corr(背离, 总球) r={r_div_tot}")
    print(f"corr(背离, xG总和) r={r_div_xgt}")

    # ── 判据 C：xG 总和窄带内的正交化对照 ──
    band_rows = [r for r in rows
                 if abs(r["xg_tot"] - XG_BAND_CENTER) < XG_BAND_HALF]
    b_hi = [r for r in band_rows if r["hi"]]
    b_lo = [r for r in band_rows if not r["hi"]]
    print(f"\n=== 判据C 正交化对照（xG总和∈{XG_BAND_CENTER}±{XG_BAND_HALF}）===")
    print(f"窄带 n={len(band_rows)} · hi={len(b_hi)} lo={len(b_lo)}")
    if b_hi and b_lo:
        kd, kclo, kchi = _prop_diff_ci(sum(r['band'] for r in b_hi), len(b_hi),
                                       sum(r['band'] for r in b_lo), len(b_lo))
        print(f"band率 hi={sum(r['band'] for r in b_hi)/len(b_hi):.4f} "
              f"lo={sum(r['band'] for r in b_lo)/len(b_lo):.4f}")
        print(f"diff={kd} CI=[{kclo}, {kchi}]  "
              f"→ {'CI跨0=背离无独立信息' if kclo is not None and kclo <= 0 else 'CI离0=背离有独立信息'}")
    else:
        print("窄带样本不足，判据C 无法执行（如实标注，不编造）")


if __name__ == "__main__":
    main()
