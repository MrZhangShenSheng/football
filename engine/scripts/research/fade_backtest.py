# -*- coding: utf-8 -*-
"""反差策略（T044买法机械化）90日回测器——预注册舱 fade-strategy-prereg.json v1。

铁律14全套：dc_rolling 防泄漏链 / 判据先落盘 / 三基线强制 / 4串11 封顶结算。
产出 data/04-summaries/fade-strategy-backtest.json。开发者 sszhang
用法：python engine/scripts/research/fade_backtest.py
"""
from __future__ import annotations

import itertools
import json
import random
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import strength_loaders as sl
import strength_chain_eval as sce

ROOT = Path(__file__).resolve().parents[3]
HIST_DIR = ROOT / "engine" / "cache" / "hist_odds"
PREREG = ROOT / "engine" / "cache" / "strength_chain" / "fade-strategy-prereg.json"
OUT = ROOT / "data" / "04-summaries" / "fade-strategy-backtest.json"

WINDOW = ("2025-10-01", "2026-09-28")   # 默认当前段   # v11-S3样本外   # 默认当前段   # v10段2   # 默认当前段   # 舱v9段2样本外   # 舱v9段1   # 预注册舱 v2：一年窗口
MIN_MATCHES = 4
TOP_N_LEGS = 4
UNIT = 2.0
CAP_PER_BET = 500_000.0
BOOM_THRESHOLD = 0.10          # v2 排除：三其他格合计>10% ≈ λ_max>3
N_RANDOM_PERM = 1000
SEED = 7

LEAGUES = ["uefa-nations", "england-premier", "spain-laliga", "germany-bundesliga",
           "italy-serie-a", "france-ligue1", "netherlands-eredivisie", "portugal-primeira",
           "korea", "japan", "denmark", "sweden", "norway", "brazil", "saudi", "usa", "france-ligue2", "world-cup", "world-cup-qual", "euro-qual", "uefa-champions", "uefa-europa",
           "england-championship", "germany-bundesliga2", "spain-liga2", "italy-serie-b", "belgium-first-a", "turkey-super-lig", "greece-super", "SC0"]


def score_to_matrix_key(score: str) -> str:
    """真实比分 → 矩阵键（>5球归三其他·方向判定）。"""
    try:
        h, a = str(score).split(":")
        h, a = int(h), int(a)
    except ValueError:
        return ""
    if h > 5 or a > 5:
        return "s1sh" if h > a else ("s1sa" if a > h else "s1sd")
    return f"s{h:02d}s{a:02d}"


def hist_crs_key_to_matrix(k: str) -> str:
    """crs 池键（'2:1'/'胜其他'）→ 矩阵键。"""
    if k in ("胜其他", "平其他", "负其他"):
        return {"胜其他": "s1sh", "平其他": "s1sd", "负其他": "s1sa"}[k]
    try:
        h, a = k.split(":")
        return f"{int(h):02d}s{int(a):02d}" if False else f"s{int(h):02d}s{int(a):02d}"
    except ValueError:
        return ""


def payout_4s11(legs: list[tuple[str, float]], per_leg_hit: list) -> float:
    """4串11 派彩（11注·单注封顶）。per_leg_hit[i]=第i腿自己的场真实比分是否命中该腿格——
    各腿各判各场·严禁合并比分集合（首版曾跨场串奖致随机基线+19867%·基线疫苗当场抓获）。"""
    ok = per_leg_hit
    pay = 0.0
    for i, j in itertools.combinations(range(4), 2):
        if ok[i] and ok[j]:
            pay += min(UNIT * legs[i][1] * legs[j][1], CAP_PER_BET)
    for i, j, k in itertools.combinations(range(4), 3):
        if ok[i] and ok[j] and ok[k]:
            pay += min(UNIT * legs[i][1] * legs[j][1] * legs[k][1], CAP_PER_BET)
    if all(ok):
        pay += min(UNIT * legs[0][1] * legs[1][1] * legs[2][1] * legs[3][1], CAP_PER_BET)
    return pay




