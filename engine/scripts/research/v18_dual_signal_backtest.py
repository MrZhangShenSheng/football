# -*- coding: utf-8 -*-
r"""v18 · 双信号选腿 N 串方案回测（2026-10-07）

预注册（跑前写死）: engine/cache/strength_chain/fade-strategy-prereg.json → v18DualSignal
设计档: docs/2026-10-07-dual-signal-design.html
大哥指令: 结合选腿逻辑 + 体彩 HAD 购法 3≤N≤8 · 比分模型验证选好腿 · <3 腿关档 · 算中奖概率/金额/投入 ROI

第一信号 = 市场置信分带（v16 S3 同规：踩线护栏 + p≥0.60 + 胆级优先）
第二信号 = V3W-v2 比分模型 39 格矩阵聚合三向 argmax 与第一信号同向
成交 = fd Pinnacle 真收盘价 × 2 元；体彩敏感性平移（v16.1 三带比）
判据（预注册）: DUAL−S3J≥+5pp 且 CI 下限>0 且扣最大单日>0 → 过线；带内/不足 = 无增量；劣 = 同判盖棺

审计三件套: ①逐日票面 stdout ②tickets[] 票级落盘 ③--audit 复算+抽天重放（含 V3W 重预测）

用法:
  python engine/scripts/research/v18_dual_signal_backtest.py          # 跑回测
  python engine/scripts/research/v18_dual_signal_backtest.py --audit  # 复算对账

开发者 sszhang
"""
import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "engine" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import strength_loaders as sl          # noqa: E402
import strength_chain_eval as sce      # noqa: E402
from v16_had_parlay_backtest import (  # noqa: E402  复用 v16 引擎（同源防两处实现漂移）
    STAKE, load_day_pools, sel_s3, mk_ticket, print_ticket, summarize,
    run_rnd, rnd_dist_summary, rnd_percentile, sporttery_sensitivity, PICK_LABEL,
)

SUMM = ROOT / "data" / "04-summaries"
HIST = ROOT / "engine" / "cache" / "hist_odds"
OUT_JSON = SUMM / "v18-dual-signal-backtest.json"
OUT_HTML = SUMM / "2026-10-07-v18-dual-signal-review.html"

# ---- 预注册常量 ----
WINDOW = ("2024-01-01", "2026-09-28")
LEAGUES = ["england-premier", "spain-laliga", "germany-bundesliga", "italy-serie-a",
           "france-ligue1", "netherlands-eredivisie", "brazil", "portugal-liga",
           "england-championship", "spain-liga2", "germany-bundesliga2", "italy-serie-b",
           "france-ligue2", "belgium-first-a", "turkey-super-lig", "greece-super", "SC0"]
N_RND, N_BOOT = 200, 2000
BOOT_SEED = 20261008                 # 预注册种子
RND_SAMPLE_SEED = 1
DUAL_MARGIN_PP = 5.0                 # 判据①过线门槛（DUAL − S3J）
TIER_DAN_P = 0.75
DAN_CAL, STD_CAL = 0.830, 0.694      # ROI 四件套分带校准命中率（v16.1 收盘基大样本）
BETA = 0.05                          # V3W 生产 β（v11 链同参）


def _cell_ha(k: str):
    """矩阵键 sXXsYY → (主进球, 客进球)；胜其他/平其他/负其他 → 6:0 / 3:3 / 0:6。"""
    if k == "s1sh": return 6, 0
    if k == "s1sa": return 0, 6
    if k == "s1sd": return 3, 3
    try: return int(k[1:3]), int(k[4:6])
    except (ValueError, IndexError): return None


def v3w_direction(hid, aid, as_of, ctx, memo):
    """V3W 39 格矩阵 → 三向概率。→ (pH,pD,pA) 或 None。"""
    pred = sce._predict_match(hid, aid, as_of, ctx, memo, beta=BETA)
    if not pred or not pred.get("matrix"):
        return None
    ph = pd_ = pa = 0.0
    for k, v in pred["matrix"].items():
        ha = _cell_ha(k)
        if ha is None: continue
        h, a = ha
        if h > a: ph += v
        elif h == a: pd_ += v
        else: pa += v
    s = ph + pd_ + pa
    return (ph / s, pd_ / s, pa / s) if s > 0 else None


