# -*- coding: utf-8 -*-
"""结算明细导出：盲测 692 场的逐场对照（市场 top1 vs 模型 top1 vs 实开）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import score_family_model as sfm
from collections import Counter, defaultdict

UNIT = 2.0
CUT = sfm.CUT


def main():
    zh = {}
    for tid, srcs in sfm.load_aliases().items():
        if srcs.get("zh"):
            zh[srcs["zh"]] = tid
    tl = sfm.league_timeline()
    hist = sfm.load_hist()
    blind = []
    for m in hist:
        if m["date"] < CUT:
            continue
        hid, aid = zh.get(m["home_zh"]), zh.get(m["away_zh"])
        if hid and aid:
            blind.append({**m, "hid": hid, "aid": aid})
    merged = [("L", d, h, a, hg, ag, None) for d, h, a, hg, ag in tl]
    merged += [("B", m["date"], m["hid"], m["aid"], m["actual"][0], m["actual"][1], i)
               for i, m in enumerate(blind)]
    merged.sort(key=lambda r: (r[1], 0 if r[0] == "L" else 1))

    stats = defaultdict(sfm.TeamStats)
    X_tr, y_tr, X_bl, meta = [], [], [], []
    tot_g = tot_n = 0
    for r in merged:
        kind, date, h, a, hg, ag = r[0], r[1], r[2], r[3], r[4], r[5]
        lg_gf = (tot_g / tot_n) if tot_n >= 50 else 2.6
        fv_h = stats[h].vector(0, lg_gf)
        fv_a = stats[a].vector(1, lg_gf)
        if kind == "L" and date < CUT and fv_h[12] >= sfm.MIN_HIST and fv_a[12] >= sfm.MIN_HIST:
            X_tr.append(sfm.feature_row((fv_h, fv_a)))
            y_tr.append(sfm.CLASSES.index(sfm.family_of(hg, ag)))
        elif kind == "B":
            meta.append(blind[r[6]])
            X_bl.append(sfm.feature_row((fv_h, fv_a)))
        stats[h].add(hg, ag, True)
        stats[a].add(ag, hg, False)
        tot_g += hg + ag
        tot_n += 1
    model = sfm.train_softmax(X_tr, y_tr, len(sfm.CLASSES))
    P = sfm.predict_proba(model, X_bl)
    fam_dist = defaultdict(Counter)
    for r in merged:
        if r[0] == "L" and r[1] < CUT:
            fam_dist[sfm.family_of(r[4], r[5])][(r[4], r[5])] += 1
    dists = []
    for row in P:
        d = {}
        for ci, cname in enumerate(sfm.CLASSES):
            tot = sum(fam_dist.get(cname, {}).values()) or 1
            for s, c in fam_dist.get(cname, {}).items():
                d[s] = d.get(s, 0.0) + row[ci] * (c / tot)
        dists.append(d)

    # 逐场明细
    lines = []
    tot_m = tot_d = 0.0
    hit_m = hit_d = 0
    for i, m in enumerate(meta):
        act = m["actual"]
        close = m["odds"]
        mkt = [s for s, _ in sorted(close.items(), key=lambda kv: kv[1])]
        mdl = [s for s, _ in sorted(dists[i].items(), key=lambda kv: -kv[1])]
        o_act = close.get(act)
        d_m = act in mkt[:1]
        d_d = mdl[0] == act and o_act is not None
        pay_m = o_act * UNIT if d_m else 0.0
        pay_d = o_act * UNIT if d_d else 0.0
        tot_m += pay_m
        tot_d += pay_d
        hit_m += int(d_m)
        hit_d += int(d_d)
        p_mdl = dists[i].get((act[0], act[1]), 0)
        lines.append({
            "date": m["date"], "code": m["code"], "league": m["league"],
            "home": m["home_zh"], "away": m["away_zh"],
            "actual": f"{act[0]}:{act[1]}", "odds": o_act,
            "mkt_top1": f"{mkt[0][0]}:{mkt[0][1]}", "mkt_odds": close[mkt[0]],
            "mkt_hit": d_m, "pay_m": round(pay_m, 1),
            "mdl_top1": f"{mdl[0][0]}:{mdl[0][1]}",
            "mdl_p": round(dists[i].get(mdl[0], 0), 3),
            "mdl_hit": d_d, "pay_d": round(pay_d, 1),
            "p_act": round(p_mdl, 3), "rank_act": mdl.index((act[0], act[1])) + 1 if (act[0], act[1]) in mdl else None,
        })

    # HTML 输出
    n = len(lines)
    html = ["<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"]
    html.append("<title>比分族模型 · 盲测结算明细</title><style>")
    html.append("""body{font-family:-apple-system,"Microsoft YaHei",sans-serif;background:#f5f6f8;color:#1a1f2e;padding:20px;font-size:13px}
.wrap{max-width:1200px;margin:0 auto}
h1{font-size:19px}.sub{color:#5a6478;font-size:12px;margin:6px 0 14px}
.sum{display:flex;gap:12px;margin-bottom:14px;flex-wrap:wrap}
.card{background:#fff;border:1px solid #e3e7ee;border-radius:10px;padding:12px 16px;flex:1;min-width:180px}
.card .v{font-size:22px;font-weight:700}
.card .l{color:#5a6478;font-size:12px}
.pos{color:#1e8e5a}.neg{color:#c0392b}
table{width:100%;border-collapse:collapse;background:#fff;border-radius:10px;overflow:hidden}
th{background:#eef1f6;padding:7px 8px;text-align:left;font-size:12px;position:sticky;top:0}
td{padding:5px 8px;border-bottom:1px solid #f0f2f5;font-family:ui-monospace,Menlo,monospace;font-size:12px}
tr.hit-m td{background:#eaf7ef}
tr.hit-d td{background:#eaf7ef}
tr.both td{background:#d5f0e2}
span.tag{display:inline-block;padding:0 6px;border-radius:4px;font-size:11px}
.tm{background:#e8f0fe;color:#2b6cb0}.td{background:#fdeeee;color:#c0392b}
.foot{margin-top:12px;color:#5a6478;font-size:12px}""")
    html.append("</style></head><body><div class='wrap'>")
    html.append("<h1>比分族特征模型 · 盲测结算明细（k=1 单关）</h1>")
    html.append(f"<div class='sub'>盲测 {n} 场（2026-07-01 后 · 两队可映射）· 绿色行=命中 · "
                f"深绿=双方同中 · 市场列蓝标/模型列红标</div>")
    html.append(f"<div class='sum'>"
                f"<div class='card'><div class='v'>{tot_m/cost*100 if (cost:=n*UNIT) else 0:.1f}%</div><div class='l'>市场回收率（{hit_m} 中 / {tot_m:.0f} 元）</div></div>"
                f"<div class='card'><div class='v pos'>{tot_d/cost*100:.1f}%</div><div class='l'>模型回收率（{hit_d} 中 / {tot_d:.0f} 元）</div></div>"
                f"<div class='card'><div class='v'>{(tot_d-tot_m)/cost*100:+.1f}pp</div><div class='l'>回收率差</div></div>"
                f"<div class='card'><div class='v'>{tot_d-tot_m:+.0f}元</div><div class='l'>净额差（{n}×2元本金）</div></div>"
                f"</div>")
    html.append("<table><tr><th>日期</th><th>编号</th><th>联赛</th><th>对阵</th><th>实开</th>"
                "<th>实开赔率</th><th>市场top1</th><th>@</th><th>市场命中</th><th>市场派彩</th>"
                "<th>模型top1</th><th>模型P</th><th>模型命中</th><th>模型派彩</th><th>实开在模型位次</th></tr>")
    for r in lines:
        cls = "both" if (r["mkt_hit"] and r["mdl_hit"]) else ("hit-m" if r["mkt_hit"] else ("hit-d" if r["mdl_hit"] else ""))
        html.append(f"<tr class='{cls}'><td>{r['date'][5:]}</td><td>{r['code']}</td>"
                    f"<td>{r['league']}</td><td>{r['home']} vs {r['away']}</td>"
                    f"<td><b>{r['actual']}</b></td><td>{r['odds'] or '池外'}</td>"
                    f"<td><span class='tag tm'>{r['mkt_top1']}</span></td><td>@{r['mkt_odds']}</td>"
                    f"<td>{'✓' if r['mkt_hit'] else '—'}</td><td>{r['pay_m']}</td>"
                    f"<td><span class='tag td'>{r['mdl_top1']}</span></td><td>{r['mdl_p']}</td>"
                    f"<td>{'✓' if r['mdl_hit'] else '—'}</td><td>{r['pay_d']}</td>"
                    f"<td>{r['rank_act'] or '池外'}</td></tr>")
    html.append("</table>")
    html.append(f"<div class='foot'>口径：k=1 单关每场 2 元；命中按实际中奖项赔率赔付；"
                f"池外=实开比分不在体彩 31 项池内（不计命中不赔付）。"
                f"模型=族特征 softmax（full train &lt;{CUT}）；无泄漏时点滚动。</div>")
    html.append("</div></body></html>")

    out = Path("data/03-predictions/2026-09-29-score-family-detail.html")
    out.write_text("\n".join(html), encoding="utf-8")
    print(f"明细已写 {out} · {n} 场")
    print(f"市场 {hit_m} 中 / {tot_m:.0f} 元 · 模型 {hit_d} 中 / {tot_d:.0f} 元")
    # 控制台摘要：模型独中 + 市场独中
    only_d = [r for r in lines if r["mdl_hit"] and not r["mkt_hit"]]
    only_m = [r for r in lines if r["mkt_hit"] and not r["mdl_hit"]]
    print(f"\n模型独中 {len(only_d)} 场（模型中/市场不中）：")
    for r in sorted(only_d, key=lambda x: -x["pay_d"])[:20]:
        print(f"  {r['date'][5:]} {r['code']:7} {r['league']:5} {r['home'][:8]} vs {r['away'][:8]:9} "
              f"实开{r['actual']} @{r['odds']}")
    print(f"\n市场独中 {len(only_m)} 场：")
    for r in sorted(only_m, key=lambda x: -x["pay_m"])[:10]:
        print(f"  {r['date'][5:]} {r['code']:7} {r['league']:5} {r['home'][:8]} vs {r['away'][:8]:9} "
              f"实开{r['actual']} @{r['odds']}")


if __name__ == "__main__":
    main()
