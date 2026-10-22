# engine/scripts/research/r2_market_increment.py
"""r2 过门后的决定性检验：对市场的概率增量（而非频次差）。

== 为什么 S2 还不够 ==

r2_selfcheck 的 S2 在 λ_sum 四分档内看频次差，3/4 档存活。但：
  1. 分档是粗粒度控制，档内 λ 仍有跨度（Q2 档 λ∈[2.54,2.75)），
     滚动射正差可能仍在捡档内残余的 λ 信息。
  2. memory/feedback_odds_no_alpha 与 model-vs-market-baseline 的既有判据是
     log-loss / RPS 对市场基准的增量，不是频次差。频次差显著 ≠ 概率预测更准。
     这正是 memory/false-positive-roi-two-causes 记录的那类警讯。

== 判据（跑前冻结）==

M1 对数损失增量（主判据）：
   基准模型 = 仅市场（λ_sum 经 Poisson 独立近似给出 P(band)），
   对照模型 = 市场 + 滚动射正差（logistic 单变量增量回归，样本内）。
   报 log-loss 差与 Brier 差。判定：增量 < 0.001（相对基准 <0.5%）→ 无实质增量。

M2 样本外时间切分：按日期排序，前 70% 拟合，后 30% 评估（不回头看）。
   样本内改善可能是过拟合；样本外是唯一可信口径。
   判定：样本外 log-loss 改善 <= 0 → 判负，r2 不得进校准通道。

M3 连续控制（替代 S2 的分档）：把 λ_sum 作为连续变量与滚动射正差一同进回归，
   看后者系数的 t 值。判定：|t| < 2 → 控住连续 λ 后无独立贡献。

报告口径：M1/M2/M3 全列，M2 为决定性判据（样本外）。
任一判负 → r2 五表降级 observational。

实现说明：手写 logistic（梯度下降），不引第三方依赖，与本仓既有研究脚本一致。

开发者 sszhang
"""
import json
import math
from pathlib import Path
from statistics import quantiles

ROOT = Path(__file__).resolve().parents[3]
HIST_DIR = ROOT / "data" / "04-summaries" / "score-attribution" / "hist"

NEWTON_ITERS = 40     # IRLS/牛顿法迭代（纯 Python 梯度下降 4000 轮过慢，改二阶收敛）
TRAIN_FRAC = 0.70


def load():
    bills = []
    for p in sorted(HIST_DIR.glob("*-attribution.json")):
        bills.extend(json.loads(p.read_text(encoding="utf-8")))
    out = []
    for b in bills:
        if "baseline" not in b:
            continue
        re_ = b.get("rolling_evidence") or {}
        if re_.get("st_h") is None or re_.get("st_a") is None:
            continue
        lam = b["baseline"]["lam_h"] + b["baseline"]["lam_a"]
        out.append({
            "date": b["date"],
            "gap": abs(re_["st_h"] - re_["st_a"]),
            "lam": lam,
            "lamgap": abs(b["baseline"]["lam_h"] - b["baseline"]["lam_a"]),
            "y": 1.0 if abs((b["result"]["hg"] or 0) - (b["result"]["ag"] or 0)) >= 2 else 0.0,
        })
    out.sort(key=lambda r: r["date"])
    return out


def _sig(z):
    if z < -30: return 1e-13
    if z > 30:  return 1 - 1e-13
    return 1 / (1 + math.exp(-z))


def _solve(A, b):
    """高斯消元解 A x = b（小矩阵·含偏置最多 4x4）。"""
    k = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(k):
        piv = max(range(c, k), key=lambda r: abs(M[r][c]))
        if abs(M[piv][c]) < 1e-12:
            return None
        M[c], M[piv] = M[piv], M[c]
        for r in range(k):
            if r == c:
                continue
            f = M[r][c] / M[c][c]
            for cc in range(c, k + 1):
                M[r][cc] -= f * M[c][cc]
    return [M[i][k] / M[i][i] for i in range(k)]


def fit(rows, feats):
    """logistic 牛顿法（IRLS）；返回 (权重, 偏置, 均值, 标准差)。"""
    n = len(rows)
    mus = [sum(r[f] for r in rows) / n for f in feats]
    sds = [math.sqrt(max(sum((r[f] - mus[i])**2 for r in rows) / n, 1e-9))
           for i, f in enumerate(feats)]
    X = [[1.0] + [(r[f] - mus[i]) / sds[i] for i, f in enumerate(feats)] for r in rows]
    y = [r["y"] for r in rows]
    k = len(feats) + 1
    beta = [0.0] * k
    for _ in range(NEWTON_ITERS):
        g = [0.0] * k
        H = [[0.0] * k for _ in range(k)]
        for xi, yi in zip(X, y):
            p = _sig(sum(b * x for b, x in zip(beta, xi)))
            e, wgt = p - yi, max(p * (1 - p), 1e-9)
            for a in range(k):
                g[a] += e * xi[a]
                for b_ in range(a, k):
                    H[a][b_] += wgt * xi[a] * xi[b_]
        for a in range(k):
            for b_ in range(a):
                H[a][b_] = H[b_][a]
            H[a][a] += 1e-8   # 岭正则防奇异
        step = _solve(H, g)
        if step is None:
            break
        beta = [b - s for b, s in zip(beta, step)]
        if max(abs(s) for s in step) < 1e-9:
            break
    return beta[1:], beta[0], mus, sds


