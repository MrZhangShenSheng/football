# engine/scripts/research/xg_divergence_retrospective.py
"""t5 追加检验：背离是不是赛前不可观测量？（循环嫌疑的根问题）

== 为什么要追加这一轮（前一轮判据设计缺陷的如实记录）==

xg_divergence_circularity.py 的三判据跑完是"背离有独立信息"（判据C：控住 xG 总和
后 diff=+0.385 CI[0.242,0.528]）。但那轮判据有两处设计缺陷，结论不可直接采信：

缺陷1（判据A 退化）：按 tot 分层再问 band=tot>=4，分层变量与 band 是同一个量，
   层内 band 率必然恒为 0 或 1。该判据无判别力，当时写预注册时没意识到。
缺陷2（判据C 同病）：E5 背离 = (实际进球 - xG) × 0.5。控住 xg_tot≈2.6 后，
   "背离大"⟺"实际总球离 2.6 远"，而 band=tot>=4 正是其上侧。C 测的仍是同一个
   恒等式的两面，只是绕了一层。我用来排除循环的判据，自己就是循环。

== 本轮判据（跑数前冻结）==

根问题不是共线性，是**可观测性**：E5 背离与 xG 皆由赛后数据算出（fd 的 HxG/AxG
是比赛射门产生的 xG，非赛前预测）。若背离是实际比分的确定性函数，则 t5 无论多显著
都不能进校准通道——校准通道喂的是 v3.6 生成器，预测时拿不到这个条件变量。

D. 确定性检验（主判据）：r = corr(背离, |tot - xg_tot|)。
   判定：|r| >= 0.8 → 背离基本是比分偏离的改写，t5 判废（不可进校准通道，
   无论 n_on 是否补过 150）；0.5 <= |r| < 0.8 → 强改写嫌疑，需人工复核；
   |r| < 0.5 → 背离含比分之外的信息，t5 的循环嫌疑排除。

E. 赛前可观测性盘点（定性·必报）：列出算 E5 背离所需的每个输入，标注各自在
   开赛前是否可得。任一输入赛后才有 → t5 不得进校准通道（硬结论，不受 D 影响）。

F. 双侧富集对照：按 tot 分层看 hi 组占比（前一轮已出，此处只做解读）。
   若 hi 组在 tot=0 与 tot>=5 两端同时富集 → 背离是双侧偏离度量，
   而 band 只取单侧 ⟹ t5 的 diff 是"把双侧度量配单侧 band"的产物，非预测力。

报告口径：D/E/F 全列。E 为硬闸（定性即可判废），D 为量化佐证。
结论只回答"t5 能否进校准通道"，不回答"E5 维度该不该留在账单里"
（账单是赛后归因，赛后变量在账单里是正当的）。

开发者 sszhang
"""
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import quantiles

ROOT = Path(__file__).resolve().parents[3]
HIST_DIR = ROOT / "data" / "04-summaries" / "score-attribution" / "hist"
FINISH_SCALE = 0.5

# 判据E 盘点表：算 E5 背离所需输入 → 赛前是否可得
E5_INPUTS = [
    ("实际进球 hg/ag", False, "赛后才有（result 字段）"),
    ("比赛 xG（fd HxG/AxG）", False, "由比赛内射门质量算出·赛后才有，非赛前预测 xG"),
    ("FINISH_SCALE 系数", True, "常量 0.5（先验占位）"),
]


def load_e5() -> list[dict]:
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
        hg, ag = b["result"]["hg"] or 0, b["result"]["ag"] or 0
        xg_h = hg - (e5["h"] / FINISH_SCALE)
        xg_a = ag - (e5["a"] / FINISH_SCALE)
        out.append({"div": max(abs(e5.get("h") or 0.0), abs(e5.get("a") or 0.0)),
                    "tot": hg + ag, "xg_tot": xg_h + xg_a})
    return out


def _pearson(xs, ys) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    return round(sxy / math.sqrt(sxx * syy), 4) if sxx > 0 and syy > 0 else None


def main() -> None:
    rows = load_e5()
    p75 = round(quantiles(sorted(r["div"] for r in rows), n=4, method="inclusive")[2], 4)
    for r in rows:
        r["hi"] = r["div"] >= p75
        r["dev"] = abs(r["tot"] - r["xg_tot"])
    print(f"池 n={len(rows)} · P75={p75}\n")

    # ── 判据 D ──
    r_dev = _pearson([x["div"] for x in rows], [x["dev"] for x in rows])
    r_tot = _pearson([x["div"] for x in rows], [float(x["tot"]) for x in rows])
    print("=== 判据D 确定性检验 ===")
    print(f"corr(背离, |tot - xg_tot|) r={r_dev}")
    print(f"corr(背离, tot)            r={r_tot}  （对照）")
    verdict_d = ("判废：背离≈比分偏离的改写" if r_dev and abs(r_dev) >= 0.8
                 else "强改写嫌疑待复核" if r_dev and abs(r_dev) >= 0.5
                 else "循环嫌疑排除")
    print(f"→ D 判定（阈值 0.8/0.5 预注册）：{verdict_d}")

    # ── 判据 E ──
    print("\n=== 判据E 赛前可观测性盘点（硬闸）===")
    for name, pre, note in E5_INPUTS:
        print(f"  [{'赛前可得' if pre else '赛后才有'}] {name} —— {note}")
    blocked = [n for n, pre, _ in E5_INPUTS if not pre]
    print(f"→ E 判定：{len(blocked)} 个输入赛后才有"
          f"{'（t5 不得进校准通道·硬结论）' if blocked else '（无阻塞）'}")

    # ── 判据 F ──
    print("\n=== 判据F 双侧富集对照 ===")
    by_tot = defaultdict(list)
    for r in rows:
        by_tot[min(r["tot"], 6)].append(r)
    base = sum(r["hi"] for r in rows) / len(rows)
    shares = {}
    for tot in sorted(by_tot):
        grp = by_tot[tot]
        shares[tot] = sum(r["hi"] for r in grp) / len(grp)
    print(f"全池 hi 占比={base:.3f}")
    print("  " + "  ".join(f"tot{t}={shares[t]:.3f}" for t in sorted(shares)))
    lo_end, mid, hi_end = shares.get(0, 0), shares.get(2, 0), shares.get(6, 0)
    two_sided = lo_end > mid and hi_end > mid
    print(f"→ F 判定：tot0={lo_end:.3f} vs tot2={mid:.3f} vs tot6={hi_end:.3f}"
          f" ⟹ {'双侧富集=背离是双侧度量,band 只取单侧' if two_sided else '单侧富集'}")

    # band 命中拆解：hi 组的 band 命中全部来自高 tot 侧，低 tot 侧的 hi 场全部不中
    hi_rows = [r for r in rows if r["hi"]]
    hi_lowtot = [r for r in hi_rows if r["tot"] <= 1]
    print(f"\nhi 组 n={len(hi_rows)}：其中 tot<=1 共 {len(hi_lowtot)} 场"
          f"（背离同样大但 band 必不中）· tot>=4 共"
          f" {sum(1 for r in hi_rows if r['tot'] >= 4)} 场（band 必中）")


if __name__ == "__main__":
    main()
