# -*- coding: utf-8 -*-
"""影子票逐票逐腿明细：每张票每条腿中没中、为什么是这个派彩。

用法:
  py -3 engine/scripts/shadow_detail.py 2026-10-21        # 按开赛日
  py -3 engine/scripts/shadow_detail.py 2026-10-21 --hit  # 只看有派彩的票
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import llm_eval as le                                      # noqa: E402

TICKETS = Path("engine/shadow/paper_tickets.json")
HAD = {0: "主胜", 1: "平局", 2: "客胜"}


def results_for(day: str) -> dict:
    """开赛日 ±1 的赛果（影子票按开赛日挂，赛果档按销售日落）。"""
    return le._load_results([le._shift_day(day, -1), day])


def ttg_band(total: int) -> str:
    """总进球 → TTG 档位键（体彩口径 s0..s7，7+ 并入 s7）。"""
    return f"s{min(total, 7)}"


def leg_hit(leg: dict, score: str) -> tuple[bool, str]:
    """单腿是否命中 + 实际落点描述。"""
    h, a = (int(x) for x in score.split(":"))
    mk, pick = leg.get("market"), leg.get("pick")
    picks = pick if isinstance(pick, list) else [pick]
    if mk == "had":
        act = 0 if h > a else (1 if h == a else 2)
        return act in picks, HAD[act]
    if mk == "ttg":
        act = ttg_band(h + a)
        pk = [p if isinstance(p, str) else f"s{p}" for p in picks]
        return act in pk, f"{act}（{h + a}球）"
    if mk == "crs":
        act = score
        return act in [str(p) for p in picks], act
    return False, "?"


def fmt_pick(leg: dict) -> str:
    mk, pick = leg.get("market"), leg.get("pick")
    picks = pick if isinstance(pick, list) else [pick]
    if mk == "had":
        return "/".join(HAD.get(p, str(p)) for p in picks)
    return "/".join(str(p) for p in picks)


def main() -> int:
    ap = argparse.ArgumentParser(description="影子票逐腿明细")
    ap.add_argument("day")
    ap.add_argument("--hit", action="store_true", help="只看有派彩的票")
    args = ap.parse_args()

    tks = json.loads(TICKETS.read_text(encoding="utf-8"))
    day_tks = [t for t in tks if str(t.get("date")) == args.day]
    if not day_tks:
        print(f"{args.day}: 无影子票")
        return 0
    res = results_for(args.day)

    cost = pay = 0.0
    n_win = n_lose = n_pend = 0
    print(f"{'=' * 92}\n影子票明细 · 开赛日 {args.day} · {len(day_tks)} 票\n{'=' * 92}")
    for t in sorted(day_tks, key=lambda x: str(x.get("id"))):
        payout = t.get("payout")
        if args.hit and not payout:
            continue
        st = t.get("result")
        cost += t.get("cost") or 0
        pay += payout or 0
        if st == "pending":
            n_pend += 1
        elif payout:
            n_win += 1
        else:
            n_lose += 1
        tag = (f"派彩 {payout}" if payout else
               ("挂起" if st == "pending" else "未中"))
        roi = f"（成本 {t.get('cost')} → {tag}）"
        print(f"\n{'-' * 92}")
        print(f"{t.get('id')}  {t.get('spec_name')}  track={t.get('track')}"
              f"  {t.get('n_bets')}注×{t.get('mult')}倍  {roi}")
        legs = t.get("legs") or []
        n_ok = 0
        for leg in legs:
            code = str(leg.get("code"))
            sc = res.get(code)
            if not sc:
                print(f"    {code:9s}{(leg.get('match') or '')[:20]:22s}"
                      f"{leg.get('market'):5s}{fmt_pick(leg):14s} ——  未开赛/无赛果")
                continue
            ok, act = leg_hit(leg, sc)
            n_ok += ok
            print(f"    {code:9s}{(leg.get('match') or '')[:20]:22s}"
                  f"{leg.get('market'):5s}{fmt_pick(leg):14s}"
                  f"{sc:6s}实际{act:12s}{'✓' if ok else '✗'}")
        done = [l for l in legs if res.get(str(l.get("code")))]
        if done:
            print(f"    → 腿账 {n_ok}/{len(done)} 中"
                  + ("（全中）" if n_ok == len(done) and len(done) == len(legs) else ""))
    print(f"\n{'=' * 92}")
    print(f"总账 {len(day_tks)} 票：中 {n_win} · 未中 {n_lose} · 挂起 {n_pend}")
    print(f"成本 {cost:.0f} 元 → 回款 {pay:.2f} 元"
          + (f"（净 {pay - cost:+.2f}）" if not n_pend else "（含挂起票，净额待定）"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
