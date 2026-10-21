# -*- coding: utf-8 -*-
"""LLM 对拍全量明细：一场一块，预测/实际/三向/格分布/判断依据/影子腿全列。

用法:
  py -3 engine/scripts/llm_eval_full.py 2026-10-20
  py -3 engine/scripts/llm_eval_full.py 2026-10-20 --html   # 落 HTML 到 04-summaries
"""
import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import llm_eval as le                                      # noqa: E402
from llm_eval_accuracy import DIRS, dir_of_key, dir_probs, match_names   # noqa: E402

TICKETS = Path("engine/shadow/paper_tickets.json")
MKTDIR = {0: "主胜", 1: "平局", 2: "客胜"}


def shadow_legs(day: str) -> dict:
    """开赛日影子票 → {code: [(票号, 形状, 玩法, 选项, 赔率, 票结果)]}。
    影子票按开赛日挂（销售日次日凌晨），故 day+1 也扫。"""
    out: dict[str, list] = {}
    if not TICKETS.exists():
        return out
    try:
        tks = json.loads(TICKETS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return out
    days = {day, le._shift_day(day, 1)}
    for t in tks:
        if str(t.get("date")) not in days:
            continue
        for leg in (t.get("legs") or []):
            pick = leg.get("pick")
            odds = leg.get("odds") or []
            if isinstance(pick, list):
                sel = "/".join(
                    MKTDIR.get(i, str(i)) if leg.get("market") == "had" else str(i)
                    for i in pick)
                od = "/".join(f"{odds[i]}" for i in pick
                              if isinstance(i, int) and i < len(odds)
                              and odds[i] is not None)
            else:
                sel, od = str(pick), ""
            out.setdefault(str(leg.get("code")), []).append(
                (t.get("id"), t.get("spec_name"), leg.get("market"), sel, od,
                 t.get("result"), t.get("payout")))
    return out


def grid_block(p: dict, actual: str, top_n: int = 8) -> list[str]:
    """概率前 N 格 + 真相格所在位次。"""
    srt = sorted(p, key=lambda k: -p[k])
    rank = srt.index(actual) + 1 if actual in srt else None
    lines = []
    for i, k in enumerate(srt[:top_n], 1):
        mark = "  ← 实际" if k == actual else ""
        lines.append(f"      {i:>2}. {k:9s} {p[k] * 100:5.1f}%{mark}")
    if rank and rank > top_n:
        lines.append(f"      ...  实际格 {actual} 排第 {rank}/{len(srt)}"
                     f"，概率 {p[actual] * 100:.2f}%")
    elif actual not in srt:
        lines.append(f"      ...  实际格 {actual} 未在预测池内")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description="LLM 对拍全量明细")
    ap.add_argument("days", nargs="*")
    ap.add_argument("--html", action="store_true", help="同时落 HTML")
    args = ap.parse_args()
    days = args.days or le._default_days()

    for d in days:
        rows = le._load_day_rows([d])
        if not rows:
            print(f"{d}: 无可结算行")
            continue
        names, legs = match_names(d), shadow_legs(d)
        n_sc = n_dir = 0
        print(f"\n{'=' * 78}\n销售日 {d} · {len(rows)} 场 · 开赛 {le._shift_day(d, 1)}\n{'=' * 78}")
        for r in sorted(rows, key=lambda x: x["code"]):
            pool = r["_marketPool"]
            p = {k: v for k, v in r["p_crs"].items() if k in pool}
            keys = list(p)
            mkt = le.market_devig_on_keys(pool, keys)
            z = sum(p.values())
            llm = {k: (v / z if z > 0 else 1.0 / len(keys)) for k, v in p.items()}
            score = r["_score"]
            # 概率查格必用池映射键：4:3/6:0 这类不在 31 定格内,要归到 other_*,
            # 直接拿裸比分查会查到 0 并把 diff 算成 0（掩盖零格自伤）
            akey = le.actual_key(score, pool)
            srt = sorted(llm, key=lambda k: -llm[k])
            top1 = srt[0]
            dp, mdp = dir_probs(llm), dir_probs(mkt)
            pd_, ad = max(dp, key=dp.get), dir_of_key(akey)
            sc_hit, dir_hit = top1 == akey, pd_ == ad
            n_sc += sc_hit
            n_dir += dir_hit
            diff = (-math.log(max(mkt.get(akey, 0.0), le.LOGLLOSS_CLIP))
                    + math.log(max(llm.get(akey, 0.0), le.LOGLLOSS_CLIP)))

            print(f"\n{'-' * 78}")
            tag = f"（真相格 {akey}：不在 31 定格内）" if akey != score else ""
            print(f"{r['code']}  {names.get(r['code'], '')}   实际 {score}{tag}")
            print(f"{'-' * 78}")
            zmark = "  ←填0自伤" if llm.get(akey, 0.0) <= le.LOGLLOSS_CLIP else ""
            print(f"  比分  预测 {top1}（{llm[top1] * 100:.1f}%）→ "
                  f"{'命中' if sc_hit else '未中'}"
                  f"      实际格概率 臣 {llm.get(akey, 0) * 100:.2f}% / "
                  f"市场 {mkt.get(akey, 0) * 100:.2f}%{zmark}")
            print(f"  方向  预测 {DIRS[pd_]}（臣 主{dp['h'] * 100:.0f}/平{dp['d'] * 100:.0f}"
                  f"/客{dp['a'] * 100:.0f}） 实际 {DIRS[ad]} → "
                  f"{'命中' if dir_hit else '未中'}")
            print(f"        市场   主{mdp['h'] * 100:.0f}/平{mdp['d'] * 100:.0f}"
                  f"/客{mdp['a'] * 100:.0f}"
                  f"        信心 {r.get('confidence')}  概率账 diff {diff:+.3f}")
            anc = r.get("anchor") or {}
            if anc:
                print(f"        锚 k={anc.get('k')} {anc.get('before')}→{anc.get('target')}"
                      f"（总进球期望）")
            print(f"  预测分布（前 8 格）")
            for ln in grid_block(llm, akey):
                print(ln)
            kjs = r.get("keyJudgments") or []
            if kjs:
                print(f"  判断依据（{len(kjs)} 条）")
                for i, kj in enumerate(kjs, 1):
                    jt = kj.get("judgment") if isinstance(kj, dict) else str(kj)
                    ev = kj.get("evidence", "") if isinstance(kj, dict) else ""
                    print(f"      {i}. {jt}")
                    if ev:
                        print(f"         证据: {ev}")
            lg = legs.get(r["code"]) or []
            if lg:
                print(f"  影子票腿（{len(lg)} 条）")
                for tid, spec, mk, sel, od, res, pay in lg:
                    pt = f" 派彩{pay}" if pay else ""
                    print(f"      {tid} {spec:16s} {mk:5s} {sel:12s} @{od:12s}"
                          f" [{res}{pt}]")
        print(f"\n{'=' * 78}\n{d} 小计：比分 {n_sc}/{len(rows)} · 方向 {n_dir}/{len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
