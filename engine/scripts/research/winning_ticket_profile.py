# -*- coding: utf-8 -*-
r"""中奖票画像：命中的都是比分吗？票型是什么？（大哥 2026-09-30 问）

两个数据源分别回答，不混在一起（口径不同，混算会得出假规律）：
  A 真实票据 data/06-tickets/tickets.json —— 我们真金白银买过的 44 张，
    按 legs[].market 分玩法（CRS 比分 / HAD 胜平负 / HHAD 让球 / TTG 总进球…），
    看"有回款的票"里命中腿的玩法构成、票型(shape)构成。
  B v4b 回测那 17 张命中票 —— 口径是纯 CRS 比分 2 串 1，本身只买比分，
    所以"是不是比分"无需问；要问的是**赔率结构**（靠低赔还是高赔中的）。

只描述、不推断因果：本脚本回答"长什么样"，不回答"为什么中"，
也不据此提出选腿规则——命中样本极少（真实票有回款者十余张、回测 17 张），
任何"规律"都可能是右尾噪声（已由 v4b_roi_53_selfrefute.py 实证：
剔除最大 2 张命中票即净利转负）。

用法：python engine/scripts/research/winning_ticket_profile.py
开发者 sszhang
"""
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TICKETS = ROOT / "data" / "06-tickets" / "tickets.json"
BACKTEST = ROOT / "engine" / "scripts" / "research" / "v4b_full_backtest.py"
OUT = ROOT / "data" / "04-summaries" / "2026-09-30-winning-ticket-profile.json"

MARKET_ZH = {"crs": "CRS 比分", "had": "HAD 胜平负", "hhad": "HHAD 让球",
             "ttg": "TTG 总进球", "hafu": "HAFU 半全场"}


def market_of(leg):
    m = str(leg.get("market") or "").strip().lower()
    return MARKET_ZH.get(m, m or "未标注")


def leg_hit(leg):
    """腿是否命中：result 字段优先，其次 pick 与 actual 比对。"""
    r = leg.get("result")
    if isinstance(r, bool):
        return r
    if isinstance(r, str):
        s = r.strip().lower()
        if s in ("hit", "win", "中", "命中", "true"):
            return True
        if s in ("miss", "lose", "未中", "false"):
            return False
    p, a = leg.get("pick"), leg.get("actual")
    if p is not None and a is not None:
        return str(p).strip() == str(a).strip()
    return None


