# -*- coding: utf-8 -*-
r"""v19 · 五挡+滚动在售池回测（2026-10-07）

预注册（跑前写死）: engine/cache/strength_chain/fade-strategy-prereg.json → v19TierRolling
大哥 ROI 口径: realized = 实际投注金额 vs 实际中奖金额（体彩真实价结算·非概率预估）

宇宙 = hist_odds 全量 13574 场（体彩在售 2023-09~2025-10·had 真实赔率+赛果）
滚动池 = 逐日 D: 池 = 比赛日 {D, D+1} 全部在售场（镜像每日执行器·同场可入相邻两日两票）
六 spec = MAIN(一至四挡补位) / TAIL(二三五右尾) / Va(二+三a) / Vb(二+三b贴线) / Vc(二+四) / TOP2
结算 = 全中 2×∏体彩 had 赔率 · 断任一腿 0 · realized ROI = Σpayout/Σstake − 1

审计三件套: 逐票明细 stdout · tickets[] 落盘 · --audit 复算重放

用法: python engine/scripts/research/v19_tier_rolling_backtest.py [--smoke]
开发者 sszhang
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "engine" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import strength_loaders as sl          # noqa: E402
from v18_dual_signal_backtest import v3w_direction, LEAGUES  # noqa: E402
import v16_had_parlay_backtest as v16  # noqa: E402

v16.BOOT_SEED = 20261009               # v19 预注册种子（覆盖 v16 常量·summarize 读模块级）

SUMM = ROOT / "data" / "04-summaries"
HIST = ROOT / "engine" / "cache" / "hist_odds"
OUT_JSON = SUMM / "v19-tier-rolling-backtest.json"
OUT_HTML = SUMM / "2026-10-07-v19-tier-rolling-review.html"

SIDES = ("H", "D", "A")
LABEL = {"H": "主胜", "D": "平", "A": "客胜"}
TIER_ORDER = {"一挡": 0, "二挡": 1, "三挡a": 2, "三挡b": 2, "四挡": 3}
ANCHORS = {"一挡": 0.830, "二挡": 0.694, "三挡a": 0.597, "三挡b": 0.597, "四挡": 0.694, "五挡": 0.433}
SPEC_MAIN = ("MAIN", "一至四挡补位")
SPECS = {
    "MAIN": ["一挡", "二挡", "三挡a", "三挡b", "四挡"],
    "TAIL": ["二挡", "三挡a", "三挡b", "五挡"],
    "Va": ["二挡", "三挡a"],
    "Vb": ["二挡", "三挡b"],
    "Vc": ["二挡", "四挡"],
    "TOP2": ["一挡", "二挡", "三挡a", "三挡b", "四挡"],   # 取前二
}


def season_of(d: str) -> str:
    y, m = int(d[:4]), int(d[5:7])
    return f"{(y - 1) % 100:02d}{y % 100:02d}" if m >= 8 else f"{(y - 2) % 100:02d}{(y - 1) % 100:02d}"


def load_hist():
    rows = []
    for f in sorted(HIST.glob("crs_hist_*.json")):
        rows += json.loads(f.read_text(encoding="utf-8"))["matches"]
    return rows


def build_legs(rows, smoke=False):
    """hist 场 → 腿 dict（含挡位）。"""
    z2i = sl.zh_to_id()
    ctx = sl.build_ctx(LEAGUES)
    memo = {}
    legs_by_date = defaultdict(list)
    tier_legs = defaultdict(list)          # 挡级 realized 统计（唯一场·不按池重复计）
    n_pred = 0
    for m in rows:
        had, score = m.get("had"), m.get("score")
        d = str(m.get("date", ""))[:10]
        if not (had and score and had.get("h") and had.get("d") and had.get("a") and ":" in str(score)):
            continue
        if smoke and not ("2025-03-01" <= d <= "2025-03-31"):
            continue
        try:
            h, a = str(score).split(":")
            res = "H" if h > a else ("D" if h == a else "A")
        except ValueError:
            continue
        oh, od, oa = float(had["h"]), float(had["d"]), float(had["a"])
        inv = [1 / oh, 1 / od, 1 / oa]
        s = sum(inv)
        probs = [i / s for i in inv]
        pk = max(range(3), key=lambda i: probs[i])
        pick, p, o = SIDES[pk], probs[pk], (oh, od, oa)[pk]
        guard_fail = o < 1.35 and p < 0.68
        guard_edge = guard_fail and (o >= 1.33 or p >= 0.66)
        hid, aid = z2i.get(m.get("home")), z2i.get(m.get("away"))
        v3 = None
        if hid and aid:
            from datetime import date as _date
            t = v3w_direction(hid, aid, _date.fromisoformat(d), ctx, memo)
            if t:
                v3 = SIDES[t.index(max(t))]
                n_pred += 1
        agree = (v3 == pick) if v3 else None
        tier = None
        if agree:
            if p >= 0.75:
                tier = "一挡"
            elif p >= 0.60 and not guard_fail:
                tier = "二挡"
            elif 0.55 <= p < 0.60:
                tier = "三挡a"
            elif guard_edge:
                tier = "三挡b"
            elif p < 0.55:
                tier = "五挡"
        elif agree is None and p >= 0.60 and not guard_fail:
            tier = "四挡"
        leg = {"date": d, "season": season_of(d), "home": m.get("home", "?"), "away": m.get("away", "?"),
               "league": m.get("league", "?"), "pick": pick, "pickP": round(p, 4), "pickOdds": o,
               "score": str(score), "res": res, "hit": res == pick, "tier": tier, "v3agree": agree}
        legs_by_date[d].append(leg)
        if tier:
            tier_legs[tier].append(leg)
    meta = {"matches": sum(len(v) for v in legs_by_date.values()), "days": len(legs_by_date),
            "v3Predicted": n_pred, "smoke": smoke}
    return dict(legs_by_date), dict(tier_legs), meta


def rolling_pools(legs_by_date):
    """逐日 D → 池 = {D, D+1} 全部在售场（镜像每日执行器）。"""
    dates = sorted(legs_by_date)
    pools = {}
    from datetime import date as _date, timedelta as _td
    for d in dates:
        d2 = (_date.fromisoformat(d) + _td(days=1)).isoformat()
        pool = legs_by_date[d] + legs_by_date.get(d2, [])
        if pool:
            pools[d] = pool
    return pools


def select(legs, tiers, top=None):
    sel = [l for l in legs if l["tier"] in tiers]
    sel.sort(key=lambda l: (TIER_ORDER.get(l["tier"], 9), -l["pickP"], l["pickOdds"]))
    if top:
        sel = sel[:top]
    return sel


def run(smoke=False):
    print("装载 hist 宇宙 + V3W 重放（memo 共享）...")
    legs_by_date, tier_legs, meta = build_legs(load_hist(), smoke=smoke)
    pools = rolling_pools(legs_by_date)
    print(f"宇宙: {meta['matches']} 场 · {meta['days']} 日 · V3W 可测 {meta['v3Predicted']} · "
          f"滚动池 {len(pools)} 天")
    tier_stat = {t: {"n": len(v), "hitRate": round(sum(1 for l in v if l['hit']) / len(v), 4),
                     "anchor": ANCHORS[t], "deltaPp": round((sum(1 for l in v if l['hit']) / len(v) - ANCHORS[t]) * 100, 1)}
                 for t, v in sorted(tier_legs.items()) if v}
    for t, s in tier_stat.items():
        print(f"  [{t}] n={s['n']} realized命中{s['hitRate']:.1%} vs 锚{s['anchor']:.1%} (Δ{s['deltaPp']:+.1f}pp)")

    tickets = []
    pairs_main, pairs_tail = [], []
    print("=" * 72)
    for d in sorted(pools):
        pool = pools[d]
        print(f"\n◆ {d} · 滚动池 {len(pool)} 场")
        for spec, tiers in SPECS.items():
            top = 2 if spec == "TOP2" else 8
            min_n = 2 if spec == "TOP2" else 3
            sel = select(pool, tiers, top=top)
            if len(sel) < min_n:
                print(f"  [{spec}] 关档（{len(sel)}腿<{min_n}）")
                continue
            t = v16.mk_ticket(d, spec, sel, f"{len(sel)}串1")
            tickets.append(t)
            if spec == "MAIN":
                pairs_main.append((d, len(sel)))
            if spec == "TAIL":
                pairs_tail.append((d, len(sel)))
            legs_str = "; ".join(f"{l['home']}{LABEL[l['pick']]}@{l['pickOdds']:.2f}" for l in sel[:4])
            outcome = f"全中+{t['ret']:.2f}" if t["hit"] else "未中"
            print(f"  [{spec}] {len(sel)}串1 · 赔率积{t['oddsProd']:.2f} · {legs_str}"
                  f"{'...' if len(sel) > 4 else ''} · {outcome}")

    groups = defaultdict(list)
    for t in tickets:
        groups[t["strat"]].append(t)
    summaries = {}
    for k in sorted(groups):
        s = v16.summarize(groups[k])
        if s:
            summaries[k] = s
    rnd_dist, rnd_sample = v16.run_rnd(pools, {"S3": pairs_main, "S4": pairs_tail})
    crit = {}
    for k in ("MAIN", "TAIL"):
        if k in summaries:
            rois = rnd_dist[f"paired{'S3' if k == 'MAIN' else 'S4'}"]
            crit[k] = {"rndPercentile": v16.rnd_percentile(rois, summaries[k]["roi"]),
                       **v16.rnd_dist_summary(rois)}

    result = {"ranAt": "2026-10-07", "script": str(Path(__file__).relative_to(ROOT)),
              "preReg": "fade-strategy-prereg v19TierRolling（跑前写死·64ff31c）",
              "data": dict(meta, universe="hist_odds体彩在售·had真实价结算"),
              "tierStats": tier_stat,
              "strategies": summaries,
              "rnd": {k: v16.rnd_dist_summary(v) for k, v in rnd_dist.items() if k.startswith("paired")},
              "criteria": crit,
              "verdict": "INFORMATIONAL(纯信息性·影子级已拍板·各spec realized ROI 见表)",
              "tickets": tickets}
    n = len(tickets)
    stake = sum(t["stake"] for t in tickets)
    payout = sum(t["ret"] for t in tickets)
    result["audit"] = {"reconcile": f"六spec共{n}票×2元=投入{stake:.0f}元 · 票面中奖金额和={payout:.2f}元 · "
                                    f"总realized ROI {payout/stake-1:+.2%}",
                       "recompute": audit_recompute(result, verbose=False)}
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    OUT_HTML.write_text(gen_html(result), encoding="utf-8")
    print("\n" + "=" * 72)
    for k, s in summaries.items():
        c = crit.get(k, {})
        print(f"  {k:5s} {s['nTickets']:4d}票 投入{s['stake']:6.0f} 中奖{s['ret']:8.2f} "
              f"realized ROI {s['roi']:+7.1%} CI[{s['boot']['p2.5']:+.1%},{s['boot']['p97.5']:+.1%}] "
              f"· 扣最大单日{s['roiExMaxDay']:+.1%} · RND分位{c.get('rndPercentile', '—')}")
    print(f"对账: {result['audit']['reconcile']}")
    print(f"产物: {OUT_JSON.name} + {OUT_HTML.name}")
    return result


def gen_html(r):
    rows = "".join(
        f"<tr><td>{k}</td><td class='num'>{s['nTickets']}</td><td class='num'>{s['stake']:.0f}</td>"
        f"<td class='num'>{s['ret']:.2f}</td>"
        f"<td class='num {'ok' if s['roi'] > 0 else 'bad'}'>{s['roi']:+.1%}</td>"
        f"<td class='num'>{s['hitRate']:.1%}</td><td class='num'>{s['payDayRate']:.1%}</td>"
        f"<td class='num'>{s['roiExMaxDay']:+.1%}</td>"
        f"<td class='num'>{s['boot']['p2.5']:+.1%}~{s['boot']['p97.5']:+.1%}</td>"
        f"<td class='num'>{json.dumps(s['bySeason'], ensure_ascii=False)}</td></tr>"
        for k, s in r["strategies"].items())
    tier_rows = "".join(
        f"<tr><td>{t}</td><td class='num'>{s['n']}</td><td class='num'>{s['hitRate']:.1%}</td>"
        f"<td class='num'>{s['anchor']:.1%}</td>"
        f"<td class='num {'ok' if abs(s['deltaPp']) <= 8 else 'warn'}'>{s['deltaPp']:+.1f}pp</td></tr>"
        for t, s in r["tierStats"].items())
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>v19 五挡+滚动池回测 · realized ROI 审计报告</title>
<style>
:root{{--ink:#1c2733;--sub:#5b6b7a;--line:#dde4ea;--bg:#f7f9fb;--card:#fff;--ok:#0f6b4f;--bad:#a33;--warn:#b3541e;--accent:#1a5fb4}}
body{{font-family:"Microsoft YaHei","PingFang SC",sans-serif;color:var(--ink);background:var(--bg);margin:0;padding:24px 16px;max-width:1100px;margin-inline:auto}}
h1{{font-size:20px;margin:0 0 4px}} h2{{font-size:15px;margin:20px 0 8px;padding-bottom:5px;border-bottom:2px solid var(--accent)}}
.meta{{color:var(--sub);font-size:12.5px;margin-bottom:12px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:13px 16px;margin:8px 0}}
table{{border-collapse:collapse;width:100%;font-size:12px;margin:5px 0}}
th,td{{border:1px solid var(--line);padding:3px 6px;text-align:left}} th{{background:#f0f4f7}}
.num{{font-variant-numeric:tabular-nums}} .ok{{color:var(--ok);font-weight:600}} .bad{{color:var(--bad);font-weight:600}} .warn{{color:var(--warn);font-weight:600}}
.small{{font-size:11.5px;color:var(--sub)}} pre{{background:#f0f4f7;border-radius:8px;padding:10px;font-size:11.5px}}
</style></head><body>
<h1>v19 五挡+滚动池回测 · realized ROI（体彩真实价）</h1>
<div class="meta">跑时 {r['ranAt']} · {r['data']['matches']} 场 {r['data']['days']} 日 · V3W 可测 {r['data']['v3Predicted']} · 预注册 v19</div>
<h2>一、六 spec · realized ROI（投注金额 vs 中奖金额·大哥口径）</h2>
<div class="card"><table><tr><th>spec</th><th>票数</th><th>投注金额</th><th>中奖金额</th><th>realized ROI</th><th>全中率</th><th>回款日率</th><th>扣最大单日</th><th>bootstrap95%CI</th><th>分赛季</th></tr>{rows}</table></div>
<h2>二、分挡 realized 命中率 vs 校准锚</h2>
<div class="card"><table><tr><th>挡</th><th>n</th><th>realized 命中</th><th>锚</th><th>偏差</th></tr>{tier_rows}</table>
<p class="small">偏差 |Δ|≤8pp=锚有效·>8pp 黄标（调锚须预注册变更）</p></div>
<h2>三、对账行</h2>
<div class="card"><pre>{r['audit']['reconcile']}</pre><p class="small">票级复算 {r['audit']['recompute']}</p></div>
<div class="small">开发者 sszhang · 2026-10-07 · tickets[] 票面可独立重算 · 全量明细见 JSON</div>
</body></html>"""


