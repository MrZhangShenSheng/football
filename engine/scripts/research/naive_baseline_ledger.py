# -*- coding: utf-8 -*-
"""朴素基线（gap前2场·双选·2串复式8元）逐日结算账单：dev 段 + 盲测段。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import score_family_model as sfm
from plan_optimizer import build_all, enrich, naive_plan

UNIT = 2.0


def ledger(days):
    rows = []
    for day in sorted(days):
        plan = naive_plan(days[day])
        if not plan:
            continue
        legs = plan["legs"]
        mult = 1.0
        all_pass = True
        leg_info = []
        for p in legs:
            scores = {s: o for s, o in p["legs"]}
            hit = p["actual"] in scores
            if hit:
                mult *= scores[p["actual"]]
            else:
                all_pass = False
            leg_info.append((p, hit))
        pay = mult * UNIT if all_pass else 0.0
        rows.append({"date": day, "legs": leg_info, "pay": pay,
                     "win": all_pass, "mult": mult if all_pass else 0.0})
    return rows


def main():
    meta_dev, dists_dev, meta_bl, dists_bl = build_all()
    dev = enrich(meta_dev, dists_dev)
    bl = enrich(meta_bl, dists_bl)
    by_dev, by_bl = defaultdict(list), defaultdict(list)
    for p in dev:
        by_dev[p["date"]].append(p)
    for p in bl:
        by_bl[p["date"]].append(p)

    seg_dev = ledger(by_dev)
    seg_bl = ledger(by_bl)

    def render(seg, title):
        tc = len(seg) * 8
        tp = sum(r["pay"] for r in seg)
        w = sum(1 for r in seg if r["win"])
        h = ["<h2>" + title + f"（{len(seg)} 天 · 投入 {tc} 元 · 回款 {tp:.0f} 元 · "
             f"ROI {(tp-tc)/tc*100 if tc else 0:+.1f}% · 中奖 {w}）</h2>"]
        h.append("<table><tr><th>日期</th><th>腿1（断层第1）</th><th>双选</th><th>实开</th>"
                 "<th>腿2（断层第2）</th><th>双选</th><th>实开</th><th>结果</th><th>派彩</th></tr>")
        for r in seg:
            (p1, h1), (p2, h2) = r["legs"]
            a1 = f"{p1['actual'][0]}:{p1['actual'][1]}"
            a2 = f"{p2['actual'][0]}:{p2['actual'][1]}"
            sel1 = f"{p1['top1'][0]}:{p1['top1'][1]} / {p1['top2'][0]}:{p1['top2'][1]}"
            sel2 = f"{p2['top1'][0]}:{p2['top1'][1]} / {p2['top2'][0]}:{p2['top2'][1]}"
            res = "✓ 全中" if r["win"] else "✗ 断关"
            cls = "win" if r["win"] else "lose"
            h.append(f"<tr class='{cls}'><td>{r['date'][5:]}</td>"
                     f"<td>{p1['code']} {p1['league']}</td><td>{sel1}</td><td><b>{a1}</b></td>"
                     f"<td>{p2['code']} {p2['league']}</td><td>{sel2}</td><td><b>{a2}</b></td>"
                     f"<td>{res}</td><td>{r['pay']:.0f}</td></tr>")
        h.append("</table>")
        return "\n".join(h)

    html = ["<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"]
    html.append("<title>朴素基线 · 逐日结算账单</title><style>")
    html.append("""body{font-family:-apple-system,"Microsoft YaHei",sans-serif;background:#f5f6f8;color:#1a1f2e;padding:20px;font-size:13px}
.wrap{max-width:1150px;margin:0 auto}
h1{font-size:19px}.sub{color:#5a6478;font-size:12px;margin:6px 0 8px}
h2{font-size:15px;margin:22px 0 8px}
table{width:100%;border-collapse:collapse;background:#fff;border-radius:10px;overflow:hidden;margin-bottom:10px}
th{background:#eef1f6;padding:7px 8px;text-align:left;font-size:12px}
td{padding:5px 8px;border-bottom:1px solid #f0f2f5;font-size:12px;font-family:ui-monospace,Menlo,monospace}
tr.win td{background:#e3f5ea}
tr.lose td{background:#fdf3f2}
.foot{color:#5a6478;font-size:12px;margin-top:10px}""")
    html.append("</style></head><body><div class='wrap'>")
    html.append("<h1>朴素基线 · 逐日结算账单（gap前2场·双选·2串复式 8元/天）</h1>")
    html.append("<div class='sub'>腿=模型断层（top1−top2 概率差）排序 · 双选=模型 top1+top2 · "
                "绿=全中派彩 · 红=断关。无泄漏时点滚动。</div>")
    tc_d = len(seg_dev) * 8
    tp_d = sum(r["pay"] for r in seg_dev)
    tc_b = len(seg_bl) * 8
    tp_b = sum(r["pay"] for r in seg_bl)
    html.append(f"<div class='sub'>汇总：dev {len(seg_dev)}天 投{tc_d} 回{tp_d:.0f} "
                f"ROI {(tp_d-tc_d)/tc_d*100 if tc_d else 0:+.1f}% ｜ "
                f"盲测 {len(seg_bl)}天 投{tc_b} 回{tp_b:.0f} "
                f"ROI {(tp_b-tc_b)/tc_b*100 if tc_b else 0:+.1f}%</div>")
    html.append(render(seg_dev, "Dev 段（2026-04 ~ 2026-06）"))
    html.append(render(seg_bl, "盲测段（2026-07 ~ 2026-09）"))
    html.append("<div class='foot'>派彩=两腿命中比分赔率乘积×2元；断关=任一腿实开不在该腿双选内。"
                "</div></div></body></html>")

    out = Path("data/03-predictions/2026-09-29-naive-baseline-ledger.html")
    out.write_text("\n".join(html), encoding="utf-8")
    print(f"账单已写 {out}")
    for title, seg in (("dev", seg_dev), ("盲测", seg_bl)):
        tc = len(seg) * 8
        tp = sum(r["pay"] for r in seg)
        w = sum(1 for r in seg if r["win"])
        print(f"{title}: {len(seg)} 天 · 投入 {tc} 元 · 回款 {tp:.0f} 元 · "
              f"ROI {(tp-tc)/tc*100 if tc else 0:+.1f}% · 中奖 {w} 天")
    print("\n盲测段逐日：")
    for r in seg_bl:
        (p1, h1), (p2, h2) = r["legs"]
        print(f"  {r['date'][5:]} {p1['code']}[{p1['top1'][0]}:{p1['top1'][1]}/"
              f"{p1['top2'][0]}:{p1['top2'][1]}→{p1['actual'][0]}:{p1['actual'][1]}{'✓' if h1 else '✗'}] "
              f"{p2['code']}[{p2['top1'][0]}:{p2['top1'][1]}/"
              f"{p2['top2'][0]}:{p2['top2'][1]}→{p2['actual'][0]}:{p2['actual'][1]}{'✓' if h2 else '✗'}] "
              f"{'全中 %.0f 元' % r['pay'] if r['win'] else '断关'}")


if __name__ == "__main__":
    from collections import defaultdict
    main()
