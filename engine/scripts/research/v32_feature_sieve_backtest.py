# -*- coding: utf-8 -*-
r"""v32 · 特征筛子批次（标签=总进球≥5·2026-10-09）

预注册（跑前写死）: engine/cache/strength_chain/fade-strategy-prereg.json → v32FeatureSieve + v32.1 纯净闸
设计档: docs/2026-10-09-v32-feature-sieve-design.html
大哥拍板: 特征筛子替代数学筛子 · 大比分=总进球多(3:2/3:4入) · 训练验证零重叠(质询后补五道纯净闸)

标签 = 总进球≥5 · 主玩法 = TTG s5+s6+s7 三格包（分带 r 探针: 全池0.597/胆级0.653）
特征 8 维（as-of）: λ和/λ主/λ客(V3W重放) · TTG大球隐含 · 联赛trailing场均 · 两队近5场均 · had p · 客热门
模型 = logistic IRLS（fit≤2024-12 硬切 · val≥2025-01 复验 · 标准化只用 fit 统计）
筛法 = 滚动池{D,D+1} 模型概率 Top-K(K=1/2/3) → TTG 包 · 对照=市场TopK/随机×200/全池/CRS高总分族

判据: ①val段任一K档CI下限>0 ②fit/val同向差<5pp ③打赢全部对照 ④全报不挑
纯净闸（代码断言）: 日期硬切零重叠 / fit-only标准化 / 同日先算后更 / 对照同切 / V3W参数弱泄漏入caveat

用法: python engine/scripts/research/v32_feature_sieve_backtest.py [--audit]
开发者 sszhang
"""
import json
import random
import sys
from collections import defaultdict, deque
from datetime import date, timedelta
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "engine" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import strength_loaders as sl          # noqa: E402
import strength_chain_eval as sce      # noqa: E402
from v18_dual_signal_backtest import LEAGUES  # noqa: E402

HIST = ROOT / "engine" / "cache" / "hist_odds"
SUMM = ROOT / "data" / "04-summaries"
OUT_JSON = SUMM / "v32-feature-sieve-backtest.json"
OUT_HTML = SUMM / "2026-10-09-v32-feature-sieve-review.html"

# ---- 预注册常量 ----
LABEL_TOT = 5                    # 大比分 = 总进球 ≥5
FIT_END = "2024-12-31"           # 纯净闸①：fit/val 日期硬切
K_LIST = (1, 2, 3)
TTG_CELLS = ("5", "6", "7")
CRS_FAMILY = ("3:2", "2:3", "3:3", "4:1", "1:4")
N_BOOT, BOOT_SEED = 2000, 20261010
N_RND, RND_SEED = 200, 20261010
LEAGUE_MIN_N = 20                # 联赛trailing最少样本·不足回退全局
LAST_N = 5                       # 球队近N场
RIDGE = 1e-8


def load_hist():
    rows = []
    for f in sorted(HIST.glob("crs_hist_*.json")):
        rows += json.loads(f.read_text(encoding="utf-8"))["matches"]
    out = []
    for m in rows:
        had, sc = m.get("had"), str(m.get("score") or "")
        if not (had and had.get("h") and ":" in sc):
            continue
        try:
            h, a = map(int, sc.split(":"))
        except ValueError:
            continue
        oh, od, oa = float(had["h"]), float(had["d"]), float(had["a"])
        inv = [1 / oh, 1 / od, 1 / oa]
        s = sum(inv)
        out.append({"date": str(m.get("date", ""))[:10], "home": m.get("home", "?"),
                    "away": m.get("away", "?"), "league": m.get("league", "?"),
                    "h": h, "a": a, "tot": h + a, "ttg": m.get("ttg") or {}, "crs": m.get("crs") or {},
                    "p": max(i / s for i in inv), "fav": inv.index(max(inv))})
    return out


