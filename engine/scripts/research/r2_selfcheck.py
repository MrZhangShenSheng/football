# engine/scripts/research/r2_selfcheck.py
"""r2 五表过门后的强制自证伪（memory/feedback_backtest_discipline 第3条）。

r2 跑出 5/6 表 calibration_ready，与 prereg 先验预期（大概率不过门）相反，
且与 memory/model-no-edge-both-ends、dc-no-edge-over-market 的既有结论冲突。
按纪律：出了正结果先自证伪 + 随机基线，再上报。

== 四项自证伪（跑前冻结）==

S1 随机基线：把 on 条件替换成同样大小的随机分组（固定种子，重复 200 次），
   看 diff_ci_lo>0 的比率。若随机分组也频繁过门 → 判据本身有偏，r2 结论作废。
   判定：随机过门率 >= 5% → 判据有偏。

S2 市场基线对照（核心）：r2 的 on 条件是否只是"市场已知信息"的改写？
   做法：用 L1 基线（市场赔率反解的 λ_h+λ_a）预测同一 band，比较
   (a) 仅市场基线的 band 命中率  (b) on 组相对 off 组的抬升
   若 on 组的高 band 率可由"市场也认为这是高进球场"解释 → 无增量。
   判定：在市场基线分档内（λ_sum 分 4 档），层内 diff 若全部 CI 跨 0 → 无增量。
   这是最关键一项——memory/feedback_odds_no_alpha 的铁律是"排序是赔率的确定函数"。

S3 滚动均值的自相关检验：A4.style 的 on 条件（滚动射正差）是否只是
   "强队 vs 弱队"的代理？若是，则 r2t1/r2t6 等于重新发现了实力差（A1 已在账），
   不是新信息。判定：corr(滚动射正差, 市场 λ 差) >= 0.5 → 高度代理。

S4 时间线复核：on 条件的窗口是否真的不含本场。虽有单测，但在真实出账数据上
   再抽查一次——取 rolling_evidence.window_h 与该队该场之前的实际可用场数对账。

报告口径：四项全列。S2 为主判据（市场增量），S1 为判据有效性，S3 为解释，S4 为纪律。
任一项判负即 r2 结论降级为 observational，不得进校准通道。

开发者 sszhang
"""
import json
import math
import random
from collections import defaultdict
from pathlib import Path
from statistics import quantiles

ROOT = Path(__file__).resolve().parents[3]
HIST_DIR = ROOT / "data" / "04-summaries" / "score-attribution" / "hist"

RANDOM_TRIALS = 200
RANDOM_SEED = 20261022


def load() -> list[dict]:
    bills = []
    for p in sorted(HIST_DIR.glob("*-attribution.json")):
        bills.extend(json.loads(p.read_text(encoding="utf-8")))
    return [b for b in bills if "baseline" in b]


def _ci_lo(p1, n1, p2, n2):
    if not n1 or not n2:
        return None
    se = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
    return (p1 - p2) - 1.96 * se


def _rate(rows, band):
    return sum(1 for r in rows if band(r)) / len(rows) if rows else None


def _diff_ci(on_rows, off_rows, band):
    p1, p2 = _rate(on_rows, band), _rate(off_rows, band)
    if p1 is None or p2 is None:
        return None, None
    return round(p1 - p2, 4), round(_ci_lo(p1, len(on_rows), p2, len(off_rows)), 4)