def build_joined_pools():
    """fd 收盘底表 ∩ hist_odds（V3W 时间线）→ {date: [legs 带 v3 字段]}。"""
    pools, n_fd = load_day_pools()
    fd_idx = {(d, l["home"], l["away"]): l for d, ls in pools.items() for l in ls}
    z2i = sl.zh_to_id()
    ctx = sl.build_ctx(LEAGUES)
    memo = {}
    joined = defaultdict(list)
    n_hist = n_join = n_pred = 0
    offsets = defaultdict(int)
    for f in sorted(HIST.glob("crs_hist_*.json")):
        for m in json.loads(f.read_text(encoding="utf-8"))["matches"]:
            d = str(m.get("date", ""))[:10]
            if not (WINDOW[0] <= d <= WINDOW[1]):
                continue
            n_hist += 1
            hid, aid = z2i.get(m.get("home")), z2i.get(m.get("away"))
            if not (hid and aid):
                continue
            leg = None
            for off in (0, 1, -1):
                dd = (date.fromisoformat(d) + timedelta(days=off)).isoformat()
                leg = fd_idx.get((dd, hid, aid))
                if leg:
                    offsets[off] += 1
                    break
            if not leg:
                continue
            n_join += 1
            t = v3w_direction(hid, aid, date.fromisoformat(d), ctx, memo)
            if t is None:
                continue
            n_pred += 1
            v3 = dict(zip(("H", "D", "A"), t))
            v3dir = max(v3, key=v3.get)
            leg2 = dict(leg)
            leg2["v3dir"] = v3dir
            leg2["v3p"] = round(v3[leg["pick"]], 4)
            leg2["v3agree"] = v3dir == leg["pick"]
            joined[leg["date"]].append(leg2)
    meta = {"fdMatches": n_fd, "histInWindow": n_hist, "joined": n_join, "v3Predicted": n_pred,
            "dateOffsets": dict(offsets), "days": len(joined)}
    return dict(joined), meta


def select_dual(pool):
    """双确认：合格池（v16 S3 护栏+p≥0.60）∩ V3W 同向 → 胆优先排序 → N=min(8)。→ (legs|None, 合格数, agree数)"""
    legs3, q3 = sel_s3(pool)
    qual = legs3 if legs3 is not None else []
    # sel_s3 已 cap 8——重算完整合格池避免 cap 前丢失 agree 腿
    from v16_had_parlay_backtest import TIER_STD_P, TREADLINE_ODDS, TREADLINE_P, TIER_DAN_P, MAX_LEGS
    full = [l for l in pool if l["pickP"] >= TIER_STD_P
            and not (l["pickOdds"] < TREADLINE_ODDS and l["pickP"] < TREADLINE_P)]
    agree = [l for l in full if l.get("v3agree")]
    if len(agree) < 3:
        return None, len(full), len(agree)
    dans = sorted([l for l in agree if l["pickP"] >= TIER_DAN_P],
                  key=lambda l: (-l["pickP"], l["pickOdds"], l["home"]))
    stds = sorted([l for l in agree if l["pickP"] < TIER_DAN_P],
                  key=lambda l: (-l["pickP"], l["pickOdds"], l["home"]))
    return (dans + stds)[:MAX_LEGS], len(full), len(agree)


def roi_fourpiece(legs):
    """ROI 四件套（claimed 口径）：分带校准命中率连乘 × Π赔率。"""
    p_all = 1.0
    prod = 1.0
    for l in legs:
        p_all *= DAN_CAL if l["pickP"] >= TIER_DAN_P else STD_CAL
        prod *= l["pickOdds"]
    return {"pAll": round(p_all, 4), "payout": round(STAKE * prod, 2),
            "stake": STAKE, "roiClaimed": round(p_all * prod - 1, 4)}


