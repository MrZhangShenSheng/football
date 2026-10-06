# -*- coding: utf-8 -*-
"""V3W-v2（boom=0.05·had_hot=1.5·HST链）两段完整明细——ROI+腿+回款日+月度分解。开发者 sszhang"""
import sys, json
from pathlib import Path
from datetime import date
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import strength_loaders as sl
import strength_chain_eval as sce

ROOT = Path(__file__).resolve().parents[3]
HIST = ROOT / "engine" / "cache" / "hist_odds"
TOP_N = 4
UNIT = 2.0
CAP = 500_000.0
LEAGUES = ["uefa-nations","england-premier","spain-laliga","germany-bundesliga","italy-serie-a",
           "france-ligue1","netherlands-eredivisie","portugal-primeira","korea","japan",
           "denmark","sweden","norway","brazil","saudi","usa","france-ligue2",
           "world-cup","world-cup-qual","euro-qual","uefa-champions","uefa-europa"]

def score_to_matrix_key(score):
    try: h, a = str(score).split(":"); h, a = int(h), int(a)
    except ValueError: return ""
    if h > 5 or a > 5: return "s1sh" if h > a else ("s1sa" if a > h else "s1sd")
    return f"s{h:02d}s{a:02d}"

def hist_crs_key_to_matrix(k):
    if k in ("胜其他","平其他","负其他"): return {"胜其他":"s1sh","平其他":"s1sd","负其他":"s1sa"}[k]
    try: h, a = k.split(":"); return f"s{int(h):02d}s{int(a):02d}"
    except ValueError: return ""

def payout_4s11(legs, per):
    import itertools
    pay = 0.0
    for i, j in itertools.combinations(range(4), 2):
        if per[i] and per[j]: pay += min(UNIT*legs[i][1]*legs[j][1], CAP)
    for i, j, k in itertools.combinations(range(4), 3):
        if per[i] and per[j] and per[k]: pay += min(UNIT*legs[i][1]*legs[j][1]*legs[k][1], CAP)
    if all(per): pay += min(UNIT*legs[0][1]*legs[1][1]*legs[2][1]*legs[3][1], CAP)
    return pay

def k2s(k):
    return {'s1sh':'胜其他','s1sd':'平其他','s1sa':'负其他'}.get(k, f"{int(k[1:3])}:{int(k[4:6])}")

def full_run(window, boom=0.05, had_hot=1.5):
    lo, hi = window
    rows = []
    for f in sorted(HIST.glob("crs_hist_*.json")):
        rows += json.loads(f.read_text(encoding="utf-8"))["matches"]
    rows = [m for m in rows if lo <= str(m.get("date",""))[:10] <= hi and m.get("crs") and m.get("score")]
    by_day = {}
    for m in rows:
        by_day.setdefault(str(m["date"])[:10], []).append(m)
    ctx = sl.build_ctx(LEAGUES)
    z2i = sl.zh_to_id()
    memo = {}
    out = []
    for day in sorted(by_day):
        day_rows = by_day[day]
        if len(day_rows) < TOP_N: continue
        as_of = date.fromisoformat(day)
        cands = []
        for m in day_rows:
            hid, aid = z2i.get(m["home"]), z2i.get(m["away"])
            if not hid or not aid: continue
            pred = sce._predict_match(hid, aid, as_of, ctx, memo, beta=0.05)
            if not pred or not pred.get("matrix"): continue
            cells = []
            for ck, ov in (m.get("crs") or {}).items():
                mk = hist_crs_key_to_matrix(str(ck))
                if not mk or mk not in pred["matrix"]: continue
                try: o = float(ov)
                except (TypeError, ValueError): continue
                if o > 1.0:
                    cells.append({"mk": mk, "odds": o, "p": pred["matrix"][mk]})
            if cells:
                bp = sum(pred["matrix"].get(k, 0.0) for k in ("s1sh","s1sd","s1sa"))
                try: hh = float(m["had"]["h"])
                except (TypeError, ValueError, KeyError): hh = None
                if hh is not None and hh < had_hot: continue
                if bp > boom: continue
                cands.append({"m": m, "cells": cells, "real": score_to_matrix_key(str(m["score"]))})
        if len(cands) < TOP_N: continue
        sel = sorted(cands, key=lambda c: -max(x["p"] for x in c["cells"]))[:TOP_N]
        tc = [max(c["cells"], key=lambda x: x["p"]) for c in sel]
        legs = [(c["mk"], c["odds"]) for c in tc]
        per = [tc[i]["mk"] == sel[i]["real"] for i in range(4)]
        pay = payout_4s11(legs, per)
        out.append({"day": day, "stake": 22.0, "pay": pay, "net": pay - 22.0,
                    "legs": [{"match": f"{c['m']['home']}v{c['m']['away']}", "pick": t["mk"],
                              "odds": t["odds"], "p": round(t["p"], 4), "hit": h}
                             for c, t, h in zip(sel, tc, per)]})
    return out

def report(days, label):
    stake = sum(d["stake"] for d in days)
    pay = sum(d["pay"] for d in days)
    hitdays = [d for d in days if d["pay"] > 0]
    legs_all = [(d["day"], l) for d in days for l in d["legs"]]
    hits = sum(1 for _, l in legs_all if l["hit"])
    print(f"\n{'='*72}")
    print(f"V3W-v2（boom=0.05·had_hot=1.5·HST链）· {label}")
    print(f"{'='*72}")
    print(f"交易日 {len(days)} · 投入 {stake:,.0f} · 回款 {pay:,.0f} · ROI {(pay-stake)/stake*100:+.1f}%")
    print(f"腿总数 {len(legs_all)} · 命中 {hits} ({hits/len(legs_all)*100:.1f}%) · 回款日 {len(hitdays)}/{len(days)}")
    if hitdays:
        print(f"\n回款日明细:")
        for d in hitdays:
            marks = " ".join(f"{'✅' if l['hit'] else '×'}{l['match'].split('v')[0][:8]} {k2s(l['pick'])}@{l['odds']:.1f}"
                             for l in d["legs"])
            print(f"  {d['day']} 回{d['pay']:5.0f}元: {marks}")
    monthly = defaultdict(lambda: [0.0, 0.0, 0])
    for d in days:
        m = d["day"][:7]
        monthly[m][0] += d["pay"]; monthly[m][1] += 22.0; monthly[m][2] += 1
    print(f"\n月度分解:")
    for m in sorted(monthly):
        p, s, n = monthly[m]
        print(f"  {m}: {p:5.0f}/{s:4.0f} = {p/s*100-100:+6.1f}% ({n}日)")

if __name__ == "__main__":
    fit = full_run(("2024-01-01", "2025-06-30"))
    report(fit, "拟合段(2024-01~2025-06)")
    val = full_run(("2025-10-01", "2026-09-28"))
    report(val, "验证段(2025-10~2026-09)")