def audit_recompute(result, verbose=True):
    groups = defaultdict(list)
    for t in result["tickets"]:
        groups[t["strat"]].append(t)
    fails = []
    for key, ts in groups.items():
        s = result["strategies"].get(key)
        if not s:
            fails.append(f"{key} 汇总缺失")
            continue
        stake, ret = sum(t["stake"] for t in ts), sum(t["ret"] for t in ts)
        for name, got, want in (("nTickets", len(ts), s["nTickets"]), ("stake", round(stake, 2), s["stake"]),
                                ("ret", round(ret, 2), s["ret"]), ("roi", round(ret / stake - 1, 4), s["roi"]),
                                ("hitRate", round(sum(1 for t in ts if t["hit"]) / len(ts), 4), s["hitRate"])):
            if got != want:
                fails.append(f"{key}.{name}: {got}≠{want}")
        for t in ts:                                   # 逐票结算复算
            prod = 1.0
            for l in t["legs"]:
                if not l["hit"]:
                    prod = None
                    break
                prod *= l["pickOdds"]
            want_ret = round(2 * prod, 2) if prod else 0.0
            if abs(t["ret"] - want_ret) > 0.01:
                fails.append(f"{t['date']} {key}: 结算复算 {want_ret}≠{t['ret']}")
                break
    verdict = "PASS" if not fails else f"FAIL——{fails[:3]}"
    if verbose:
        print(f"[审计②票级复算] {verdict}")
    return verdict


