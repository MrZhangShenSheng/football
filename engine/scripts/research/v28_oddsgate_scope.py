# -*- coding: utf-8 -*-
"""v28 可行性勘察（线二前置·非实验）：赔率进决策层的改动面有多大？

线二设想：把彩票档门槛 p_fused≥0.55 的 p 由「融合 p」换成「市场去水 p」。
但换之前须先量：**到底有多少腿的门槛判定会翻**。若改动面近零，则此线为空转，
不值得立预注册跑 A/B。

做法：读历史出票卡的彩票档腿（已落盘 p_fused/p_mkt=p_fused−divergence 可反推），
逐腿重判 p_mkt≥0.55，与现行 p_fused≥0.55 对照：
  - 同判腿 = 换口径后入池/落池结果不变（改动无感）
  - 翻判腿 = 现行入池但市场口径落池（或反之）——这才是线二真正的作用面

纪律：勘察性·不作 EV 呈报·不改任何生产参数。
开发者 sszhang
"""
from __future__ import annotations

import collections
import glob
import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "data" / "04-summaries" / "v28-oddsgate-scope.json"
GATE = 0.55
PICK_SLOT = {"主胜": 0, "平": 1, "客胜": 2, "让球主胜": 0, "让球平": 1, "让球客胜": 2}


def resolve_pmkt(rows):
    """用当日赔率存档精确算 p_mkt（devig 三向），定出 divergence 符号。→ 解出条数。

    源：data/02-results/{date}-*.json 或 engine/cache/score_odds/{date}.json 存档的
    当日在售三向价（had/hhad）。缺档则该腿留 None 走两侧可能性。"""
    archives: dict[str, dict] = {}

    def day_odds(day):
        if day in archives:
            return archives[day]
        pool = {}
        for pat in (ROOT / "engine" / "cache" / "score_odds" / f"{day}.json",
                    ROOT / "engine" / "cache" / "odds_today.json"):
            if not pat.exists():
                continue
            try:
                blob = json.loads(pat.read_text(encoding="utf-8"))
            except Exception:
                continue
            # score_odds 存档为 {matchDays:[{businessDate,matches:[...]}]}；odds_today 为扁平 matches
            buckets = [blob] + list(blob.get("matchDays") or [])
            for b in buckets:
                for m in b.get("matches") or []:
                    mid = m.get("matchNumStr") or m.get("code")
                    if mid:
                        pool.setdefault(mid, {"had": m.get("had"), "hhad": m.get("hhad")})
        archives[day] = pool
        return pool

    solved = 0
    for r in rows:
        src = day_odds(r["day"]).get(r["code"]) or {}
        o3 = src.get(r["play"]) or {}
        try:
            trio = [float(o3["h"]), float(o3["d"]), float(o3["a"])]
        except (KeyError, TypeError, ValueError):
            r["pm"] = None
            continue
        slot = PICK_SLOT.get(r["pick"])
        if slot is None or any(x <= 1.0 for x in trio):
            r["pm"] = None
            continue
        inv = [1.0 / x for x in trio]
        r["pm"] = inv[slot] / sum(inv)
        solved += 1
    return solved


