"""① 概率校准层（isotonic/PAVA · 三梯队一期 · docs/2026-09-27-probability-modernization-design.html）。

问题（E1）：trend 校准图 0~40% 桶实际 59% vs 预测 38%——系统性低估 22pp，
整套下游（星级/EV/串关连乘）建立在未校准概率上。

方案：对 corpus 已结算腿的 (p̂=p_final[选定方向], hit=directionHit) 做 PAVA
保序回归（自实现不引 sklearn），输出分段线性映射表 isotonic.json。
本期只落盘映射表+报告，不动生产链（观察一个回填周期后按消融纪律决定接线）。

验证口径：按日期排序前 80% 拟合 / 后 20% 时间外验证（OOT）；
reliability 10 桶对比校准前后 |预测-实际| 偏差（目标低桶 22pp→<10pp）。

用法：
  python calibrate_isotonic.py           # 全流程：拟合+OOT+落盘报告
  python calibrate_isotonic.py --selftest
开发者 sszhang
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "engine" / "cache" / "isotonic.json"
REPORT = ROOT / "data" / "04-summaries" / "isotonic-report.json"

DIR_KEY = {"主": 0, "平": 1, "客": 2}


def pava(xs: list[float], ys: list[float]) -> list[float]:
    """Pool Adjacent Violators：输入按 x 升序的 (x,y)，返回单调不减拟合值。

    经典实现：栈式块合并，违反单调(y[i]>y[i+1] 且需非降)则加权合并。
    selftest 用分段真值+噪声验证。开发者 sszhang
    """
    blocks = [[y, 1] for y in ys]  # [块均值, 块权重]
    i = 0
    while i < len(blocks) - 1:
        if blocks[i][0] > blocks[i + 1][0] + 1e-12:  # 违反非降 → 合并
            w = blocks[i][1] + blocks[i + 1][1]
            m = (blocks[i][0] * blocks[i][1] + blocks[i + 1][0] * blocks[i + 1][1]) / w
            blocks[i] = [m, w]
            del blocks[i + 1]
            if i > 0:
                i -= 1  # 合并可能引发新的违反，回看
        else:
            i += 1
    out: list[float] = []
    for mean, w in blocks:
        out.extend([mean] * w)
    return out


def pick_dir_index(pick: str) -> int | None:
    """pick 字符串 → 三向下标（主胜/平/客胜；让球前缀忽略）。"""
    for key, idx in DIR_KEY.items():
        if key in pick:
            return idx
    return None


def load_pairs() -> list[dict]:
    """corpus → [(date, p̂, hit)]，剔除方向不可解析记录。"""
    corpus = json.loads((ROOT / "data" / "04-summaries" / "corpus.json").read_text(encoding="utf-8"))
    pairs = []
    for r in corpus.get("records", []):
        pf, hit, pick = r.get("p_final"), r.get("directionHit"), str(r.get("pick") or "")
        if not pf or hit is None:
            continue
        idx = pick_dir_index(pick)
        if idx is None:
            continue
        pairs.append({"date": r.get("date", ""), "p": float(pf[idx]), "hit": bool(hit)})
    pairs.sort(key=lambda x: x["date"])  # 时间升序（OOT 划分依据）
    return pairs


def reliability(ps: list[float], hits: list[bool], n_bins: int = 10) -> list[dict]:
    """10 桶 reliability：每桶预测均值 vs 实际命中率。"""
    out = []
    for b in range(n_bins):
        lo, hi = b / n_bins, (b + 1) / n_bins
        sel = [(p, h) for p, h in zip(ps, hits) if lo <= p < hi or (b == n_bins - 1 and p == 1.0)]
        if not sel:
            continue
        bp = sum(p for p, _ in sel) / len(sel)
        bh = sum(1 for _, h in sel if h) / len(sel)
        out.append({"bin": f"[{lo:.1f},{hi:.1f})", "n": len(sel),
                    "predMean": round(bp, 3), "actRate": round(bh, 3),
                    "gapPp": round((bh - bp) * 100, 1)})
    return out


def fit_map(ps: list[float], hits: list[float]) -> list[dict]:
    """(p,hit) → PAVA → 分段映射表 [{x0,x1,y,n}]。

    段值加 Beta(1,1) 平滑 y=(Σhit+1)/(n+2)：防 train 高段全中把 OOT 推到 1.00
    （首版实测 0.82-0.90 段 y=1.00 · OOT act=0.77 的边界过拟合），并 clamp
    [0.02, 0.98]——没有任何足球事件是 100%。开发者 sszhang
    """
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    xs = [ps[i] for i in order]
    ys = [hits[i] for i in order]
    fit = pava(xs, ys)
    segs = []
    i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and abs(fit[j + 1] - fit[i]) < 1e-9:
            j += 1
        n = j - i + 1
        s = sum(ys[i:j + 1])
        y = max(0.02, min(0.98, (s + 1) / (n + 2)))  # Beta(1,1) 平滑
        segs.append({"x0": xs[i], "x1": xs[j], "y": round(y, 4), "n": n})
        i = j + 1
    return segs


def apply_map(segs: list[dict], p: float) -> float:
    for s in segs:
        if s["x0"] <= p <= s["x1"]:
            return s["y"]
    return segs[-1]["y"] if segs else p


def main() -> None:
    if "--selftest" in sys.argv:
        _selftest()
        return
    pairs = load_pairs()
    n = len(pairs)
    cut = int(n * 0.8)
    train, oot = pairs[:cut], pairs[cut:]
    print(f"[isotonic] 样本 {n}（train {len(train)} / OOT {len(oot)}）")

    tp = [x["p"] for x in train]
    th = [1.0 if x["hit"] else 0.0 for x in train]
    segs = fit_map(tp, th)
    print(f"[isotonic] 映射表 {len(segs)} 段: " +
          " ".join(f"{s['x0']:.2f}-{s['x1']:.2f}→{s['y']:.2f}(n{s['n']})" for s in segs))

    # OOT 验证：校准前后 reliability 对比
    op = [x["p"] for x in oot]
    oh = [x["hit"] for x in oot]
    rel_before = reliability(op, oh)
    cp = [apply_map(segs, p) for p in op]
    rel_after = reliability(cp, oh)
    gap_before = max(abs(r["gapPp"]) for r in rel_before if r["n"] >= 5) if rel_before else None
    gap_after = max(abs(r["gapPp"]) for r in rel_after if r["n"] >= 5) if rel_after else None
    print(f"[isotonic] OOT 最大桶偏差: 校准前 {gap_before}pp → 校准后 {gap_after}pp")

    CACHE.write_text(json.dumps({
        "fittedAt": pairs[-1]["date"] if pairs else None, "nTrain": len(train),
        "segments": segs,
        "note": "一期只落盘观察不接生产链（docs/2026-09-27 概率底座现代化设计§①）",
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    REPORT.write_text(json.dumps({
        "at": "2026-09-27", "n": n, "nTrain": len(train), "nOot": len(oot),
        "ootReliabilityBefore": rel_before, "ootReliabilityAfter": rel_after,
        "maxBinGapPp": {"before": gap_before, "after": gap_after},
        "segments": segs,
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"[isotonic] → {CACHE.relative_to(ROOT)}")
    print(f"[isotonic] → {REPORT.relative_to(ROOT)}")


def _selftest() -> None:
    # 1) PAVA 单调性：随机数据拟合后非降
    import random
    random.seed(42)
    xs = sorted(random.random() for _ in range(200))
    ys = [0.3 if x < 0.5 else 0.7 for x in xs]
    ys = [y + random.gauss(0, 0.15) for y in ys]
    ys = [max(0.0, min(1.0, y)) for y in ys]
    fit = pava(xs, ys)
    assert all(fit[i] <= fit[i + 1] + 1e-9 for i in range(len(fit) - 1)), "PAVA 非单调"
    assert abs(fit[0] - 0.3) < 0.05 and abs(fit[-1] - 0.7) < 0.05, "PAVA 真值恢复失败"
    # 2) 已校准数据（p=hit 频率）应近似恒等
    xs2 = [i / 100 for i in range(100)]
    ys2 = [max(0.0, min(1.0, i / 100 + random.gauss(0, 0.01))) for i in range(100)]
    fit2 = pava(xs2, ys2)
    assert max(abs(a - b) for a, b in zip(fit2, ys2)) < 0.02, "恒等场景失真"
    # 3) pick 解析
    assert pick_dir_index("主胜(方案外)") == 0
    assert pick_dir_index("让-2客胜") == 2
    assert pick_dir_index("平") == 1
    print("[isotonic] selftest ✅ PAVA单调+真值恢复+恒等+pick解析")


if __name__ == "__main__":
    main()