def audit():
    result = json.loads(OUT_JSON.read_text(encoding="utf-8"))
    print(f"[审计①对账行] {result['audit']['reconcile']}")
    print(f"[审计②票级复算] {audit_recompute(result)}")
    dates = sorted({t["date"] for t in result["tickets"]})
    legs_by_date, _, _ = build_legs(load_hist())
    pools = rolling_pools(legs_by_date)
    fails = []
    for d in dates[::max(1, len(dates) // 3)][:3]:
        pool = pools.get(d, [])
        for spec, tiers in SPECS.items():
            top = 2 if spec == "TOP2" else 8
            want = sorted((l["home"], l["pick"]) for l in select(pool, tiers, top=top))
            got = sorted((l["home"], l["pick"]) for t in result["tickets"]
                         if t["date"] == d and t["strat"] == spec for l in t["legs"])
            if (want if len(want) >= (2 if spec == "TOP2" else 3) else []) != got:
                fails.append(f"{d} {spec} 重放不符")
    v3 = "PASS" if not fails else f"FAIL——{fails[:2]}"
    print(f"[审计③全链路重放·3天·含V3W重预测] {v3}")
    print("审计完成")


if __name__ == "__main__":
    if "--audit" in sys.argv:
        audit()
    else:
        run(smoke="--smoke" in sys.argv)
