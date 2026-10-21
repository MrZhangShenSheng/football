# -*- coding: utf-8 -*-
"""LLM 对拍逐场明细（eval.json 只落汇总，逐场账要看这里）。

用法:
  py -3 engine/scripts/llm_eval_detail.py                 # 全部评估日
  py -3 engine/scripts/llm_eval_detail.py 2026-10-20      # 指定销售日
  py -3 engine/scripts/llm_eval_detail.py --outliers 5     # 只看 |diff|>5 的离群行

R027: 读 diffMean 前先逐场拆——单场 |diff|>5 必是零格或极端薄尾，均值被它主导
时结论要分开报（2026-10-20 实证：全量 −1.083 实为两场 4:3 填 0 所致，剔除后 +0.13）。
"""
import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import llm_eval as le                                      # noqa: E402

OUTLIER_ABS = 5.0        # R027 离群线


def row_detail(r: dict) -> dict:
    """单行 → 对拍账（市场/臣在真相格的概率、diff、零格与尾部口径）。"""
    keys = [k for k in r["p_crs"] if k in r["_marketPool"]]
    mkt = le.market_devig_on_keys(r["_marketPool"], keys)
    actual = le.actual_key(r["_score"], r["_marketPool"])
    z = sum(r["p_crs"].get(k, 0.0) for k in keys)
    llm = {k: (r["p_crs"].get(k, 0.0) / z if z > 0 else 1.0 / len(keys)) for k in keys}
    p_llm, p_mkt = llm.get(actual, 0.0), mkt.get(actual, 0.0)
    diff = (-math.log(max(p_mkt, le.LOGLLOSS_CLIP))
            + math.log(max(p_llm, le.LOGLLOSS_CLIP)))
    zeros = [k for k in keys if llm.get(k, 0.0) <= le.LOGLLOSS_CLIP]
    return {
        "code": r["code"], "score": r["_score"], "actual": actual,
        "pLlm": p_llm, "pMkt": p_mkt, "diff": diff,
        "zeroCells": len(zeros),
        "zeroOnActual": p_llm <= le.LOGLLOSS_CLIP,
        "zeroMktMass": sum(mkt.get(k, 0.0) for k in zeros),
        "genCohort": r.get("_genCohort"), "kjCohort": r.get("_kjCohort"),
        "closeProxy": r.get("_closeProxy"), "conf": r.get("confidence"),
        "top1": max(llm, key=llm.get) if llm else None,
        "pTop1": max(llm.values()) if llm else 0.0,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="LLM 对拍逐场明细")
    ap.add_argument("days", nargs="*")
    ap.add_argument("--outliers", type=float, nargs="?", const=OUTLIER_ABS,
                    help=f"只列 |diff| 超过该值的行（默认 {OUTLIER_ABS}）")
    args = ap.parse_args()
    days = args.days or le._default_days()

    all_rows = []
    for d in days:
        rows = [row_detail(r) for r in le._load_day_rows([d])]
        if not rows:
            continue
        for x in rows:
            x["day"] = d
        all_rows += rows
        if args.outliers:
            continue
        s = sum(x["diff"] for x in rows)
        print(f"\n=== {d} · {len(rows)} 场 · 合计 {s:+.3f} 均 {s / len(rows):+.4f} ===")
        print(f"{'场':9s}{'赛果':6s}{'真相格':10s}{'臣p':>8s}{'市p':>8s}"
              f"{'diff':>9s}{'零格':>5s}{'零格市值':>9s}  {'口径':14s}{'top1':>10s}")
        for x in sorted(rows, key=lambda y: y["diff"]):
            flag = " ←零格自伤" if x["zeroOnActual"] else ""
            print(f"{x['code']:9s}{x['score']:6s}{x['actual']:10s}"
                  f"{x['pLlm']:8.4f}{x['pMkt']:8.4f}{x['diff']:+9.3f}"
                  f"{x['zeroCells']:5d}{x['zeroMktMass']:9.3f}  "
                  f"{(x['genCohort'] or '')[:13]:14s}"
                  f"{(x['top1'] or ''):>7s}{x['pTop1']:7.3f}{flag}")

    if not all_rows:
        print("无可结算行")
        return 0

    out = [x for x in all_rows if abs(x["diff"]) >= (args.outliers or OUTLIER_ABS)]
    if args.outliers:
        print(f"=== 离群行 |diff|>={args.outliers} · {len(out)}/{len(all_rows)} 场 ===")
        for x in sorted(out, key=lambda y: y["diff"]):
            print(f"{x['day']}  {x['code']:9s}{x['score']:6s}{x['actual']:10s}"
                  f" 臣p={x['pLlm']:.4f} 市p={x['pMkt']:.4f} diff={x['diff']:+.3f}"
                  f"{'  ←零格自伤' if x['zeroOnActual'] else ''}")

    tot = sum(x["diff"] for x in all_rows)
    clean = [x for x in all_rows if abs(x["diff"]) < (args.outliers or OUTLIER_ABS)]
    ct = sum(x["diff"] for x in clean)
    print(f"\n=== 总账 {len(all_rows)} 场 ===")
    print(f"全量      合计 {tot:+9.3f}  均 {tot / len(all_rows):+.4f}")
    if clean and len(clean) != len(all_rows):
        print(f"剔离群{len(all_rows) - len(clean)}场 合计 {ct:+9.3f}  均 {ct / len(clean):+.4f}"
              f"   ← R027 分开报")
    zs = [x for x in all_rows if x["zeroOnActual"]]
    print(f"真相格落零格 {len(zs)} 场"
          + (f"：{', '.join(x['code'] + '/' + x['score'] for x in zs)}" if zs else ""))
    win = sum(1 for x in all_rows if x["diff"] > 0)
    print(f"逐场胜负 臣赢 {win} / 市场赢 {len(all_rows) - win}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
