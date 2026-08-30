#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实票账本报告：data/06-tickets/tickets.json → tickets.html。

四块（docs/2026-08-25-tickets-design.html §6）：
① 资金曲线：累计净利逐票折线（结算时序）+ y=0 基线，单系列无图例
② 票务清单表：票面/结算/来源，撤销腿灰显
③ 玩法分解：命中注派彩与全部本金按"注内腿均分"归因到 market（HAD/CRS/TTG/HAFU）
④ 纪律对照：tickets.json meta.disciplineEvents（手工维护的决策事件对账）

色对经 dataviz validate_palette.js 六检全过（蓝/橙 diverging，protan/deutan ΔE≥21，
绿红对 deutan 5.3 不可用）：light #1a6faf/#c2620a · dark #4a94d1/#cf7f36。

用法：
  python ticket_report.py        # 重刷报告
（backfill.settle_tickets 结算有变化时自动调用 render_report）
"""
import json
import sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path

from common import log, ROOT

SRC = ROOT / "data" / "06-tickets" / "tickets.json"
OUT = ROOT / "data" / "06-tickets" / "tickets.html"
MARKETS = ["HAD", "CRS", "TTG", "HAFU"]


def settled_tickets(tickets: list) -> list:
    return sorted((t for t in tickets if t.get("settled", {}).get("status") == "settled"),
                  key=lambda t: t["settled"].get("settledAt", ""))


def cum_points(tickets: list) -> list:
    """结算时序累计净利点列：[('起点',0), (id, 累计)]。"""
    pts, acc = [("起点", 0.0)], 0.0
    for t in settled_tickets(tickets):
        acc += t["settled"]["net"]
        pts.append((t["id"], round(acc, 2)))
    return pts


def market_attribution(tickets: list) -> dict[str, float]:
    """玩法归因：每注本金与命中注派彩均按注内腿数均分到 market。

    4串11 的 2串1 命中注派彩分到两腿 market 各半（本金口径同），与手算口径一致
    （T001~T003 验证：CRS+114.5/TTG+120/HAFU+3.9/HAD-11 → 合计+227.4）。
    """
    pay, stake = defaultdict(float), defaultdict(float)
    for t in settled_tickets(tickets):
        legs, unit = t["legs"], t["unitStake"]
        for size in range(2, len(legs) + 1):
            for combo in combinations(range(len(legs)), size):
                for i in combo:
                    stake[legs[i]["market"]] += unit / len(combo)
                if all(legs[i].get("result") == "hit" for i in combo):
                    odds = 1.0
                    for i in combo:
                        odds *= legs[i]["odds"]
                    for i in combo:
                        pay[legs[i]["market"]] += unit * odds / len(combo)
    return {m: round(pay[m] - stake[m], 2) for m in MARKETS if m in stake or m in pay}


def svg_curve(pts: list) -> str:
    """① 资金曲线：2px 折线 + r4 表面环点 + 终点直接标签；y=0 基线加重。"""
    if len(pts) < 2:
        return '<p class="empty">已结算票不足 2 张，曲线待生长</p>'
    W, H = 720, 240
    pl, pr, pt_, pb = 56, 30, 18, 34
    ys = [v for _, v in pts]
    lo, hi = min(min(ys), 0), max(max(ys), 0)
    span = (hi - lo) or 1.0

    def X(i):
        return pl + i * (W - pl - pr) / (len(pts) - 1)

    def Y(v):
        return pt_ + (hi - v) * (H - pt_ - pb) / span

    grid = []
    for k in range(5):
        v = lo + span * k / 4
        y = Y(v)
        grid.append(f'<line x1="{pl}" y1="{y:.1f}" x2="{W - pr}" y2="{y:.1f}" class="grid"/>'
                    f'<text x="{pl - 6}" y="{y + 4:.1f}" class="tick" text-anchor="end">{v:+.0f}</text>')
    path = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, (_, v) in enumerate(pts))
    dots = []
    for i, (name, v) in enumerate(pts):
        dots.append(f'<circle cx="{X(i):.1f}" cy="{Y(v):.1f}" r="4" class="dot">'
                    f'<title>{name} · 累计净利 {v:+.1f}</title></circle>')
        dots.append(f'<text x="{X(i):.1f}" y="{H - pb + 18}" class="tick" text-anchor="middle">{name}</text>')
    return (f'<svg viewBox="0 0 {W} {H}" class="chart" role="img" aria-label="累计净利资金曲线">'
            + "".join(grid)
            + f'<line x1="{pl}" y1="{Y(0):.1f}" x2="{W - pr}" y2="{Y(0):.1f}" class="zero"/>'
            + f'<polyline points="{path}" class="line"/>'
            + "".join(dots)
            + f'<text x="{X(len(pts) - 1):.1f}" y="{Y(pts[-1][1]) - 10:.1f}" class="endlab" text-anchor="end">'
              f'{pts[-1][1]:+.1f}</text>'
            + "</svg>")


def svg_bars(rows: list) -> str:
    """③ 玩法分解：横向 diverging 条形，数据端 4px 圆角、基线端平，端部直接标值。"""
    if not rows:
        return '<p class="empty">暂无已结算票</p>'
    W, RH, BH = 720, 46, 20
    H = RH * len(rows) + 8
    pl, pr = 60, 70
    vals = [v for _, v in rows]
    lo, hi = min(min(vals), 0), max(max(vals), 0)
    span = (hi - lo) or 1.0
    x0 = pl + (0 - lo) * (W - pl - pr) / span
    out = [f'<svg viewBox="0 0 {W} {H}" class="chart" role="img" aria-label="玩法盈亏分解">',
           f'<line x1="{x0:.1f}" y1="4" x2="{x0:.1f}" y2="{H - 4}" class="zero"/>']
    for i, (mkt, v) in enumerate(rows):
        cy = 4 + i * RH + (RH - BH) // 2
        w = max(abs(v) * (W - pl - pr) / span, 1.0)
        r = min(4.0, w / 2, BH / 2)
        if v >= 0:
            d = (f'M{x0:.1f},{cy} L{x0 + w - r:.1f},{cy} A{r},{r} 0 0 1 {x0 + w:.1f},{cy + r} '
                 f'L{x0 + w:.1f},{cy + BH - r} A{r},{r} 0 0 1 {x0 + w - r:.1f},{cy + BH} L{x0:.1f},{cy + BH} Z')
            cls, lx, anc = "bar-pos", x0 + w + 6, "start"
        else:
            d = (f'M{x0:.1f},{cy} L{x0 - w + r:.1f},{cy} A{r},{r} 0 0 0 {x0 - w:.1f},{cy + r} '
                 f'L{x0 - w:.1f},{cy + BH - r} A{r},{r} 0 0 0 {x0 - w + r:.1f},{cy + BH} L{x0:.1f},{cy + BH} Z')
            cls, lx, anc = "bar-neg", x0 - w - 6, "end"
        out.append(f'<path d="{d}" class="{cls}"><title>{mkt} 净利 {v:+.1f}</title></path>')
        out.append(f'<text x="{pl - 10}" y="{cy + BH / 2 + 4:.1f}" class="tick" text-anchor="end">{mkt}</text>')
        out.append(f'<text x="{lx:.1f}" y="{cy + BH / 2 + 4:.1f}" class="vlab" text-anchor="{anc}">{v:+.1f}</text>')
    out.append("</svg>")
    return "".join(out)


def ticket_rows(tickets: list) -> str:
    """② 票务清单表（pending 票也列出，结算列示待结算）。"""
    rows = []
    for t in tickets:
        st = t.get("settled", {})
        legs = " / ".join(
            f'<span class="{"hit" if l.get("result") == "hit" else ("miss" if l.get("result") == "miss" else "pend")}">'
            f'{"<s>" if l.get("revoked") else ""}{l["code"]} {l["market"]} {l["pick"]}@{l["odds"]}'
            f'{"</s>" if l.get("revoked") else ""}</span>'
            for l in t["legs"])
        cross = "跨日" if len(t.get("matchDays", [])) > 1 else ""
        if st.get("status") == "settled":
            res = (f'{st["hits"]}/{len(t["legs"])}关 · 回{st.get("winUnits", "-")}注 · '
                   f'派彩{st["payout"]:.1f} · 净<b class="{"pos" if st["net"] >= 0 else "neg"}">'
                   f'{st["net"]:+.1f}</b> ({st.get("roi", "-")}%)')
        else:
            res = "待结算"
        rows.append(
            f'<tr><td>{t["id"]}</td><td>{t["shape"]}{f"<br><span class=sub>{cross}</span>" if cross else ""}</td>'
            f'<td>{t["placedAt"][:16]}</td><td>{t["stake"]}</td><td class="legs">{legs}</td>'
            f'<td>{res}</td><td>{"补录" if t.get("backfilled") else ("转正" if t.get("source") == "promoted" else "手动")}</td></tr>')
    return "".join(rows)


def discipline_rows(meta: dict) -> str:
    """④ 纪律对照：meta.disciplineEvents（手工维护）。"""
    events = meta.get("disciplineEvents") or []
    if not events:
        return '<p class="empty">暂无登记事件（出票后的临场干预/撤单对账，出票时登记）</p>'
    rows = []
    for e in events:
        an, cn = e.get("actualNet") or 0, e.get("counterfactualNet") or 0   # 手工维护可能为 null（2026-08-30 结算撞线）
        cost = round(cn - an, 1)
        cls = "pos" if cost >= 0 else "neg"
        rows.append(f'<tr><td>{e["date"]}</td><td>{e["event"]}</td>'
                    f'<td>{an:+.1f}</td><td>{cn:+.1f}</td>'
                    f'<td class="{cls}">{cost:+.1f}</td><td class="sub">{e.get("verdict", "")}</td></tr>')
    return "".join(rows)


def render(data: dict) -> str:
    tickets = data.get("tickets", [])
    meta = data.get("meta", {})
    pts = cum_points(tickets)
    mkt = market_attribution(tickets)
    bars = sorted(mkt.items(), key=lambda kv: -kv[1])
    n_settled = len(settled_tickets(tickets))
    n_pending = len(tickets) - n_settled
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>实票账本 · tickets</title>
<style>
:root{{--ink:#1c2733;--sub:#5b6b7a;--line:#d8e0e8;--bg:#f6f8fa;--card:#fff;
--data:#1a6faf;--pos:#1a6faf;--neg:#c2620a}}
@media(prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--ink:#dbe4ec;--sub:#8fa1b3;--line:#2c3947;--bg:#101822;--card:#18222e;
--data:#4a94d1;--pos:#4a94d1;--neg:#cf7f36}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.7 "Segoe UI","Microsoft YaHei",sans-serif}}
main{{max-width:940px;margin:0 auto;padding:28px 26px 64px}}
h1{{font-size:22px;margin:0 0 2px}}h2{{font-size:17px;margin:36px 0 10px;padding-bottom:6px;border-bottom:1px solid var(--line)}}
.sub{{color:var(--sub);font-size:13px}}
.kpis{{display:flex;gap:12px;flex-wrap:wrap;margin:14px 0}}
.kpi{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 18px;min-width:110px}}
.kpi b{{font-size:20px;display:block}}.kpi span{{font-size:12px;color:var(--sub)}}
table{{border-collapse:collapse;width:100%;margin:10px 0;font-size:13.5px;background:var(--card)}}
th,td{{border:1px solid var(--line);padding:6px 9px;text-align:left;vertical-align:top}}
th{{background:var(--bg);font-weight:600}}
.legs span{{display:inline-block;margin:1px 3px 1px 0;padding:1px 6px;border-radius:4px;background:var(--bg);border:1px solid var(--line);font-size:12.5px}}
.legs .hit{{border-color:var(--pos);color:var(--pos)}}
.legs .miss{{border-color:var(--neg);color:var(--neg);opacity:.85}}
.legs .pend{{color:var(--sub)}}
.pos{{color:var(--pos)}}.neg{{color:var(--neg)}}
.chart{{width:100%;height:auto;background:var(--card);border:1px solid var(--line);border-radius:10px}}
.line{{fill:none;stroke:var(--data);stroke-width:2}}
.dot{{fill:var(--data);stroke:var(--card);stroke-width:2}}.dot:hover{{r:6}}
.grid{{stroke:var(--line);stroke-width:1}}
.zero{{stroke:var(--sub);stroke-width:1.5;stroke-dasharray:1 0}}
.tick{{fill:var(--sub);font-size:11px}}.vlab{{fill:var(--ink);font-size:12px;font-weight:600}}
.endlab{{fill:var(--data);font-size:13px;font-weight:700}}
.bar-pos{{fill:var(--pos)}}.bar-neg{{fill:var(--neg)}}
.bar-pos:hover,.bar-neg:hover{{opacity:.8}}
.empty{{color:var(--sub);font-size:13.5px}}
.src{{margin-top:40px;color:var(--sub);font-size:12.5px}}
</style></head><body><main>
<h1>实票账本</h1>
<p class="sub">实票=有结算记录的票，其余全是方案推演 · 派彩按票面形状算（4串11中2关只回1注2串1）· 刷新于结算时</p>
<div class="kpis">
<div class="kpi"><span>实票</span><b>{len(tickets)}<i style="font-size:13px;font-style:normal"> 张</i></b></div>
<div class="kpi"><span>已结算/待结算</span><b>{n_settled}<i style="font-size:13px;font-style:normal"> / {n_pending}</i></b></div>
<div class="kpi"><span>累计本金</span><b>{meta.get("totalStake", 0):.0f}<i style="font-size:13px;font-style:normal"> 元</i></b></div>
<div class="kpi"><span>累计净利</span><b class="{"pos" if meta.get("totalNet", 0) >= 0 else "neg"}">{meta.get("totalNet", 0):+.1f}<i style="font-size:13px;font-style:normal"> 元</i></b></div>
<div class="kpi"><span>整体ROI</span><b>{(meta.get("totalNet", 0) / meta["totalStake"] * 100):+.1f}%</b></div>
</div>

<h2>① 资金曲线（累计净利，按结算时序）</h2>
{svg_curve(pts)}

<h2>② 票务清单</h2>
<table><tr><th>ID</th><th>形状</th><th>出票时间</th><th>本金</th><th>票面四关</th><th>结算</th><th>来源</th></tr>
{ticket_rows(tickets)}</table>

<h2>③ 玩法分解（净利归因，命中注派彩与本金按注内腿均分）</h2>
{svg_bars(bars)}

<h2>④ 纪律对照（实票 vs 反事实）</h2>
<table><tr><th>日期</th><th>事件</th><th>实票净利</th><th>反事实净利</th><th>差异</th><th>评注</th></tr>
{discipline_rows(meta)}</table>

<p class="src">数据来源：data/06-tickets/tickets.json（{meta.get("lastUpdated", "")} 刷新）· 设计：docs/2026-08-25-tickets-design.html ·
色对经 dataviz validate_palette.js 验证（蓝/橙 diverging，protan ΔE≥21）</p>
</main></body></html>
"""


def render_report() -> Path:
    data = json.loads(SRC.read_text(encoding="utf-8"))
    OUT.write_text(render(data), encoding="utf-8")
    return OUT


def main() -> None:
    out = render_report()
    log("tickets", f"{len(settled_tickets(json.loads(SRC.read_text(encoding='utf-8'))['tickets']))} 票已结算 → {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
