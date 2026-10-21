# -*- coding: utf-8 -*-
"""影子层方案总账：按 spec 汇总样本量/回款率/净额,给转正与关灯判据。

用法:
  py -3 engine/scripts/shadow_scoreboard.py              # 全量总账
  py -3 engine/scripts/shadow_scoreboard.py --recent 14  # 近 N 个开赛日
  py -3 engine/scripts/shadow_scoreboard.py --wins       # 只列有派彩的票

判据（影子层转正门槛,v5.8 口径）:
  n>=30 且 净额>0 且 Wilson 下限>0  → 可提转正评审
  n>=30 且 回款率<40%              → 建议关灯
  n<30                             → 样本不足,继续观察
"""
import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

TICKETS = Path("engine/shadow/paper_tickets.json")
MIN_N = 30                 # 转正/关灯判据的最小样本
LOW_RETURN = 0.40          # 关灯线：回款率


def wilson_lo(k: int, n: int, z: float = 1.96) -> float:
    """命中率 Wilson 下限（票级中奖率,非金额）。"""
    if n == 0:
        return 0.0
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (c - m) / d)


def main() -> int:
    ap = argparse.ArgumentParser(description="影子层方案总账")
    ap.add_argument("--recent", type=int, help="只看近 N 个开赛日")
    ap.add_argument("--wins", action="store_true", help="只列有派彩的票")
    args = ap.parse_args()

    tks = json.loads(TICKETS.read_text(encoding="utf-8"))
    days = sorted({str(t.get("date")) for t in tks})
    if args.recent:
        keep = set(days[-args.recent:])
        tks = [t for t in tks if str(t.get("date")) in keep]
        print(f"窗口：近 {args.recent} 个开赛日（{min(keep)} → {max(keep)}）")

    if args.wins:
        wins = [t for t in tks if (t.get("payout") or 0) > 0]
        print(f"\n{'=' * 86}\n有派彩的票 {len(wins)} 张"
              f"（共 {len(tks)} 张,中奖率 {len(wins) / len(tks):.1%}）\n{'=' * 86}")
        print(f"{'开赛日':12s}{'票':6s}{'方案':22s}{'成本':>6s}{'派彩':>9s}{'净':>9s}  腿数")
        for t in sorted(wins, key=lambda x: (str(x.get("date")), str(x.get("id")))):
            c, p = t.get("cost") or 0, t.get("payout") or 0
            print(f"{str(t.get('date')):12s}{str(t.get('id')):6s}"
                  f"{str(t.get('spec_name'))[:20]:22s}{c:6.0f}{p:9.2f}{p - c:+9.2f}"
                  f"  {len(t.get('legs') or [])}")
        tc = sum(t.get("cost") or 0 for t in wins)
        tp = sum(t.get("payout") or 0 for t in wins)
        print(f"{'小计':12s}{'':6s}{'':22s}{tc:6.0f}{tp:9.2f}{tp - tc:+9.2f}")
        return 0

    agg = defaultdict(lambda: {"n": 0, "win": 0, "cost": 0.0, "pay": 0.0, "pend": 0})
    for t in tks:
        a = agg[str(t.get("spec_name"))]
        if t.get("result") == "pending":
            a["pend"] += 1
            continue
        a["n"] += 1
        a["cost"] += t.get("cost") or 0
        a["pay"] += t.get("payout") or 0
        a["win"] += 1 if (t.get("payout") or 0) > 0 else 0

    rows = []
    for spec, a in agg.items():
        if not a["n"]:
            continue
        ret = a["pay"] / a["cost"] if a["cost"] else 0.0
        rows.append((spec, a["n"], a["win"], a["cost"], a["pay"],
                     a["pay"] - a["cost"], ret, wilson_lo(a["win"], a["n"]),
                     a["pend"]))
    rows.sort(key=lambda r: -r[5])

    print(f"\n{'=' * 104}\n影子层方案总账 · {len(rows)} 个方案 · "
          f"{sum(r[1] for r in rows)} 张已结票\n{'=' * 104}")
    print(f"{'方案':24s}{'n':>4s}{'中':>4s}{'中奖率':>8s}{'成本':>8s}"
          f"{'回款':>9s}{'净额':>9s}{'回款率':>8s}{'裁决':>12s}")
    for spec, n, win, cost, pay, net, ret, lo, pend in rows:
        if n < MIN_N:
            verd = "样本不足"
        elif net > 0 and lo > 0:
            verd = "★可转正"
        elif ret < LOW_RETURN:
            verd = "建议关灯"
        else:
            verd = "继续观察"
        print(f"{spec[:22]:24s}{n:4d}{win:4d}{win / n:8.1%}{cost:8.0f}"
              f"{pay:9.2f}{net:+9.2f}{ret:8.1%}{verd:>12s}")

    tn = sum(r[1] for r in rows)
    tw = sum(r[2] for r in rows)
    tc = sum(r[3] for r in rows)
    tp = sum(r[4] for r in rows)
    print(f"{'=' * 104}")
    print(f"{'合计':24s}{tn:4d}{tw:4d}{tw / tn:8.1%}{tc:8.0f}"
          f"{tp:9.2f}{tp - tc:+9.2f}{tp / tc:8.1%}")
    print(f"\n判据：n>={MIN_N} 且净额>0 且 Wilson 下限>0 → 可提转正评审；"
          f"n>={MIN_N} 且回款率<{LOW_RETURN:.0%} → 建议关灯")
    pend = sum(a["pend"] for a in agg.values())
    if pend:
        print(f"挂起 {pend} 张未计入（含未开赛腿）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