def run():
    print("装载 fd 底表 + V3W 重放（memo 共享一次 preload）...")
    pools, meta = build_joined_pools()
    print(f"交集底表: fd {meta['fdMatches']} · hist窗口 {meta['histInWindow']} · join {meta['joined']} · "
          f"V3W可预测 {meta['v3Predicted']} · {meta['days']} 日 · 日期偏移 {meta['dateOffsets']}")
    dates = sorted(pools)

    tickets = []
    s3_pairs, dual_pairs = [], []
    cum = defaultdict(lambda: [0.0, 0.0])

    def emit(t):
        tickets.append(t)
        c = cum[t["strat"]]
        c[0] += t["stake"]; c[1] += t["ret"]
        print_ticket(t, c[0], c[1])

    print("=" * 72)
    for d in dates:
        pool = pools[d]
        print(f"\n◆ {d} · 交集日池 {len(pool)} 场")
        legs3, q3 = sel_s3(pool)
        if legs3:
            s3_pairs.append((d, len(legs3)))
            emit(mk_ticket(d, "S3J", legs3, f"{len(legs3)}串1"))
        else:
            print(f"  [S3J] 关档（合格腿 {q3}<3）")
        dl, qf, qa = select_dual(pool)
        if dl:
            dual_pairs.append((d, len(dl)))
            t = mk_ticket(d, "DUAL", dl, f"{len(dl)}串1")
            t["roi4"] = roi_fourpiece(dl)
            emit(t)
        else:
            print(f"  [DUAL] 关档（双确认腿 {qa}/{qf} <3）")

    print("\nRND×200 基线（S3J/DUAL 逐日配对）...")
    rnd_dist, rnd_sample = run_rnd(pools, {"S3": s3_pairs, "S4": dual_pairs})
    for t in rnd_sample:
        tickets.append(t)

    groups = defaultdict(list)
    for t in tickets:
        if not t["strat"].startswith("RND"):
            groups[t["strat"]].append(t)
    summaries = {}
    for key in sorted(groups):
        s = summarize(groups[key])
        if s: summaries[key] = s

    # 判据（预注册·机械应用）
    s3j, dual = summaries.get("S3J"), summaries.get("DUAL")
    crit = {}
    if s3j and dual:
        r_s3 = rnd_dist_summary(rnd_dist["pairedS3"]); r_du = rnd_dist_summary(rnd_dist["pairedS4"])
        diff_pp = (dual["roi"] - s3j["roi"]) * 100
        crit = {
            "diffVsS3J_pp": round(diff_pp, 1),
            "c1_margin": diff_pp >= DUAL_MARGIN_PP,
            "c1_ciLowPos": dual["boot"]["p2.5"] > 0,
            "c1_exMaxPos": dual["roiExMaxDay"] is not None and dual["roiExMaxDay"] > 0,
            "c2_inRnd95": r_du["p2.5"] <= dual["roi"] <= r_du["p97.5"],
            "dualRndPercentile": rnd_percentile(rnd_dist["pairedS4"], dual["roi"]),
            "s3jRndPercentile": rnd_percentile(rnd_dist["pairedS3"], s3j["roi"]),
        }
        if crit["c1_margin"] and crit["c1_ciLowPos"] and crit["c1_exMaxPos"]:
            verdict = "PASS(判据①过线→批次二建每日生成器·体彩价+影子登记)"
        elif dual["roi"] < s3j["roi"]:
            verdict = "SEALED(判据③·V3W确认无价值·与v16/v17同判)"
        else:
            verdict = "NO_EDGE(判据②·双确认无增量·结论入档不接生产)"
    else:
        verdict = "INSUFFICIENT(交集样本不足)"

    sens = sporttery_sensitivity(pools)
    result = {
        "ranAt": "2026-10-07", "script": str(Path(__file__).relative_to(ROOT)),
        "preReg": "fade-strategy-prereg v18DualSignal（跑前写死·59080c6）",
        "data": dict(meta, settle="Pinnacle真收盘×2元·交集口径"),
        "strategies": summaries, "rnd": {k: rnd_dist_summary(v) for k, v in rnd_dist.items()},
        "criteria": crit, "verdict": verdict,
        "roi4Calibration": {"danRate": DAN_CAL, "stdRate": STD_CAL,
                            "note": "claimed口径=v16.1收盘基分带大样本命中率·realized=票面结算·分开报告"},
        "sensitivity": sens,
        "audit": {"reconcile": "", "recompute": ""},
        "tickets": tickets,
    }
    n_paid = sum(len(v) for k, v in groups.items() if k in ("S3J", "DUAL"))
    tot_stake = sum(t["stake"] for v in groups.values() for t in v)
    tot_ret = sum(t["ret"] for v in groups.values() for t in v)
    result["audit"]["reconcile"] = (f"S3J+DUAL {n_paid}票×2元=投入{tot_stake:.0f}元 · 票面回款和={tot_ret:.2f}元")
    result["audit"]["recompute"] = audit_recompute(result, verbose=False)

    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    OUT_HTML.write_text(gen_html(result), encoding="utf-8")
    print("\n" + "=" * 72)
    print(f"终局: {verdict}")
    if crit: print(f"判据: {json.dumps(crit, ensure_ascii=False)}")
    for k, s in summaries.items():
        print(f"  {k:6s} ROI {s['roi']:+7.1%} CI[{s['boot']['p2.5']:+.1%},{s['boot']['p97.5']:+.1%}] · 扣最大单日 {s['roiExMaxDay']:+.1%} · {s['nTickets']}票")
    print(f"产物: {OUT_JSON.name} + {OUT_HTML.name}")
    return result