def main():
    print("=" * 74)
    print("A 真实票据（tickets.json）——命中腿的玩法与票型构成")
    print("=" * 74)
    t = json.loads(TICKETS.read_text(encoding="utf-8"))
    ts = t if isinstance(t, list) else t.get("tickets", [])
    settled = [x for x in ts if (x.get("settled") or {}).get("status") == "settled"]
    paid = [x for x in settled if float((x.get("settled") or {}).get("payout") or 0) > 0]
    print(f"总票 {len(ts)}  已结算 {len(settled)}  有回款 {len(paid)}")

    # 玩法构成：全部腿 vs 命中腿
    all_by_mkt, hit_by_mkt = Counter(), Counter()
    for x in settled:
        for leg in (x.get("legs") or []):
            if leg.get("revoked"):
                continue
            mk = market_of(leg)
            all_by_mkt[mk] += 1
            if leg_hit(leg):
                hit_by_mkt[mk] += 1
    print("\n  各玩法腿命中率（已结算票的全部腿）")
    for mk, n in all_by_mkt.most_common():
        h = hit_by_mkt[mk]
        print(f"    {mk:12s} 腿 {n:4d}  命中 {h:3d}  命中率 {h/n:6.1%}")
    tot_legs, tot_hits = sum(all_by_mkt.values()), sum(hit_by_mkt.values())
    print(f"    {'合计':12s} 腿 {tot_legs:4d}  命中 {tot_hits:3d}  "
          f"命中率 {tot_hits/max(1,tot_legs):6.1%}")

    print("\n  有回款票中，命中腿的玩法占比（回答'是不是都靠比分'）")
    paid_hit = Counter()
    for x in paid:
        for leg in (x.get("legs") or []):
            if not leg.get("revoked") and leg_hit(leg):
                paid_hit[market_of(leg)] += 1
    s = sum(paid_hit.values()) or 1
    for mk, n in paid_hit.most_common():
        print(f"    {mk:12s} {n:3d} 腿  {n/s:5.1%}")

    print("\n  有回款票的票型(shape)构成")
    for sh, n in Counter(str(x.get("shape") or "未标注") for x in paid).most_common():
        pay = sum(float((y.get("settled") or {}).get("payout") or 0)
                  for y in paid if str(y.get("shape") or "未标注") == sh)
        cost = sum(float(y.get("stake") or 0)
                   for y in paid if str(y.get("shape") or "未标注") == sh)
        print(f"    {sh:16s} {n:2d} 张  成本 {cost:6.0f}  派彩 {pay:7.1f}  "
              f"回收 {pay/max(1,cost):6.1%}")

    print("\n  全部已结算票型的盈亏（含未回款，看哪种票型真的赚过）")
    agg = defaultdict(lambda: [0, 0.0, 0.0])
    for x in settled:
        sh = str(x.get("shape") or "未标注")
        agg[sh][0] += 1
        agg[sh][1] += float(x.get("stake") or 0)
        agg[sh][2] += float((x.get("settled") or {}).get("payout") or 0)
    for sh, (n, c, p) in sorted(agg.items(), key=lambda kv: -(kv[1][2] - kv[1][1])):
        print(f"    {sh:16s} {n:2d} 张  成本 {c:6.0f}  派彩 {p:7.1f}  "
              f"净 {p-c:+8.1f}  回收 {p/max(1,c):6.1%}")

    print("\n" + "=" * 74)
    print("B v4b 回测 17 张命中票——赔率结构（该口径只买 CRS 比分 2 串 1）")
    print("=" * 74)
    r = subprocess.run([sys.executable, "engine/scripts/research/v4b_full_backtest.py"],
                       cwd=str(ROOT), capture_output=True, timeout=3600)
    txt = (r.stdout or b"").decode("utf-8", "replace")
    legs = [(sc, float(od)) for sc, od in
            re.findall(r"预测.*?:\s*(\d+:\d+)\s*赔率([\d.]+)", txt)]
    pays = [float(v) for v in re.findall(r"成本\d+(?:\.\d+)?元\s*派彩([\d.]+)元", txt)]
    print(f"命中票 {len(pays)} 张，命中腿 {len(legs)} 条（每票 2 腿）")
    if legs:
        sc_cnt = Counter(sc for sc, _ in legs)
        print("\n  命中的比分分布")
        for sc, n in sc_cnt.most_common():
            print(f"    {sc}  {n:2d} 次  {n/len(legs):5.1%}")
        ods = sorted(od for _, od in legs)
        print(f"\n  命中腿赔率：最低 {ods[0]:.1f}  中位 {ods[len(ods)//2]:.1f}  "
              f"最高 {ods[-1]:.1f}")
        draws = sum(1 for sc, _ in legs if sc.split(":")[0] == sc.split(":")[1])
        print(f"  平局比分（x:x）占命中腿 {draws}/{len(legs)} = {draws/len(legs):.1%}"
              f"  ← 这是最扎眼的一条")
        low = sum(1 for _, od in legs if od <= 7.0)
        print(f"  赔率 ≤7.0 的腿占 {low}/{len(legs)} = {low/len(legs):.1%}")
    if pays:
        pays_sorted = sorted(pays, reverse=True)
        print(f"\n  票派彩：最大 {pays_sorted[0]:.1f}  中位 "
              f"{pays_sorted[len(pays_sorted)//2]:.1f}  最小 {pays_sorted[-1]:.1f}")

    OUT.write_text(json.dumps({
        "ranAt": "2026-09-30",
        "ask": "大哥问：中奖票都是比分中的吗？票型是什么",
        "real": {
            "nTickets": len(ts), "nSettled": len(settled), "nPaid": len(paid),
            "legsByMarket": dict(all_by_mkt), "hitsByMarket": dict(hit_by_mkt),
            "paidTicketHitsByMarket": dict(paid_hit),
            "byShape": {k: {"n": v[0], "cost": round(v[1], 1),
                            "payout": round(v[2], 1), "net": round(v[2]-v[1], 1)}
                        for k, v in agg.items()},
        },
        "backtest17": {
            "nHitTickets": len(pays), "nHitLegs": len(legs),
            "scoreDist": dict(Counter(sc for sc, _ in legs)),
            "drawShare": round(sum(1 for sc, _ in legs
                                   if sc.split(":")[0] == sc.split(":")[1])
                               / max(1, len(legs)), 4),
            "oddsMin": min((o for _, o in legs), default=None),
            "oddsMax": max((o for _, o in legs), default=None),
        },
        "caveat": "命中样本极少，画像仅描述不作规则；v4b_roi_53_selfrefute 已证"
                  "剔除最大 2 张命中票即净利转负",
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\n→ {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