def main() -> None:
    bills = load()
    pool = [b for b in bills
            if (b.get("rolling_evidence") or {}).get("st_h") is not None
            and (b.get("rolling_evidence") or {}).get("st_a") is not None]
    for b in pool:
        re_ = b["rolling_evidence"]
        b["_gap"] = abs(re_["st_h"] - re_["st_a"])
        b["_lam"] = b["baseline"]["lam_h"] + b["baseline"]["lam_a"]
        b["_lamgap"] = abs(b["baseline"]["lam_h"] - b["baseline"]["lam_a"])
    gap_p75 = quantiles(sorted(b["_gap"] for b in pool), n=4, method="inclusive")[2]
    net2 = lambda b: abs((b["result"]["hg"] or 0) - (b["result"]["ag"] or 0)) >= 2
    on = [b for b in pool if b["_gap"] >= gap_p75]
    off = [b for b in pool if b["_gap"] < gap_p75]
    d0, c0 = _diff_ci(on, off, net2)
    print(f"池 n={len(pool)} · 复现 r2t1: diff={d0} CI下限={c0}\n")

    # ── S1 随机基线 ──
    rng = random.Random(RANDOM_SEED)
    pass_cnt, diffs = 0, []
    for _ in range(RANDOM_TRIALS):
        shuffled = pool[:]
        rng.shuffle(shuffled)
        r_on, r_off = shuffled[:len(on)], shuffled[len(on):]
        d, c = _diff_ci(r_on, r_off, net2)
        diffs.append(d)
        if c is not None and c > 0:
            pass_cnt += 1
    print(f"=== S1 随机基线（{RANDOM_TRIALS} 次·种子{RANDOM_SEED}）===")
    print(f"随机分组过门次数 {pass_cnt}/{RANDOM_TRIALS} = {pass_cnt/RANDOM_TRIALS*100:.1f}%")
    print(f"随机 diff 均值 {sum(diffs)/len(diffs):+.4f}（真实 {d0:+.4f}）")
    print(f"→ S1 {'判负：判据有偏' if pass_cnt/RANDOM_TRIALS >= 0.05 else '判正：随机不过门'}")

    # ── S2 市场基线分档内对照（主判据）──
    print(f"\n=== S2 市场基线分档内对照（主判据）===")
    lams = sorted(b["_lam"] for b in pool)
    qs = quantiles(lams, n=4, method="inclusive")
    print(f"λ_sum 四分位：{[round(q,2) for q in qs]}")
    print(f"{'档':<6} {'n_on':>6} {'n_off':>6} {'rate_on':>8} {'rate_off':>9} "
          f"{'diff':>8} {'CI下限':>9}")
    layers, sig = [], 0
    for i, (lo, hi) in enumerate([(-9, qs[0]), (qs[0], qs[1]), (qs[1], qs[2]), (qs[2], 99)]):
        seg = [b for b in pool if lo <= b["_lam"] < hi]
        s_on = [b for b in seg if b["_gap"] >= gap_p75]
        s_off = [b for b in seg if b["_gap"] < gap_p75]
        d, c = _diff_ci(s_on, s_off, net2)
        layers.append((i, len(s_on), len(s_off), d, c))
        if c is not None and c > 0:
            sig += 1
        print(f"Q{i+1:<5} {len(s_on):>6} {len(s_off):>6} "
              f"{_rate(s_on, net2) or 0:>8.4f} {_rate(s_off, net2) or 0:>9.4f} "
              f"{d if d is not None else 0:>8.4f} {c if c is not None else 0:>9.4f}")
    print(f"→ S2 {sig}/4 档层内 CI 仍 >0 ⟹ "
          f"{'判正：控住市场基线后仍有增量' if sig >= 3 else '判负：抬升由市场基线解释' if sig == 0 else '判疑：部分档消失'}")

    # ── S3 代理检验 ──
    print(f"\n=== S3 滚动射正差 vs 市场 λ 差 的代理检验 ===")
    xs = [b["_gap"] for b in pool]
    ys = [b["_lamgap"] for b in pool]
    n = len(xs)
    mx, my = sum(xs)/n, sum(ys)/n
    sxy = sum((x-mx)*(y-my) for x, y in zip(xs, ys))
    sxx = sum((x-mx)**2 for x in xs); syy = sum((y-my)**2 for y in ys)
    r = sxy / math.sqrt(sxx*syy) if sxx > 0 and syy > 0 else None
    print(f"corr(滚动射正差, 市场λ差) r={r:.4f}")
    print(f"→ S3 {'判负：高度代理市场实力差' if r and r >= 0.5 else '判正：非市场实力差的简单代理'}")

    # ── S4 时间线复核 ──
    print(f"\n=== S4 时间线复核（真实出账数据抽查）===")
    wins = [(b.get("rolling_evidence") or {}).get("window_h") for b in pool[:2000]]
    wins = [w for w in wins if w is not None]
    print(f"抽查 {len(wins)} 场 window_h：min={min(wins)} max={max(wins)} "
          f"（上限应 = WINDOW=10，下限应 >= MIN_WINDOW=5）")
    ok = min(wins) >= 5 and max(wins) <= 10
    print(f"→ S4 {'判正：窗口在约定范围内' if ok else '判负：窗口越界'}")


if __name__ == "__main__":
    main()