def rolling_features(matches):
    """纯净闸③：同日整组先算后更——当日比赛互不可见。"""
    by_day = defaultdict(list)
    for m in matches:
        by_day[m["date"]].append(m)
    lg_sum, lg_n = defaultdict(float), defaultdict(int)
    team_hist = defaultdict(lambda: deque(maxlen=LAST_N))
    g_sum, g_n = 0.0, 0
    for d in sorted(by_day):
        day = by_day[d]
        for m in day:
            g_avg = g_sum / g_n if g_n else 2.6
            m["f_lgavg"] = (lg_sum[m["league"]] / lg_n[m["league"]]
                            if lg_n[m["league"]] >= LEAGUE_MIN_N else g_avg)
            hs, as_ = team_hist[m["home"]], team_hist[m["away"]]
            m["f_t5"] = (sum(hs) / len(hs) + sum(as_) / len(as_)) / 2 if hs and as_ else m["f_lgavg"]
        for m in day:                                   # 当日赛果后更
            lg_sum[m["league"]] += m["tot"]
            lg_n[m["league"]] += 1
            team_hist[m["home"]].append(m["tot"])
            team_hist[m["away"]].append(m["tot"])
            g_sum += m["tot"]
            g_n += 1


def extract_lams(matches):
    """V3W λ 重放（memo 共享·as-of 内部纪律）→ {date|home|away: (λh, λa)}。"""
    z2i = sl.zh_to_id()
    ctx = sl.build_ctx(LEAGUES)
    memo = {}
    out = {}
    n = 0
    for m in matches:
        hid, aid = z2i.get(m["home"]), z2i.get(m["away"])
        if not (hid and aid):
            continue
        pred = sce._predict_match(hid, aid, date.fromisoformat(m["date"]), ctx, memo, beta=0.05)
        if pred and pred.get("lam"):
            out[f"{m['date']}|{m['home']}|{m['away']}"] = pred["lam"]
            n += 1
    return out, n


def ttg_implied(m):
    return sum(1 / float(m["ttg"][k]) for k in ("4", "5", "6", "7")
               if m["ttg"].get(k) and float(m["ttg"][k]) > 1)


def build_matrix(matches, lams):
    rows = []
    for m in matches:
        lam = lams.get(f"{m['date']}|{m['home']}|{m['away']}")
        if not lam or not m["ttg"]:
            continue
        rows.append({"m": m, "x": [lam[0] + lam[1], lam[0], lam[1], ttg_implied(m),
                                   m["f_lgavg"], m["f_t5"], m["p"], 1.0 if m["fav"] == 2 else 0.0],
                     "y": 1 if m["tot"] >= LABEL_TOT else 0})
    return rows


def irls(X, y, max_iter=60, tol=1e-9):
    """logistic 回归（确定性 IRLS + 微岭）。X 含截距列。"""
    beta = np.zeros(X.shape[1])
    for _ in range(max_iter):
        z = X @ beta
        p = 1 / (1 + np.exp(-np.clip(z, -30, 30)))
        w = np.clip(p * (1 - p), 1e-6, None)
        grad = X.T @ (y - p)
        hess = X.T @ (X * w[:, None]) + RIDGE * np.eye(X.shape[1])
        step = np.linalg.solve(hess, grad)
        beta += step
        if np.max(np.abs(step)) < tol:
            break
    return beta


def bet_ttg(m):
    stake = pay = 0.0
    hits = []
    cells = []
    for k in TTG_CELLS:
        o = m["ttg"].get(k)
        if not o or float(o) <= 1:
            continue
        o = float(o)
        hit = (m["tot"] == int(k))
        stake += 2
        pay += 2 * o if hit else 0
        cells.append({"cell": k, "odds": o, "hit": hit})
        if hit:
            hits.append(k)
    return cells, stake, pay, hits


def bet_crs_family(m):
    stake = pay = 0.0
    cells, hits = [], []
    sc = f"{m['h']}:{m['a']}"
    for k in CRS_FAMILY:
        o = m["crs"].get(k)
        if not o or float(o) <= 1:
            continue
        o = float(o)
        hit = (k == sc)
        stake += 2
        pay += 2 * o if hit else 0
        cells.append({"cell": k, "odds": o, "hit": hit})
        if hit:
            hits.append(k)
    return cells, stake, pay, hits


def seg_of(d):
    return "fit" if d <= FIT_END else "val"


