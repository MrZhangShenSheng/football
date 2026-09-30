# -*- coding: utf-8 -*-
r"""方向线（HAD/HHAD）正收益自证伪审计（铁律 14 第 3 条）。

2026-09-30 大哥问"还能往哪提高准确率"。corpus 方向腿 274 注回收率 107.9% 是正的，
按铁律 14，正收益必须先自证伪再上报。本脚本查四件事：

  ① 样本是否真前瞻（已单独核实：预测文件赛前入库、赛果次日回填，非回测）
  ② 回收率置信区间是否越过打平线（正收益是不是只是样本噪声）
  ③ 是否存在选择性记账（漏记 directionHit 的 62 条、"避开"类记录是否系统性偏利）
  ④ 下注子集 vs 全记录子集差异（只算"入方案"的腿会不会挑出更好看的数字）

判据预注册：只有当 ②的 95%CI 下界 > 100% 且 ③④ 无系统性偏差，才可称"方向线有正 EV"；
否则一律标"未证伪、不可作决策依据"。
用法：python engine/scripts/research/direction_line_audit.py
开发者 sszhang
"""
import json
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
CORPUS = ROOT / "data" / "04-summaries" / "corpus.json"
OUT = ROOT / "data" / "04-summaries" / "2026-09-30-direction-audit.json"
SEED = 20260930
N_BOOT = 20000


def boot(pay, seed=SEED):
    """回收率 bootstrap 区间。"""
    rng = np.random.default_rng(seed)
    n = len(pay)
    bs = np.array([rng.choice(pay, n, replace=True).mean() for _ in range(N_BOOT)])
    lo, hi = np.percentile(bs, [2.5, 97.5])
    return pay.mean(), lo, hi, (bs > 1.0).mean()


def describe(tag, recs):
    if not recs:
        print(f"{tag}: 无样本")
        return None
    pay = np.array([x["odds"] if x["directionHit"] else 0.0 for x in recs])
    hit = np.mean([bool(x["directionHit"]) for x in recs])
    m, lo, hi, pwin = boot(pay)
    print(f"{tag}: n={len(recs)} 命中 {hit:.1%} 回收率 {m:.1%} "
          f"95%CI [{lo:.1%}, {hi:.1%}] P(>100%)={pwin:.1%}")
    return {"n": len(recs), "hit": round(float(hit), 4), "roi": round(float(m), 4),
            "ci": [round(float(lo), 4), round(float(hi), 4)],
            "pAboveBreakeven": round(float(pwin), 4)}


def main():
    recs = json.loads(CORPUS.read_text(encoding="utf-8"))["records"]
    num = (int, float)
    dr = [x for x in recs
          if x.get("directionHit") is not None and isinstance(x.get("odds"), num)]

    print(f"corpus 共 {len(recs)} 条\n")
    print("=" * 66)
    print("① 全部方向腿（带数值赔率）")
    print("=" * 66)
    full = describe("  全部", dr)

    print("\n" + "=" * 66)
    print("② 选择性记账检查")
    print("=" * 66)
    play_dir = [x for x in recs
                if (str(x.get("play") or "")).upper() in ("HAD", "HHAD", "胜平负", "方向")]
    missing = [x for x in play_dir if x.get("directionHit") is None]
    avoid = [x for x in recs if "避开" in str(x.get("pick", ""))]
    print(f"  play=方向类 {len(play_dir)} 条，其中漏记 directionHit {len(missing)} 条"
          f"（{len(missing)/max(1,len(play_dir)):.1%}）")
    print(f"  含'避开'标记 {len(avoid)} 条，其中记了 directionHit 的 "
          f"{sum(1 for x in avoid if x.get('directionHit') is not None)} 条")
    # 漏记的那些若补记，命中率会是多少？用 result 无法直接复原 pick 对错，故只报规模
    print("  ⚠ 漏记的 pick 多为'(避开)'等非下注标记，无法复原对错 → 分母不确定性")

    print("\n" + "=" * 66)
    print("③ 入方案子集 vs 全体（挑子集会不会更好看）")
    print("=" * 66)
    inplan = [x for x in dr if x.get("in_plan") not in (None, False, "", "排除")]
    notin = [x for x in dr if x.get("in_plan") in (None, False, "", "排除")]
    sub_in = describe("  入方案", inplan)
    sub_no = describe("  未入方案", notin)

    print("\n" + "=" * 66)
    print("④ 分月稳定性（单月为王 vs 持续）")
    print("=" * 66)
    by_m = {}
    for mon in sorted({x["date"][:7] for x in dr}):
        sub = [x for x in dr if x["date"][:7] == mon]
        by_m[mon] = describe(f"  {mon}", sub)

    print("\n" + "=" * 66)
    print("⑤ 赔率档位（是否只靠某一档）")
    print("=" * 66)
    bands = {"<1.5": lambda v: v < 1.5, "1.5~2.0": lambda v: 1.5 <= v < 2.0,
             ">=2.0": lambda v: v >= 2.0}
    by_b = {}
    for tag, f in bands.items():
        by_b[tag] = describe(f"  {tag}", [x for x in dr if f(x["odds"])])

    print("\n" + "=" * 66)
    print("判据结论")
    print("=" * 66)
    lo = full["ci"][0]
    verdict_parts = []
    if lo > 1.0:
        verdict_parts.append("CI 下界 > 100%")
    else:
        verdict_parts.append(f"❌ CI 下界 {lo:.1%} 未越过打平线 → 正收益与'无优势'统计上分不开")
    if len(missing) / max(1, len(play_dir)) > 0.1:
        verdict_parts.append(
            f"❌ 漏记 {len(missing)}/{len(play_dir)} 条使分母不确定，回收率口径不可靠")
    print("  " + "；".join(verdict_parts))
    verdict = ("未证伪，不可作决策依据：" + "；".join(verdict_parts)) if lo <= 1.0 \
        else "CI 下界越过打平线，可进一步验证"
    print(f"\n  → {verdict}")
    print("\n  注：本样本为真前瞻记录（预测文件赛前入库、赛果次日回填，非回测），")
    print("  故不含 2026-09-30 那类回测自泄漏；问题在样本量与记账口径，不在时间线。")

    OUT.write_text(json.dumps({
        "ranAt": "2026-09-30", "source": "corpus.json", "nCorpus": len(recs),
        "full": full, "inPlan": sub_in, "notInPlan": sub_no,
        "byMonth": by_m, "byOddsBand": by_b,
        "bookkeeping": {"nPlayDirection": len(play_dir), "nMissingHit": len(missing),
                        "nAvoidTagged": len(avoid)},
        "prospective": True,
        "prospectiveEvidence": "data/02-results/2026-09-28.json 首次入库 09-28 16:10（赛前），赛果 09-29 09:42 回填",
        "verdict": verdict,
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\n→ {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