def payout_multi(sel: list, real_keys: list, cap: float = CAP_PER_BET) -> float:
    """复式混串派彩。sel[i]=该场选格[(mk,odds),...]；real_keys[i]=该场真实比分矩阵键。
    2*3*4三层：6对2串1+4组3串1+1注4串1·每注独立·单注封顶。"""
    import itertools as it
    pay = 0.0
    for i, j in it.combinations(range(len(sel)), 2):
        for a in sel[i]:
            for b in sel[j]:
                if a[0] == real_keys[i] and b[0] == real_keys[j]:
                    pay += min(UNIT * a[1] * b[1], cap)
    for i, j, k in it.combinations(range(len(sel)), 3):
        for a in sel[i]:
            for b in sel[j]:
                for c in sel[k]:
                    if a[0] == real_keys[i] and b[0] == real_keys[j] and c[0] == real_keys[k]:
                        pay += min(UNIT * a[1] * b[1] * c[1], cap)
    for a in sel[0]:
        for b in sel[1]:
            for c in sel[2]:
                for dd in sel[3]:
                    if (a[0] == real_keys[0] and b[0] == real_keys[1]
                            and c[0] == real_keys[2] and dd[0] == real_keys[3]):
                        pay += min(UNIT * a[1] * b[1] * c[1] * dd[1], cap)
    return pay