def predict(r, feats, w, b0, mus, sds):
    x = [(r[f] - mus[i]) / sds[i] for i, f in enumerate(feats)]
    return _sig(sum(wi * xi for wi, xi in zip(w, x)) + b0)


def metrics(rows, ps):
    ll = -sum(y * math.log(max(p, 1e-13)) + (1 - y) * math.log(max(1 - p, 1e-13))
              for y, p in zip((r["y"] for r in rows), ps)) / len(rows)
    br = sum((p - r["y"])**2 for r, p in zip(rows, ps)) / len(rows)
    return ll, br


def main():
    rows = load()
    print(f"池 n={len(rows)}（{rows[0]['date']} ~ {rows[-1]['date']}）")
    print(f"band 基础率 P(净胜>=2) = {sum(r['y'] for r in rows)/len(rows):.4f}\n")

    BASE = ["lam", "lamgap"]      # 仅市场信息
    FULL = ["lam", "lamgap", "gap"]  # 市场 + 滚动射正差

    # ── M1 样本内 ──
    print("=== M1 样本内增量 ===")
    wb, bb, mb, sb = fit(rows, BASE)
    wf, bf, mf, sf = fit(rows, FULL)
    p_b = [predict(r, BASE, wb, bb, mb, sb) for r in rows]
    p_f = [predict(r, FULL, wf, bf, mf, sf) for r in rows]
    ll_b, br_b = metrics(rows, p_b)
    ll_f, br_f = metrics(rows, p_f)
    print(f"仅市场      log-loss={ll_b:.5f} Brier={br_b:.5f}")
    print(f"市场+滚动   log-loss={ll_f:.5f} Brier={br_f:.5f}")
    print(f"改善        Δll={ll_b-ll_f:+.5f}（相对 {(ll_b-ll_f)/ll_b*100:+.2f}%）"
          f" ΔBrier={br_b-br_f:+.5f}")
    print(f"→ M1 {'判正：样本内有增量' if ll_b-ll_f >= 0.001 else '判负：增量 <0.001'}")

    # ── M2 样本外（决定性）──
    cut = int(len(rows) * TRAIN_FRAC)
    tr, te = rows[:cut], rows[cut:]
    print(f"\n=== M2 样本外时间切分（决定性判据）===")
    print(f"训练 {len(tr)} 场（~{tr[-1]['date']}）/ 测试 {len(te)} 场（{te[0]['date']}~）")
    wb2, bb2, mb2, sb2 = fit(tr, BASE)
    wf2, bf2, mf2, sf2 = fit(tr, FULL)
    pb2 = [predict(r, BASE, wb2, bb2, mb2, sb2) for r in te]
    pf2 = [predict(r, FULL, wf2, bf2, mf2, sf2) for r in te]
    ll_b2, br_b2 = metrics(te, pb2)
    ll_f2, br_f2 = metrics(te, pf2)
    print(f"仅市场      log-loss={ll_b2:.5f} Brier={br_b2:.5f}")
    print(f"市场+滚动   log-loss={ll_f2:.5f} Brier={br_f2:.5f}")
    d_out = ll_b2 - ll_f2
    print(f"改善        Δll={d_out:+.5f}（相对 {d_out/ll_b2*100:+.2f}%）"
          f" ΔBrier={br_b2-br_f2:+.5f}")
    print(f"→ M2 {'判正：样本外仍有改善' if d_out > 0 else '判负：样本外无改善⟹不得进校准通道'}")

    # ── M3 连续控制下的系数 t 值 ──
    print(f"\n=== M3 连续控制（λ 作连续变量）===")
    # 数值 Hessian 对角近似算 SE
    n = len(rows)
    mus = [sum(r[f] for r in rows)/n for f in FULL]
    sds = [math.sqrt(max(sum((r[f]-mus[i])**2 for r in rows)/n, 1e-9))
           for i, f in enumerate(FULL)]
    info = [0.0]*len(FULL)
    for r in rows:
        x = [(r[f]-mus[i])/sds[i] for i, f in enumerate(FULL)]
        p = predict(r, FULL, wf, bf, mf, sf)
        for i in range(len(FULL)):
            info[i] += p*(1-p)*x[i]*x[i]
    for i, f in enumerate(FULL):
        se = 1/math.sqrt(info[i]) if info[i] > 0 else float("inf")
        t = wf[i]/se if se else 0
        print(f"   {f:<8} 系数={wf[i]:+.4f} SE={se:.4f} t={t:+.2f}")
    i_gap = FULL.index("gap")
    se_gap = 1/math.sqrt(info[i_gap])
    t_gap = wf[i_gap]/se_gap
    print(f"→ M3 {'判正：控住连续λ后仍显著' if abs(t_gap) >= 2 else '判负：|t|<2'}")


if __name__ == "__main__":
    main()