def main():
    print("══ v28 线二可行性勘察：赔率进决策层的改动面 ══\n", flush=True)
    rows = []
    for p in sorted(glob.glob(str(ROOT / "data" / "03-predictions" / "*-boldplay.json"))):
        try:
            card = json.loads(Path(p).read_text(encoding="utf-8"))
        except Exception:
            continue
        day = card.get("date") or Path(p).stem.split("-boldplay")[0]
        for leg in ((card.get("tiers") or {}).get("lottery") or {}).get("legs") or []:
            pf, dv = leg.get("p"), leg.get("divergence")
            if not isinstance(pf, (int, float)):
                continue      # modelSupport=none/无库腿：无 p_fused·本就纯赔率入选
            # p_mkt 反推：divergence = |p_fused − p_mkt|·符号未存→两种可能都算，取保守面
            if not isinstance(dv, (int, float)):
                continue
            rows.append({"day": day, "code": leg.get("matchNumStr"), "play": leg.get("play"),
                         "pick": leg.get("pick"), "pf": pf, "dv": dv, "odds": leg.get("odds"),
                         "support": leg.get("modelSupport"),
                         "pm_lo": pf - dv, "pm_hi": pf + dv})

    n = len(rows)
    if not n:
        print("无可勘察腿（历史卡无 p_fused 腿）", flush=True)
        return

    # divergence 只存绝对值·符号靠落盘赔率精确反推：p_mkt = devig(三向)[pick 槽]
    # 卡内腿只存所选项赔率，三向须回查当日 sporttery 存档
    resolved = resolve_pmkt(rows)

    # 门槛翻判：符号已定者直判；仍未定者按两侧可能性分类
    flip_sure, flip_maybe, same = [], [], 0
    for r in rows:
        inn = r["pf"] >= GATE
        if r.get("pm") is not None:
            if (r["pm"] >= GATE) != inn:
                flip_sure.append(r)
            else:
                same += 1
            continue
        lo_f, hi_f = (r["pm_lo"] >= GATE) != inn, (r["pm_hi"] >= GATE) != inn
        if lo_f and hi_f:
            flip_sure.append(r)
        elif lo_f or hi_f:
            flip_maybe.append(r)
        else:
            same += 1
    print(f"   [符号反推] {resolved}/{n} 条由当日赔率存档精确定出 p_mkt", flush=True)

    print(f"① 有 p_fused 的彩票档腿 {n} 条（另有无库腿纯按赔率入选·不受本改动影响）", flush=True)
    print(f"   平均 |p_fused − p_mkt| = {statistics.fmean(r['dv'] for r in rows) * 100:.2f}pp", flush=True)
    print(f"   门槛判定不变 {same} 条（{same / n * 100:.1f}%）", flush=True)
    print(f"   必翻 {len(flip_sure)} 条（{len(flip_sure) / n * 100:.1f}%）"
          f"·符号待定 {len(flip_maybe)} 条（{len(flip_maybe) / n * 100:.1f}%）", flush=True)

    # 支撑分层：a=0 联赛的腿 p_fused≡p_mkt（融合权重为零）→ 换口径天然 no-op
    sup = collections.Counter(r["support"] for r in rows)
    zero_div = sum(1 for r in rows if r["dv"] < 1e-9)
    print(f"\n② modelSupport 分布 {dict(sup)}", flush=True)
    print(f"   divergence≈0 腿 {zero_div} 条（{zero_div / n * 100:.1f}%）"
          f"——a=0 联赛：p_fused≡p_mkt·换口径数学上 no-op", flush=True)

    print(f"\n③ 必翻腿明细（现行入池·市场口径会落池 或 反之）", flush=True)
    for r in flip_sure[:20]:
        direction = "现入→市落" if r["pf"] >= GATE else "现落→市入"
        pm = f"{r['pm']:.3f}" if r.get("pm") is not None else f"±{r['dv']:.3f}"
        print(f"   {r['day']} {r['code']} {r['play']}/{r['pick']} "
              f"p_fused={r['pf']:.3f} p_mkt={pm} @{r['odds']} [{direction}]", flush=True)
    if len(flip_sure) > 20:
        print(f"   …另 {len(flip_sure) - 20} 条", flush=True)

    verdict = ("改动面 <5%：线二为空转——彩票档门槛换市场 p 几无实际影响"
               if (len(flip_sure) + len(flip_maybe)) / n < 0.05 else
               f"改动面 {(len(flip_sure) + len(flip_maybe)) / n * 100:.1f}%：值得立预注册跑影子 A/B")
    print(f"\n══ 勘察结论: {verdict} ══", flush=True)

    OUT.write_text(json.dumps({
        "ranAt": "2026-10-09", "gate": GATE, "nLegsWithFusedP": n,
        "meanDivergencePP": round(statistics.fmean(r["dv"] for r in rows) * 100, 2),
        "sameVerdict": same, "flipSure": len(flip_sure), "flipMaybe": len(flip_maybe),
        "zeroDivergenceLegs": zero_div, "modelSupport": dict(sup),
        "flipDetail": flip_sure, "verdict": verdict,
        "discipline": "勘察性·不作 EV 呈报·不改生产参数",
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"归档 {OUT.relative_to(ROOT)}", flush=True)


if __name__ == "__main__":
    main()
