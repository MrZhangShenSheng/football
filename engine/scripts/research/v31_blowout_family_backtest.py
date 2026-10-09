# -*- coding: utf-8 -*-
r"""v31 · 大比分族包批次回测（离散模型线·2026-10-09）

预注册（跑前写死）: engine/cache/strength_chain/fade-strategy-prereg.json → v31BlowoutFamily
设计档: docs/2026-10-09-v31-blowout-design.html

赌题: 胆级场(封盘had去水p>=0.75)大比分频率2.05×基线 × v30先验悬殊格r≈0.49 → 族包r≈1.00贴线·本批量化
Spec: S1胆级CRS窄族包(3:0+4:0+3:1+胜其他) / S2宽族包(+2:0+4:1) / S3标准级窄族包 / S4胆级TTG大档(4-7球) / S5胆级小比分top2对照
结算: 每格2元独立单关·realized ROI=Σ中奖/Σ投注-1·体彩真实价
判据: ①CI下限>0过线接影子 ②全负盖棺与v30合流 ③5spec全报不挑·假阳0.25对账

审计三件套: 逐票stdout + tickets[]落盘 + --audit复算重放

用法: python engine/scripts/research/v31_blowout_family_backtest.py [--audit]
开发者 sszhang
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
HIST = ROOT / "engine" / "cache" / "hist_odds"
SUMM = ROOT / "data" / "04-summaries"
OUT_JSON = SUMM / "v31-blowout-family-backtest.json"
OUT_HTML = SUMM / "2026-10-09-v31-blowout-review.html"

# ---- 预注册常量 ----
DAN_P, STD_P = 0.75, 0.60
STAKE_CELL = 2.0
N_BOOT, BOOT_SEED = 2000, 20261009
V30_PRIOR_R = 0.49              # v30 悬殊格先验（净胜3档）
NARROW_H = ["3:0", "4:0", "3:1", "other_h"]
NARROW_A = ["0:3", "0:4", "1:3", "other_a"]
WIDE_EXTRA_H = ["2:0", "4:1"]
WIDE_EXTRA_A = ["0:2", "1:4"]
TTG_BIG = ["4", "5", "6", "7"]
SPEC_LIST = ["S1", "S2", "S3", "S4", "S5"]


def load_matches():
    rows = []
    for f in sorted(HIST.glob("crs_hist_*.json")):
        rows += json.loads(f.read_text(encoding="utf-8"))["matches"]
    out = []
    for m in rows:
        had, crs, ttg, sc = m.get("had"), m.get("crs"), m.get("ttg"), str(m.get("score") or "")
        if not (had and had.get("h") and crs and ":" in sc):
            continue
        try:
            h, a = map(int, sc.split(":"))
        except ValueError:
            continue
        oh, od, oa = float(had["h"]), float(had["d"]), float(had["a"])
        inv = [1 / oh, 1 / od, 1 / oa]
        s = sum(inv)
        p = max(i / s for i in inv)
        fav = inv.index(max(inv))                     # 0主/1平/2客（平为热门=极罕见·CRS族spec跳过该场）
        out.append({"date": str(m.get("date", ""))[:10], "home": m.get("home", "?"), "away": m.get("away", "?"),
                    "league": m.get("league", "?"), "p": p, "fav": fav, "score": sc,
                    "h": h, "a": a, "crs": crs, "ttg": ttg or {}})
    return out


def cells_for(spec, fav):
    """→ 该 spec 的格键列表（按热门侧镜像）。"""
    if spec in ("S1", "S3"):
        return NARROW_H if fav == 0 else NARROW_A
    if spec == "S2":
        return (NARROW_H + WIDE_EXTRA_H) if fav == 0 else (NARROW_A + WIDE_EXTRA_A)
    if spec == "S4":
        return TTG_BIG
    if spec == "S5":
        return ["1:1", "1:0"] if fav == 0 else ["1:1", "0:1"]
    raise ValueError(spec)


def pool_for(spec):
    return DAN_P if spec != "S3" else STD_P


def settle(m, spec):
    """→ (格清单带赔率, 投注, 中奖, 命中格)。TTG 走 ttg 池·CRS 走 crs 池。
    other_* 判中=体彩规则：胜其他=主胜且比分在 5:5 网格外（任一侧>5）。"""
    if spec != "S4" and m["fav"] == 1:                # 平热门：族包无方向侧·整场跳过
        return [], 0.0, 0.0, []
    keys = cells_for(spec, m["fav"])
    book = m["ttg"] if spec == "S4" else m["crs"]
    cells, stake, pay, hits = [], 0.0, 0.0, []
    for k in keys:
        o = book.get(k)
        if not o:
            continue
        o = float(o)
        if o <= 1.0:
            continue
        if k == "other_h":
            hit = m["h"] > m["a"] and (m["h"] > 5 or m["a"] > 5)
        elif k == "other_d":
            hit = m["h"] == m["a"] and m["h"] > 5
        elif k == "other_a":
            hit = m["a"] > m["h"] and (m["h"] > 5 or m["a"] > 5)
        elif spec == "S4":
            hit = (k == str(m["h"] + m["a"]))
        else:
            hit = (k == m["score"])
        stake += STAKE_CELL
        pay += STAKE_CELL * o if hit else 0.0
        if hit:
            hits.append(k)
        cells.append({"cell": k, "odds": o, "hit": hit})
    return cells, stake, pay, hits


def run():
    ms = load_matches()
    print(f"宇宙: {len(ms)} 场 · 胆级 {sum(1 for m in ms if m['p'] >= DAN_P)} · 标准级 {sum(1 for m in ms if STD_P <= m['p'] < DAN_P)}")
    tickets = []
    summaries = {}
    print("=" * 72)
    for spec in SPEC_LIST:
        pool_p = pool_for(spec)
        sel = [m for m in ms if m["p"] >= pool_p] if spec != "S3" else [m for m in ms if STD_P <= m["p"] < DAN_P]
        spec_tk = []
        stake = pay = 0.0
        fam_hit = 0
        implied_sum = 0.0
        implied_n = 0
        for m in sel:
            cells, s_, p_, hits = settle(m, spec)
            if not cells:
                continue
            stake += s_
            pay += p_
            fam_hit += bool(hits)
            implied_sum += sum(1 / c["odds"] for c in cells)
            implied_n += 1
            spec_tk.append({"date": m["date"], "match": f"{m['home']} vs {m['away']}", "league": m["league"],
                            "spec": spec, "cells": cells, "stake": round(s_, 2), "payout": round(p_, 2),
                            "hit": bool(hits), "hits": hits, "score": m["score"]})
        tickets += spec_tk
        n = len(spec_tk)
        roi = pay / stake - 1 if stake else None
        # bootstrap（场重抽样）
        rng = np.random.RandomState(BOOT_SEED)
        rois = []
        if n:
            st = np.array([t["stake"] for t in spec_tk])
            pa = np.array([t["payout"] for t in spec_tk])
            for _ in range(N_BOOT):
                idx = rng.randint(0, n, n)
                ss, pp = st[idx].sum(), pa[idx].sum()
                rois.append(pp / ss - 1 if ss else 0.0)
            rois.sort()
        summaries[spec] = {
            "nMatches": n, "stake": round(stake, 2), "payout": round(pay, 2),
            "roi": round(roi, 4) if roi is not None else None,
            "rMeasured": round(roi + 1, 4) if roi is not None else None,
            "familyHitRate": round(fam_hit / n, 4) if n else None,
            "impliedSum": round(implied_sum / implied_n, 4) if implied_n else None,
            "boot": {"p2.5": round(rois[int(0.025 * N_BOOT)], 4), "p50": round(rois[N_BOOT // 2], 4),
                     "p97.5": round(rois[int(0.975 * N_BOOT)], 4)} if rois else None,
        }
        s = summaries[spec]
        print(f"\n[{spec}] {n}场 投入{stake:.0f} 中奖{pay:.2f} realized ROI {roi:+.1%} (r={s['rMeasured']})"
              f" · 族命中率{s['familyHitRate']:.1%} vs 隐含{s['impliedSum']:.1%}"
              f" · CI[{s['boot']['p2.5']:+.1%},{s['boot']['p97.5']:+.1%}]")
        for t in spec_tk[:3]:
            print(f"    样例 {t['date']} {t['match']}: {'/'.join(c['cell'] + '@' + format(c['odds'], '.1f') for c in t['cells'])} → {t['score']} {'✓' + ','.join(t['hits']) if t['hit'] else '✗'}")
        if n > 3:
            print(f"    ... 其余 {n - 3} 场见 tickets[]")

    # 判据（预注册·机械应用）
    crit = {sp: {"ciLow": summaries[sp]["boot"]["p2.5"] if summaries[sp]["boot"] else None,
                 "pass": bool(summaries[sp]["boot"] and summaries[sp]["boot"]["p2.5"] > 0)} for sp in SPEC_LIST}
    n_pass = sum(c["pass"] for c in crit.values())
    exp_fp = round(0.05 * len(SPEC_LIST), 2)
    verdict = (f"PASS({'+'.join(sp for sp in SPEC_LIST if crit[sp]['pass'])} CI下限>0 → 建每日影子spec·预注册转正)"
               if n_pass else "SEALED(全spec CI含0或负 → 胆级选择救不动悬殊格r·与v30合流盖棺)")

    result = {"ranAt": "2026-10-09", "script": str(Path(__file__).relative_to(ROOT)),
              "preReg": "fade-strategy-prereg v31BlowoutFamily（跑前写死·c2fbc07）",
              "data": {"universe": len(ms), "dan": sum(1 for m in ms if m['p'] >= DAN_P),
                       "std": sum(1 for m in ms if STD_P <= m['p'] < DAN_P)},
              "v30Prior": {"r_blowout_cells": V30_PRIOR_R, "note": "族包r实测对账"},
              "strategies": summaries, "criteria": crit,
              "multipleTesting": {"specs": len(SPEC_LIST), "expectedFP": exp_fp, "note": "全报不挑·探针看过胆级带故此闸强制"},
              "verdict": verdict,
              "audit": {}, "tickets": tickets}
    n_tk = len(tickets)
    stake_all = sum(t["stake"] for t in tickets)
    pay_all = sum(t["payout"] for t in tickets)
    result["audit"]["reconcile"] = (f"5spec共{n_tk}票(场)·格投注合计{stake_all:.0f}元·中奖合计{pay_all:.2f}元"
                                    f"·总realized ROI {pay_all/stake_all-1:+.2%}")
    result["audit"]["recompute"] = audit_recompute(result, verbose=False)
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    OUT_HTML.write_text(gen_html(result), encoding="utf-8")
    print("\n" + "=" * 72)
    print(f"多重检验: 过线 {n_pass}/{len(SPEC_LIST)} vs 期望假阳 {exp_fp}")
    print(f"v30先验对账: S1窄族包 r={summaries['S1']['rMeasured']} vs 全池先验 {V30_PRIOR_R}")
    print(f"终局: {verdict}")
    print(f"产物: {OUT_JSON.name} + {OUT_HTML.name}")
    return result


def gen_html(r):
    rows = "".join(
        f"<tr><td>{sp}</td><td class='num'>{s['nMatches']}</td><td class='num'>{s['stake']:.0f}</td>"
        f"<td class='num'>{s['payout']:.2f}</td>"
        f"<td class='num {'ok' if s['roi'] > 0 else 'bad'}'>{s['roi']:+.1%}</td>"
        f"<td class='num'>{s['rMeasured']}</td><td class='num'>{s['familyHitRate']:.1%}</td>"
        f"<td class='num'>{s['impliedSum']:.1%}</td>"
        f"<td class='num'>{s['boot']['p2.5']:+.1%}~{s['boot']['p97.5']:+.1%}</td></tr>"
        for sp, s in r["strategies"].items())
    v_cls = "ok" if r["verdict"].startswith("PASS") else "bad"
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>v31 大比分族包回测 · 审计报告</title>
<style>
:root{{--ink:#1c2733;--sub:#5b6b7a;--line:#dde4ea;--bg:#f7f9fb;--card:#fff;--ok:#0f6b4f;--bad:#a33;--warn:#b3541e;--accent:#1a5fb4}}
body{{font-family:"Microsoft YaHei","PingFang SC",sans-serif;color:var(--ink);background:var(--bg);margin:0;padding:24px 16px;max-width:1100px;margin-inline:auto}}
h1{{font-size:20px;margin:0 0 4px}} h2{{font-size:15px;margin:20px 0 8px;padding-bottom:5px;border-bottom:2px solid var(--accent)}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:13px 16px;margin:8px 0}}
table{{border-collapse:collapse;width:100%;font-size:12px;margin:5px 0}}
th,td{{border:1px solid var(--line);padding:3px 6px;text-align:left}} th{{background:#f0f4f7}}
.num{{font-variant-numeric:tabular-nums}} .ok{{color:var(--ok);font-weight:600}} .bad{{color:var(--bad);font-weight:600}} .small{{font-size:11.5px;color:var(--sub)}}
</style></head><body>
<h1>v31 大比分族包回测 · realized ROI（体彩真实价）</h1>
<div class="meta">跑时 {r['ranAt']} · 宇宙 {r['data']['universe']} 场（胆级 {r['data']['dan']}）· 预注册 v31 · v30 先验 r={r['v30Prior']['r_blowout_cells']}</div>
<div class="card"><b>终局：</b><span class="{v_cls}">{r['verdict']}</span></div>
<h2>一、五 spec 汇总</h2>
<div class="card"><table><tr><th>spec</th><th>场次</th><th>投注</th><th>中奖</th><th>realized ROI</th><th>r 实测</th><th>族命中率</th><th>市场隐含</th><th>bootstrap95%CI</th></tr>{rows}</table></div>
<h2>二、对账行</h2>
<div class="card"><pre>{r['audit']['reconcile']}</pre><p class="small">票级复算 {r['audit']['recompute']} · 多重检验 {json.dumps(r['multipleTesting'], ensure_ascii=False)}</p></div>
<div class="small">开发者 sszhang · 2026-10-09 · tickets[] 逐场逐格可独立重算</div>
</body></html>"""