def pools_of(rows):
    by_day = defaultdict(list)
    for r in rows:
        by_day[r["m"]["date"]].append(r)
    dates = sorted(by_day)
    pools = {}
    for d in dates:
        d2 = (date.fromisoformat(d) + timedelta(days=1)).isoformat()
        pool = by_day[d] + by_day.get(d2, [])
        if pool:
            pools[d] = pool
    return pools


def summarize(tickets, seg):
    """tickets=[{date, stake, pay, hit}] → 指标（bootstrap 日重抽样）。"""
    ts = [t for t in tickets if seg_of(t["date"]) == seg]
    if not ts:
        return None
    stake = sum(t["stake"] for t in ts)
    pay = sum(t["pay"] for t in ts)
    days = defaultdict(lambda: [0.0, 0.0])
    for t in ts:
        days[t["date"]][0] += t["stake"]
        days[t["date"]][1] += t["pay"]
    arr = list(days.values())
    rng = np.random.RandomState(BOOT_SEED)
    rois = []
    for _ in range(N_BOOT):
        idx = rng.randint(0, len(arr), len(arr))
        s = sum(arr[i][0] for i in idx)
        r = sum(arr[i][1] for i in idx)
        rois.append(r / s - 1 if s else 0)
    rois.sort()
    return {"n": len(ts), "days": len(days), "stake": round(stake, 2), "payout": round(pay, 2),
            "roi": round(pay / stake - 1, 4) if stake else None,
            "r": round(pay / stake, 4) if stake else None,
            "hitRate": round(sum(1 for t in ts if t["pay"] > 0) / len(ts), 4),
            "boot": {"p2.5": round(rois[int(0.025 * N_BOOT)], 4), "p50": round(rois[N_BOOT // 2], 4),
                     "p97.5": round(rois[int(0.975 * N_BOOT)], 4)}}


def run():
    print("装载 hist + 滚动特征（同日先算后更）...")
    matches = load_hist()
    rolling_features(matches)
    print(f"{len(matches)} 场 · 特征就绪")
    print("V3W λ 重放（~13分钟·memo 共享）...")
    lams, n_lam = extract_lams(matches)
    print(f"λ 覆盖 {n_lam}/{len(matches)}")
    rows = build_matrix(matches, lams)

    # 纯净闸①：日期硬切零重叠断言
    fit_dates = {r["m"]["date"] for r in rows if seg_of(r["m"]["date"]) == "fit"}
    val_dates = {r["m"]["date"] for r in rows if seg_of(r["m"]["date"]) == "val"}
    assert not (fit_dates & val_dates) and (max(fit_dates) < min(val_dates)), "纯净闸①失败：日期重叠"

    fit_rows = [r for r in rows if seg_of(r["m"]["date"]) == "fit"]
    val_rows = [r for r in rows if seg_of(r["m"]["date"]) == "val"]
    Xf = np.array([r["x"] for r in fit_rows])
    mu, sd = Xf.mean(axis=0), Xf.std(axis=0) + 1e-9          # 纯净闸②：fit-only 统计
    yf = np.array([r["y"] for r in fit_rows])
    Xb = np.column_stack([np.ones(len(fit_rows)), (Xf - mu) / sd])
    beta = irls(Xb, yf)
    print(f"logistic fit: {len(fit_rows)} 行 · 系数 {[round(float(b), 3) for b in beta]}")
    for r in rows:
        xs = (np.array(r["x"]) - mu) / sd
        r["prob"] = float(1 / (1 + np.exp(-np.clip(beta[0] + xs @ beta[1:], -30, 30))))

    pools = pools_of(rows)
    tickets = []
    specs = defaultdict(list)

    def emit(spec, day, r, cells, stake, pay, hits, prob):
        t = {"date": day, "spec": spec, "match": f"{r['m']['home']} vs {r['m']['away']}",
             "prob": round(prob, 4), "tot": r["m"]["tot"],
             "cells": cells, "stake": round(stake, 2), "pay": round(pay, 2),
             "hit": bool(hits), "hits": hits}
        tickets.append(t)
        specs[spec].append(t)

    print(f"滚动池 {len(pools)} 天 · 开跑筛法...")
    for day in sorted(pools):
        pool = pools[day]
        for K in K_LIST:
            by_model = sorted(pool, key=lambda r: -r["prob"])[:K]
            by_market = sorted(pool, key=lambda r: -r["m"]["p"])[:K]
            for tag, sel in (("MODEL", by_model), ("MARKET", by_market)):
                for r in sel:
                    cells, st, pay, hits = bet_ttg(r["m"])
                    if cells:
                        emit(f"{tag}-Top{K}", day, r, cells, st, pay, hits,
                             r["prob"] if tag == "MODEL" else r["m"]["p"])

    # 随机筛 ×200（val 段判读·fit 段同报）
    rng = random.Random(RND_SEED)
    rnd_specs = defaultdict(list)
    for seed in range(1, N_RND + 1):
        r2 = random.Random(seed)
        for K in K_LIST:
            st = pay = 0.0
            tk = []
            for day in sorted(pools):
                sel = r2.sample(pools[day], min(K, len(pools[day])))
                for r in sel:
                    cells, s_, p_, hits = bet_ttg(r["m"])
                    st += s_
                    pay += p_
                    if seed == 1:
                        tk.append({"date": day, "spec": f"RND-Top{K}", "match": f"{r['m']['home']} vs {r['m']['away']}",
                                   "prob": None, "tot": r["m"]["tot"], "cells": cells,
                                   "stake": round(s_, 2), "pay": round(p_, 2), "hit": bool(hits), "hits": hits})
            rnd_specs[f"RND-Top{K}"].append(pay / st - 1 if st else 0.0)
            if seed == 1:
                tickets += tk
                specs[f"RND-Top{K}"] += tk

    # 全池基线 + CRS 高总分族（模型Top1场）
    for r in rows:
        cells, st, pay, hits = bet_ttg(r["m"])
        if cells:
            emit("ALLPOOL", r["m"]["date"], r, cells, st, pay, hits, r["prob"])
    for day in sorted(pools):
        top1 = sorted(pools[day], key=lambda r: -r["prob"])[:1]
        for r in top1:
            cells, st, pay, hits = bet_crs_family(r["m"])
            if cells:
                emit("CRSfam-Top1", day, r, cells, st, pay, hits, r["prob"])

    # 汇总（fit/val 分段）+ 判据
    summaries = {}
    for spec in sorted(specs):
        for seg in ("fit", "val"):
            s = summarize(specs[spec], seg)
            if s:
                summaries[f"{spec}|{seg}"] = s
    crit = {}
    for K in K_LIST:
        key = f"MODEL-Top{K}|val"
        f_, v_ = summaries.get(f"MODEL-Top{K}|fit"), summaries.get(key)
        if not v_:
            continue
        rnd = sorted(rnd_specs[f"RND-Top{K}"])
        mk = summaries.get(f"MARKET-Top{K}|val")
        ap = summaries.get(f"ALLPOOL|val")
        crit[f"MODEL-Top{K}"] = {
            "c1_ciLowPos": v_["boot"]["p2.5"] > 0,
            "c2_sameDir": bool(f_ and v_ and f_["r"] > 1.0 and v_["r"] > 1.0 and abs(f_["r"] - v_["r"]) < 0.05),
            "c3_beatMarket": bool(mk and v_["r"] > mk["r"]),
            "c3_beatRandomP95": bool(v_["r"] > rnd[int(0.95 * N_RND)] + 1),
            "c3_beatAllpool": bool(ap and v_["r"] > ap["r"]),
            "rndP95": round(rnd[int(0.95 * N_RND)], 4),
            "rndP50": round(rnd[N_RND // 2], 4),
        }
    n_pass = sum(1 for c in crit.values()
                 if c["c1_ciLowPos"] and c["c3_beatMarket"] and c["c3_beatRandomP95"] and c["c3_beatAllpool"])
    verdict = (f"PASS(判据①+③全过 → 建影子spec·转正前须纯前向复验[V3W参数弱泄漏交底])" if n_pass
               else "SEALED(特征无增量·市场TTG定价已含攻防信息·或CI含0)——判据未全过")

    result = {"ranAt": "2026-10-09", "script": str(Path(__file__).relative_to(ROOT)),
              "preReg": "fade-strategy-prereg v32FeatureSieve+v32.1纯净闸（e580933..）",
              "data": {"matches": len(matches), "lamCoverage": n_lam, "rows": len(rows),
                       "fitRows": len(fit_rows), "valRows": len(val_rows)},
              "model": {"beta": [round(float(b), 6) for b in beta],
                        "mu": [round(float(x), 6) for x in mu], "sd": [round(float(x), 6) for x in sd],
                        "features": ["λ和", "λ主", "λ客", "TTG隐含", "联赛场均", "近5场均", "had_p", "客热门"]},
              "strategies": summaries, "criteria": crit,
              "verdict": verdict,
              "audit": {"reconcile": "", "recompute": ""},
              "tickets": tickets}
    n_tk = len(tickets)
    stake_all = sum(t["stake"] for t in tickets)
    pay_all = sum(t["pay"] for t in tickets)
    result["audit"]["reconcile"] = f"全部票{n_tk}张·投入{stake_all:.0f}·中奖{pay_all:.2f}"
    result["audit"]["recompute"] = audit_recompute(result, verbose=False)
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    OUT_HTML.write_text(gen_html(result), encoding="utf-8")

    print("\n" + "=" * 72)
    for k in sorted(summaries):
        if k.startswith(("MODEL", "MARKET", "ALLPOOL", "CRSfam")):
            s = summaries[k]
            print(f"  {k:20s} n={s['n']:5d} r={s['r']:.3f} ROI{s['roi']:+7.1%} "
                  f"CI[{s['boot']['p2.5']:+.1%},{s['boot']['p97.5']:+.1%}] 命中{s['hitRate']:.1%}")
    print(f"\n判据: {json.dumps(crit, ensure_ascii=False, indent=1)}")
    print(f"终局: {verdict}")
    print(f"产物: {OUT_JSON.name} + {OUT_HTML.name}")
    return result


def gen_html(r):
    rows = "".join(
        f"<tr><td>{k.split('|')[0]}</td><td>{k.split('|')[1]}</td><td class='num'>{s['n']}</td>"
        f"<td class='num'>{s['stake']:.0f}</td><td class='num'>{s['payout']:.2f}</td>"
        f"<td class='num {'ok' if s['roi'] > 0 else 'bad'}'>{s['r']}</td>"
        f"<td class='num'>{s['roi']:+.1%}</td><td class='num'>{s['hitRate']:.1%}</td>"
        f"<td class='num'>{s['boot']['p2.5']:+.1%}~{s['boot']['p97.5']:+.1%}</td></tr>"
        for k, s in sorted(r["strategies"].items()) if not k.startswith("RND"))
    v_cls = "ok" if r["verdict"].startswith("PASS") else "bad"
    feats = " · ".join(f"{n}={b:+.3f}" for n, b in zip(["截距"] + r["model"]["features"], r["model"]["beta"]))
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>v32 特征筛子回测 · 审计报告</title>
<style>
:root{{--ink:#1c2733;--sub:#5b6b7a;--line:#dde4ea;--bg:#f7f9fb;--card:#fff;--ok:#0f6b4f;--bad:#a33;--warn:#b3541e;--accent:#1a5fb4}}
body{{font-family:"Microsoft YaHei","PingFang SC",sans-serif;color:var(--ink);background:var(--bg);margin:0;padding:24px 16px;max-width:1100px;margin-inline:auto}}
h1{{font-size:20px;margin:0 0 4px}} h2{{font-size:15px;margin:20px 0 8px;padding-bottom:5px;border-bottom:2px solid var(--accent)}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:13px 16px;margin:8px 0}}
table{{border-collapse:collapse;width:100%;font-size:12px;margin:5px 0}}
th,td{{border:1px solid var(--line);padding:3px 6px;text-align:left}} th{{background:#f0f4f7}}
.num{{font-variant-numeric:tabular-nums}} .ok{{color:var(--ok);font-weight:600}} .bad{{color:var(--bad);font-weight:600}} .small{{font-size:11.5px;color:var(--sub)}}
</style></head><body>
<h1>v32 特征筛子回测 · 标签=总进球≥5</h1>
<div class="meta">跑时 {r['ranAt']} · λ覆盖 {r['data']['lamCoverage']}/{r['data']['matches']} · fit {r['data']['fitRows']} / val {r['data']['valRows']} 行 · 预注册 v32+v32.1</div>
<div class="card"><b>终局：</b><span class="{v_cls}">{r['verdict']}</span></div>
<h2>一、模型系数（冻结）</h2>
<div class="card"><code>{feats}</code></div>
<h2>二、spec × 段 汇总（realized·TTG s5+包）</h2>
<div class="card"><table><tr><th>spec</th><th>段</th><th>n</th><th>投入</th><th>中奖</th><th>r</th><th>ROI</th><th>命中率</th><th>bootstrap95%CI</th></tr>{rows}</table></div>
<h2>三、判据明细</h2>
<div class="card"><pre>{json.dumps(r['criteria'], ensure_ascii=False, indent=1)}</pre></div>
<h2>四、对账行</h2>
<div class="card"><pre>{r['audit']['reconcile']}</pre><p class="small">复算 {r['audit']['recompute']}</p></div>
<div class="small">开发者 sszhang · 2026-10-09 · tickets[] 票面可独立重算 · 纯净闸五道见预注册 v32.1</div>
</body></html>"""


def audit_recompute(result, verbose=True):
    """确定性全链路重放：特征+IRLS+筛法重算·与落盘比对。"""
    matches = load_hist()
    rolling_features(matches)
    lams, _ = extract_lams(matches)
    rows = build_matrix(matches, lams)
    fit_rows = [r for r in rows if seg_of(r["m"]["date"]) == "fit"]
    Xf = np.array([r["x"] for r in fit_rows])
    yf = np.array([r["y"] for r in fit_rows])
    beta_saved = np.array(result["model"]["beta"])
    mu = np.array(result["model"]["mu"])
    sd = np.array(result["model"]["sd"])
    Xb = np.column_stack([np.ones(len(fit_rows)), (Xf - mu) / sd])
    beta = irls(Xb, yf)
    fails = []
    if np.max(np.abs(beta - beta_saved)) > 1e-4:
        fails.append(f"模型系数重放不符 Δ{np.max(np.abs(beta - beta_saved)):.6f}")
    for r in rows:
        xs = (np.array(r["x"]) - mu) / sd
        r["prob"] = float(1 / (1 + np.exp(-np.clip(beta[0] + xs @ beta[1:], -30, 30))))
    pools = pools_of(rows)
    for K in K_LIST:
        for day in sorted(pools):
            sel = sorted(pools[day], key=lambda r: -r["prob"])[:K]
            stored = {(t["date"], t["match"]) for t in result["tickets"]
                      if t["spec"] == f"MODEL-Top{K}"}
            want = {(day, f"{r['m']['home']} vs {r['m']['away']}") for r in sel}
            miss = want - stored
            if miss:
                fails.append(f"MODEL-Top{K} {day}: 重放选场未落盘 {list(miss)[:2]}")
                break
    for key, s in result["strategies"].items():
        if key.startswith("RND"):
            continue
        spec, seg = key.split("|")
        ts = [t for t in result["tickets"] if t["spec"] == spec and seg_of(t["date"]) == seg]
        stake = sum(t["stake"] for t in ts)
        pay = sum(t["pay"] for t in ts)
        if abs(stake - s["stake"]) > 0.02 or abs(pay - s["payout"]) > 0.02:
            fails.append(f"{key}: 票面{stake:.2f}/{pay:.2f}≠落盘{s['stake']}/{s['payout']}")
    verdict = "PASS" if not fails else f"FAIL——{fails[:3]}"
    if verbose:
        print(f"[审计②全链路重放(特征+IRLS+筛法)] {verdict}")
    return verdict


def audit():
    result = json.loads(OUT_JSON.read_text(encoding="utf-8"))
    print(f"[审计①对账行] {result['audit']['reconcile']}")
    print(f"[审计②确定性重放] {audit_recompute(result)}")
    print("审计完成")


if __name__ == "__main__":
    if "--audit" in sys.argv:
        audit()
    else:
        run()