def gen_html(r):
    rows = "".join(
        f"<tr><td>{k}</td><td class='num'>{s['nTickets']}</td><td class='num'>{s['stake']:.0f}</td>"
        f"<td class='num'>{s['ret']:.2f}</td>"
        f"<td class='num {'ok' if s['roi'] > 0 else 'bad'}'>{s['roi']:+.1%}</td>"
        f"<td class='num'>{s['hitRate']:.1%}</td><td class='num'>{s['roiExMaxDay']:+.1%}</td>"
        f"<td class='num'>{s['boot']['p2.5']:+.1%}~{s['boot']['p97.5']:+.1%}</td>"
        f"<td class='num'>{json.dumps(s['bySeason'], ensure_ascii=False)}</td></tr>"
        for k, s in r["strategies"].items())
    dual_tk = [t for t in r["tickets"] if t["strat"] == "DUAL" and t.get("roi4")][:8]
    roi4_rows = "".join(
        f"<tr><td>{t['date']}</td><td>{t['depth']}</td>"
        f"<td class='num'>{t['roi4']['pAll']:.1%}</td><td class='num'>{t['roi4']['payout']}</td>"
        f"<td class='num'>{t['roi4']['roiClaimed']:+.1%}</td>"
        f"<td class='num'>{'全中' if t['hit'] else '未中'}</td></tr>"
        for t in dual_tk)
    v = r["verdict"]
    v_cls = "ok" if v.startswith("PASS") else ("bad" if v.startswith("SEALED") else "warn")
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>v18 双信号选腿 N 串回测 · 审计报告</title>
<style>
:root{{--ink:#1c2733;--sub:#5b6b7a;--line:#dde4ea;--bg:#f7f9fb;--card:#fff;--ok:#0f6b4f;--bad:#a33;--warn:#b3541e;--accent:#1a5fb4}}
*{{box-sizing:border-box}}
body{{font-family:"Microsoft YaHei","PingFang SC",sans-serif;color:var(--ink);background:var(--bg);margin:0;padding:24px 16px;max-width:1100px;margin-inline:auto}}
h1{{font-size:20px;margin:0 0 4px}} h2{{font-size:15px;margin:20px 0 8px;padding-bottom:5px;border-bottom:2px solid var(--accent)}}
.meta{{color:var(--sub);font-size:12.5px;margin-bottom:12px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:13px 16px;margin:8px 0}}
table{{border-collapse:collapse;width:100%;font-size:12px;margin:5px 0}}
th,td{{border:1px solid var(--line);padding:3px 6px;text-align:left}} th{{background:#f0f4f7}}
.num{{font-variant-numeric:tabular-nums}} .ok{{color:var(--ok);font-weight:600}} .bad{{color:var(--bad);font-weight:600}} .warn{{color:var(--warn);font-weight:600}}
.small{{font-size:11.5px;color:var(--sub)}} pre{{background:#f0f4f7;border-radius:8px;padding:10px;font-size:11.5px}}
</style></head><body>
<h1>v18 双信号选腿 N 串回测 · 审计报告</h1>
<div class="meta">跑时 {r['ranAt']} · 交集 {r['data']['v3Predicted']} 场 {r['data']['days']} 日 · fd∩hist_odds（V3W 时间线）· 预注册 v18</div>
<div class="card"><b>终局：</b><span class="{v_cls}">{v}</span></div>
<h2>一、策略汇总</h2>
<div class="card"><table><tr><th>策略</th><th>票数</th><th>投入</th><th>回款</th><th>ROI</th><th>全中率</th><th>扣最大单日</th><th>bootstrap95%CI</th><th>分赛季</th></tr>{rows}</table></div>
<h2>二、ROI 四件套样例（DUAL·claimed 口径）</h2>
<div class="card"><table><tr><th>日期</th><th>串深</th><th>中奖概率</th><th>中奖金额</th><th>ROI(claimed)</th><th>实际</th></tr>{roi4_rows}</table>
<p class="small">{r['roi4Calibration']['note']}</p></div>
<h2>三、判据明细与体彩敏感性</h2>
<div class="card"><pre>{json.dumps(r['criteria'], ensure_ascii=False, indent=1)}</pre>
<p class="small">体彩/Pinnacle 三带比 {json.dumps({k: v['ratioMean'] for k, v in r['sensitivity']['bands'].items()}, ensure_ascii=False)}（{r['sensitivity']['nMatches']} 对）</p></div>
<h2>四、对账行</h2>
<div class="card"><pre>{r['audit']['reconcile']}</pre><p class="small">票级复算 {r['audit']['recompute']}</p></div>
<div class="small">开发者 sszhang · 2026-10-07 · tickets[] 票面可独立重算一切 · 全量明细见 JSON</div>
</body></html>"""


def audit_recompute(result, verbose=True):
    """从 tickets[] 重算两策略汇总 + DUAL 双确认规则重放抽检。"""
    groups = defaultdict(list)
    for t in result["tickets"]:
        if t["strat"] in ("S3J", "DUAL"):
            groups[t["strat"]].append(t)
    fails = []
    for key, ts in groups.items():
        s = result["strategies"].get(key)
        if not s: fails.append(f"{key} 汇总缺失"); continue
        stake, ret = sum(t["stake"] for t in ts), sum(t["ret"] for t in ts)
        for name, got, want in (("nTickets", len(ts), s["nTickets"]), ("stake", round(stake, 2), s["stake"]),
                                ("ret", round(ret, 2), s["ret"]), ("roi", round(ret / stake - 1, 4), s["roi"])):
            if got != want: fails.append(f"{key}.{name}: {got}≠{want}")
    verdict = "PASS" if not fails else f"FAIL——{fails[:3]}"
    if verbose: print(f"[审计②票级复算] {verdict}")
    return verdict


def audit():
    result = json.loads(OUT_JSON.read_text(encoding="utf-8"))
    groups = defaultdict(list)
    for t in result["tickets"]:
        if t["strat"] in ("S3J", "DUAL"): groups[t["strat"]].append(t)
    n = sum(len(v) for v in groups.values())
    stake = sum(t["stake"] for v in groups.values() for t in v)
    ret = sum(t["ret"] for v in groups.values() for t in v)
    print(f"[审计①对账行] {n}票×2元={stake:.0f}元 · 回款和{ret:.2f}元 · ROI {ret/stake-1:+.2%}")
    print(f"[审计②票级复算] {audit_recompute(result)}")
    # 抽 3 天全链路重放（含 V3W 重预测）
    dates = sorted(groups.get("DUAL", []) and {t["date"] for t in groups["DUAL"]} or {t["date"] for t in groups["S3J"]})
    step = max(1, len(dates) // 3)
    pools, _ = build_joined_pools()
    fails = []
    for d in dates[::step][:3]:
        dl, _, _ = select_dual(pools.get(d, []))
        want = sorted((l["home"], l["pick"]) for l in (dl or []))
        got = sorted((l["home"], l["pick"]) for t in groups["DUAL"] if t["date"] == d for l in t["legs"])
        if want != got: fails.append(f"{d} DUAL 重放不符")
        print(f"  {d}: DUAL 重放 {len(got)}腿核对{'✓' if want == got else '✗'}")
    v3 = "PASS" if not fails else f"FAIL——{fails}"
    print(f"[审计③全链路重放·3天·含V3W重预测] {v3}")
    print("审计完成")


if __name__ == "__main__":
    if "--audit" in sys.argv:
        audit()
    else:
        run()