def audit_recompute(result, verbose=True):
    ms = load_matches()
    fails = []
    for sp, s in result["strategies"].items():
        if sp == "S3":
            sel = [m for m in ms if STD_P <= m["p"] < DAN_P]
        else:
            sel = [m for m in ms if m["p"] >= pool_for(sp)]
        stake = pay = 0.0
        for m in sel:
            _, s_, p_, _ = settle(m, sp)
            stake += s_
            pay += p_
        for name, got, want in (("stake", round(stake, 2), s["stake"]), ("payout", round(pay, 2), s["payout"]),
                                ("roi", round(pay / stake - 1, 4) if stake else None, s["roi"])):
            if got != want:
                fails.append(f"{sp}.{name}: 重算{got}≠落盘{want}")
    # 票级抽验：重放 10 张票逐格比对（键=spec+日期+对阵·防两年内重复对阵撞键）
    by_key = {(t["spec"], t["date"], t["match"]): t for t in result["tickets"]}
    checked = 0
    for m in ms[::max(1, len(ms) // 50)]:
        if checked >= 10:
            break
        for sp in SPEC_LIST:
            t = by_key.get((sp, m["date"], f"{m['home']} vs {m['away']}"))
            if not t:
                continue
            cells, s_, p_, _ = settle(m, sp)
            want = [(c["cell"], c["odds"], c["hit"]) for c in cells]
            got = [(c["cell"], c["odds"], c["hit"]) for c in t["cells"]]
            if want != got or abs(t["payout"] - round(p_, 2)) > 0.01:
                fails.append(f"{sp} {t['match']}: 票面重放不符")
            checked += 1
            break
    verdict = "PASS" if not fails else f"FAIL——{fails[:3]}"
    if verbose:
        print(f"[审计②全量重算+票级抽验{checked}张] {verdict}")
    return verdict


def audit():
    result = json.loads(OUT_JSON.read_text(encoding="utf-8"))
    print(f"[审计①对账行] {result['audit']['reconcile']}")
    print(f"[审计②全量重算] {audit_recompute(result)}")
    print("审计完成")


if __name__ == "__main__":
    if "--audit" in sys.argv:
        audit()
    else:
        run()