def main() -> None:
    rows = []
    for f in sorted(HIST_DIR.glob("crs_hist_*.json")):
        rows += json.loads(f.read_text(encoding="utf-8"))["matches"]
    rows = [m for m in rows if WINDOW[0] <= str(m.get("date", ""))[:10] <= WINDOW[1]
            and m.get("crs") and m.get("score")]
    by_day = defaultdict(list)
    for m in rows:
        by_day[str(m["date"])[:10]].append(m)

    ctx = sl.build_ctx(LEAGUES)
    zh2id = sl.zh_to_id()
    memo: dict = {}
    memo_hard: dict = {}

    days = []
    stats = {"daysTotal": len(by_day), "daysTraded": 0, "daysSkippedLt4": 0,
             "daysSkippedNoPick": 0, "matchesPredicted": 0, "matchesNoLeague": 0}
    for day in sorted(by_day):
        day_rows = by_day[day]
        if len(day_rows) < MIN_MATCHES:
            stats["daysSkippedLt4"] += 1
            continue
        as_of = date.fromisoformat(day)
        cands = []                                    # 每场：所有格EV+模型P+备选格
        for m in day_rows:
            hid, aid = zh2id.get(m["home"]), zh2id.get(m["away"])
            if not hid or not aid:
                stats["matchesNoLeague"] += 1
                continue
            pred = sce._predict_match(hid, aid, as_of, ctx, memo, beta=0.05)
            if not pred or not pred.get("matrix"):
                stats["matchesNoLeague"] += 1
                continue
            stats["matchesPredicted"] += 1
            matrix = pred["matrix"]
            crs = m["crs"]
            cells = []
            for ck, ov in crs.items():
                mk = hist_crs_key_to_matrix(str(ck))
                if not mk or mk not in matrix:
                    continue
                try:
                    o = float(ov)
                except (TypeError, ValueError):
                    continue
                if o <= 1.0:
                    continue
                cells.append({"mk": mk, "odds": o, "p": matrix[mk], "ev": matrix[mk] * o - 1.0})
            if not cells:
                continue
            cells.sort(key=lambda c: -c["ev"])
            boom = sum(matrix.get(k, 0.0) for k in ("s1sh", "s1sd", "s1sa"))
            cands.append({"m": m, "cells": cells, "boomP": boom})
        if len(cands) < MIN_MATCHES:
            stats["daysSkippedNoPick"] += 1
            continue

        hit_keys_by_m = {id(c): {score_to_matrix_key(str(c["m"]["score"]))} for c in cands}

        def settle_generic(selected):
            """selected=[(原cand引用, cell)]——每腿只对自己的场判命中。"""
            legs = [(cell["mk"], cell["odds"]) for c, cell in selected]
            per_hit = [cell["mk"] in hit_keys_by_m[id(c)] for c, cell in selected]
            return payout_4s11(legs, per_hit)

        # V1 主策略：场分(最高EV)top4
        ranked = sorted(cands, key=lambda c: -c["cells"][0]["ev"])[:TOP_N_LEGS]
        ranked_sel = [(c, c["cells"][0]) for c in ranked]
        # V2 对照：排除爆炸场后 top4
        non_boom = [c for c in cands if c["boomP"] <= BOOM_THRESHOLD]
        ranked2 = sorted(non_boom, key=lambda c: -c["cells"][0]["ev"])[:TOP_N_LEGS]
        ranked2_sel = [(c, c["cells"][0]) for c in ranked2]

        # 基线1：市场最热格（每场最低价格）4串11
        market_sel = [(c, min(c["cells"], key=lambda x: x["odds"]))
                      for c in sorted(cands, key=lambda c: min(x["odds"] for x in c["cells"]))[:TOP_N_LEGS]]
        # 基线2：模型top概率格（非EV）
        topp_sel = [(c, max(c["cells"], key=lambda x: x["p"]))
                    for c in sorted(cands, key=lambda c: -max(x["p"] for x in c["cells"]))[:TOP_N_LEGS]]
        # V3 正向主策略（舱v4）：模型P最高格·场分=P·top4
        v3_sel = topp_sel

        # V3W 腿级去水版（舱v9）：三水排除（λ差>1.5·λ和>3.0·HAD<1.3）后 topP top4
        def lam_of(c):
            # 从矩阵反推 λ 近似：boomP>0.10 已知≈λmax>3；这里直接用 had 主胜近似超热
            return None
        def is_water(c, m):
            try: hh = float(m['had']['h'])
            except (TypeError, ValueError, KeyError): hh = None
            if hh is not None and hh < 1.3: return True
            return c['boomP'] > 0.10        # λmax>3 代理（λ和/λ差的稳定代理·爆炸格合计）
        # v10 硬仗口径候选（预选赛库剔除·俱乐部库照收·对 cands 平行重算）
        hard_cands = []
        for m in day_rows:
            hid, aid = zh2id.get(m["home"]), zh2id.get(m["away"])
            if not hid or not aid: continue
            pred = sce._predict_match(hid, aid, as_of, ctx, memo_hard, beta=0.05, hard_only=True)
            if not pred or not pred.get("matrix"): continue
            cells = []
            for ck, ov in (m.get("crs") or {}).items():
                mk = hist_crs_key_to_matrix(str(ck))
                if not mk or mk not in pred["matrix"]: continue
                try: o = float(ov)
                except (TypeError, ValueError): continue
                if o > 1.0:
                    cells.append({"mk": mk, "odds": o, "p": pred["matrix"][mk], "ev": pred["matrix"][mk]*o-1.0})
            if cells:
                boom = sum(pred["matrix"].get(k, 0.0) for k in ("s1sh", "s1sd", "s1sa"))
                hard_cands.append({"m": m, "cells": sorted(cells, key=lambda c: -c["ev"]), "boomP": boom})
        # V3H/V3WH v10 硬仗口径（预选赛剔除）
        h_hit = {id(c): {score_to_matrix_key(str(c["m"]["score"]))} for c in hard_cands}
        def settle_h(selected):
            legs = [(cell["mk"], cell["odds"]) for c, cell in selected]
            per = [cell["mk"] in h_hit[id(c)] for c, cell in selected]
            return payout_4s11(legs, per)
        v3h_sel = ([(c, max(c["cells"], key=lambda x: x["p"]))
                    for c in sorted(hard_cands, key=lambda c: -max(x["p"] for x in c["cells"]))[:TOP_N_LEGS]]
                   if len(hard_cands) >= TOP_N_LEGS else None)
        hc_clean = [c for c in hard_cands if not is_water(c, c['m'])]
        v3wh_sel = ([(c, max(c["cells"], key=lambda x: x["p"]))
                     for c in sorted(hc_clean, key=lambda c: -max(x["p"] for x in c["cells"]))[:TOP_N_LEGS]]
                    if len(hc_clean) >= TOP_N_LEGS else None)
        clean_cands = [c for c in cands if not is_water(c, c['m'])]
        w_sel = ([(c, max(c["cells"], key=lambda x: x["p"]))
                  for c in sorted(clean_cands, key=lambda c: -max(x["p"] for x in c["cells"]))[:TOP_N_LEGS]]
                 if len(clean_cands) >= TOP_N_LEGS else None)
        # V6 修复集中（舱v6）：场选择 topP top4 · 格选择 = 场内 max(P×odds)（cells[0]）
        v6_sel = [(c, c["cells"][0]) for c in sorted(cands, key=lambda c: -max(x["p"] for x in c["cells"]))[:TOP_N_LEGS]]
        # V4/V5 复式混串（舱v5）：topP 4场·每场 P 最高的 2/3 格
        top_picks = sorted(cands, key=lambda c: -max(x["p"] for x in c["cells"]))[:TOP_N_LEGS]
        by_p = lambda c: sorted(c["cells"], key=lambda x: -x["p"])
        real_keys = [score_to_matrix_key(str(c["m"]["score"])) for c in top_picks]
        v4_sel = [[(x["mk"], x["odds"]) for x in by_p(c)[:2]] for c in top_picks]
        v5_sel = [[(x["mk"], x["odds"]) for x in by_p(c)[:3]] for c in top_picks]
        n_v4 = (sum(len(v4_sel[i]) * len(v4_sel[j]) for i, j in itertools.combinations(range(4), 2))
                + sum(len(v4_sel[i]) * len(v4_sel[j]) * len(v4_sel[k]) for i, j, k in itertools.combinations(range(4), 3))
                + 16)
        # D1-D6 纯4串1复式设计扫描（舱v7·含明细）
        def pure4(picks_per_match, real_keys, fields):
            pay, bets, detail = 0.0, 1, None
            for lst in picks_per_match:
                bets *= len(lst)
            for a in picks_per_match[0]:
                for b in picks_per_match[1]:
                    for c in picks_per_match[2]:
                        for e in picks_per_match[3]:
                            if (a[0] == real_keys[0] and b[0] == real_keys[1]
                                    and c[0] == real_keys[2] and e[0] == real_keys[3]):
                                gross = UNIT * a[1] * b[1] * c[1] * e[1]
                                pay += min(gross, CAP_PER_BET)
                                detail = {"legs": [
                                    {"match": f"{fields[i]['m']['home']}v{fields[i]['m']['away']}",
                                     "hit": g[0], "odds": g[1]}
                                    for i, g in enumerate((a, b, c, e))],
                                    "gross": round(gross, 1), "paid": round(min(gross, CAP_PER_BET), 1)}
            return pay, bets, detail
        designs = {}
        for name, k in (("D1_top2", 2), ("D2_top3", 3), ("D3_top4", 4), ("D4_top5", 5)):
            sel = [[(x["mk"], x["odds"]) for x in by_p(c)[:k]] for c in top_picks]
            pay, bets, detail = pure4(sel, real_keys, top_picks)
            designs[name] = {"stake": bets * UNIT, "pay": pay, "bets": bets, "hitDetail": detail}
        for name, nmid in (("D5_hot2mid1", 1), ("D6_hot2mid2", 2)):
            sel = []
            for c in top_picks:
                cells = by_p(c)
                hot = [(x["mk"], x["odds"]) for x in cells[:2]]
                mids = [(x["mk"], x["odds"]) for x in cells[2:] if x["odds"] >= 10.0][:nmid]
                sel.append(hot + (mids or [(cells[2]["mk"], cells[2]["odds"])]))
            pay, bets, detail = pure4(sel, real_keys, top_picks)
            designs[name] = {"stake": bets * UNIT, "pay": pay, "bets": bets, "hitDetail": detail}
        days.append({
            "day": day,
            "nCands": len(cands),
            "v1": {"stake": 22.0, "pay": settle_generic(ranked_sel),
                   "legs": [{"match": f"{c['m']['home']}v{c['m']['away']}", "pick": c["cells"][0]["mk"],
                             "odds": c["cells"][0]["odds"], "p": round(c["cells"][0]["p"], 4),
                             "ev": round(c["cells"][0]["ev"], 3), "boomP": round(c["boomP"], 3)} for c in ranked]},
            "v2": ({"stake": 22.0, "pay": settle_generic(ranked2_sel)} if len(ranked2_sel) == TOP_N_LEGS else None),
            "v3": {"stake": 22.0, "pay": settle_generic(v3_sel),
                   "legs": [{"match": f"{c['m']['home']}v{c['m']['away']}", "pick": cell["mk"],
                             "odds": cell["odds"], "p": round(cell["p"], 4),
                             "hit": cell["mk"] in hit_keys_by_m[id(c)]}
                            for c, cell in v3_sel]},
            "designs": designs,
            "v3h": ({"stake": 22.0, "pay": settle_h(v3h_sel),
                    "legs": [{"hit": cell["mk"] in h_hit[id(c)]} for c, cell in v3h_sel]}
                   if v3h_sel else None),
            "v3wh": ({"stake": 22.0, "pay": settle_h(v3wh_sel)} if v3wh_sel else None),
            "v3w": ({"stake": 22.0, "pay": settle_generic(w_sel),
                     "legs": [{"match": f"{c['m']['home']}v{c['m']['away']}", "pick": cell["mk"],
                               "odds": cell["odds"], "p": round(cell["p"], 4),
                               "hit": cell["mk"] in hit_keys_by_m[id(c)]}
                              for c, cell in w_sel]} if w_sel else None),
            "v6": {"stake": 22.0, "pay": settle_generic(v6_sel),
                   "legs": [{"match": f"{c['m']['home']}v{c['m']['away']}", "pick": cell["mk"],
                             "odds": cell["odds"], "p": round(cell["p"], 4),
                             "hit": cell["mk"] in hit_keys_by_m[id(c)]}
                            for c, cell in v6_sel]},
            "v4": {"stake": n_v4 * UNIT, "pay": payout_multi(v4_sel, real_keys), "bets": n_v4},
            "v5": {"stake": 243 * UNIT, "pay": payout_multi(v5_sel, real_keys)},
            "baseMarket": {"stake": 22.0, "pay": settle_generic(market_sel)},
            "baseTopP": {"stake": 22.0, "pay": settle_generic(topp_sel)},
            "cands": cands,          # 供随机基线
        })
        stats["daysTraded"] += 1

    # 基线3：随机选腿 1000 次
    rng = random.Random(SEED)
    rand_pays = []
    for _ in range(N_RANDOM_PERM):
        tot = 0.0
        for d in days:
            cs = d["cands"]
            sel = rng.sample(cs, TOP_N_LEGS)
            picks = [rng.choice(c["cells"]) for c in sel]
            legs = [(pk["mk"], pk["odds"]) for pk in picks]
            per_hit = [pk["mk"] == score_to_matrix_key(str(c["m"]["score"]))
                       for c, pk in zip(sel, picks)]
            tot += payout_4s11(legs, per_hit)
        rand_pays.append(tot)

    def agg(key):
        stake = sum(d[key]["stake"] for d in days if d.get(key))
        pay = sum(d[key]["pay"] for d in days if d.get(key))
        return {"stake": stake, "pay": pay, "roi": (pay - stake) / stake if stake else None,
                "hitDays": sum(1 for d in days if d.get(key) and d[key]["pay"] > 0)}
    v3 = agg("v3")
    v3w = agg("v3w")
    v3h = agg("v3h")
    v3wh = agg("v3wh")
    des = {}
    for name in ("D1_top2", "D2_top3", "D3_top4", "D4_top5", "D5_hot2mid1", "D6_hot2mid2"):
        st = sum(dd["designs"][name]["stake"] for dd in days)
        pay = sum(dd["designs"][name]["pay"] for dd in days)
        des[name] = {"stake": st, "pay": pay, "roi": (pay - st) / st if st else None,
                     "hitDays": sum(1 for dd in days if dd["designs"][name]["hitDetail"]),
                     "bigHits": sum(1 for dd in days if dd["designs"][name]["pay"] >= 100000)}
    v6 = agg("v6")
    v4, v5 = agg("v4"), agg("v5")

    v1, v2 = agg("v1"), agg("v2")
    bm, bp = agg("baseMarket"), agg("baseTopP")
    total_stake = v1["stake"]
    rand_mean = sum(rand_pays) / len(rand_pays)
    rand_pays.sort()
    v1_pay = v1["pay"]
    pct = sum(1 for x in rand_pays if x <= v1_pay) / len(rand_pays) * 100

    # bootstrap CI（日收益序列）
    day_nets = [d["v1"]["pay"] - d["v1"]["stake"] for d in days]
    bs = []
    for _ in range(1000):
        bs.append(sum(rng.choice(day_nets) for _ in day_nets) / len(day_nets) * len(day_nets))
    bs.sort()

    result = {
        "generatedAt": "2026-10-05", "prereg": {"version": 1, "path": str(PREREG)},
        "window": WINDOW, "stats": stats,
        "v1_main": v1, "designScan": des, "v3_topP": v3, "v3w_dewater": v3w, "v3h_hard": v3h, "v3wh_hardDW": v3wh, "v6_shareArgmax": v6, "v4_multi2": v4, "v5_multi3": v5, "v2_noBoom": v2,
        "baselines": {"randomShuffle": {"meanPay": rand_mean, "percentileOfV1": pct,
                                        "roi": (rand_mean - total_stake) / total_stake},
                      "marketHottest": bm, "modelTopP": bp},
        "bootstrap": {"totalNetCI": [bs[49], bs[949]], "median": bs[500]},
        "days": [{k: d[k] for k in ("day", "nCands", "v1", "v3", "v3h", "v3wh", "v3w", "v6", "designs", "v4", "v5", "v2", "baseMarket", "baseTopP")} for d in days],
        "notes": ["结算价=crs末档价(停售前)·非出票时点价(口径限制)", "V2阈值由T044单例反推·过拟合风险声明",
                  "随机基线每腿从该场全部有价格格随机取(非仅EV格)"],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"==== 反差策略回测 {WINDOW[0]}~{WINDOW[1]} ====")
    print(f"交易日 {stats['daysTraded']}/{stats['daysTotal']} · 预测 {stats['matchesPredicted']} 场(可算域)")
    print(f"V1 纯机械: 投入 {v1['stake']:.0f} 回款 {v1['pay']:.0f} ROI {(v1['pay']-v1['stake'])/v1['stake']*100:+.1f}% · 回款日 {v1['hitDays']}")
    if v3w.get("roi") is not None:
        wl = [(dd['day'], l) for dd in days if dd.get('v3w') for l in dd['v3w']['legs']]
        wh = sum(l['hit'] for _, l in wl) / max(len(wl), 1)
        print(f"V3W 去水版: 投入 {v3w['stake']:.0f} 回款 {v3w['pay']:.0f} ROI {v3w['roi']*100:+.1f}% · 回款日 {v3w['hitDays']} · 腿命中 {wh*100:.1f}%")
    if v3h.get("roi") is not None:
        hl = [(dd['day'], l) for dd in days if dd.get('v3h') for l in dd['v3h']['legs']]
        hh = sum(l['hit'] for _, l in hl)/max(len(hl),1)
        print(f"V3H 硬仗口径: 投入 {v3h['stake']:.0f} 回款 {v3h['pay']:.0f} ROI {v3h['roi']*100:+.1f}% · 回款日 {v3h['hitDays']} · 腿命中 {hh*100:.1f}%")
    if v3wh.get("roi") is not None:
        print(f"V3WH 硬仗+去水: 投入 {v3wh['stake']:.0f} 回款 {v3wh['pay']:.0f} ROI {v3wh['roi']*100:+.1f}% · 回款日 {v3wh['hitDays']}")
    print(f"V3 单选top1: 投入 {v3['stake']:.0f} 回款 {v3['pay']:.0f} ROI {v3['roi']*100:+.1f}% · 回款日 {v3['hitDays']}")
    print(f"V6 修复集中: 投入 {v6['stake']:.0f} 回款 {v6['pay']:.0f} ROI {v6['roi']*100:+.1f}% · 回款日 {v6['hitDays']}")
    print(f"V4 复式top2: 投入 {v4['stake']:.0f} 回款 {v4['pay']:.0f} ROI {v4['roi']*100:+.1f}% · 回款日 {v4['hitDays']}")
    print(f"V5 复式top3: 投入 {v5['stake']:.0f} 回款 {v5['pay']:.0f} ROI {v5['roi']*100:+.1f}% · 回款日 {v5['hitDays']}")
    print(f"V2 排爆炸: 投入 {v2['stake']:.0f} 回款 {v2['pay']:.0f} ROI {v2['roi']*100:+.1f}%")
    print(f"基线 市场: ROI {bm['roi']*100:+.1f}% · 模型topP: ROI {bp['roi']*100:+.1f}% · 随机: ROI {(rand_mean-total_stake)/total_stake*100:+.1f}%(V1百分位{pct:.1f})")
    print(f"bootstrap净额CI: [{bs[49]:.0f}, {bs[949]:.0f}]")


if __name__ == "__main__":
    main()
