#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""胜率趋势报告：回填赛果后自动跑，产出 4 张 SVG 折线图 + 分桶胜率表 → data/04-summaries/trend.html。

设计依据（docs/2026-08-22-learning-loop-design.html + 文献调研 2026-08-22）：
- 主曲线 = 累计 log loss（arXiv:1908.08980：Ignorance/log-loss 实证优于 RPS/Brier）+ 市场基线对照线
- 累计方向命中率（展示口径）+ 滚动 20 场副线（捕捉 regime change，金融 equity curve 惯例）
- CLV 走势（DK 近似口径，体彩抽水结构性负值看相对趋势）
- 校准图简化版（arXiv:2008.03033 CORP 思想的固定分桶版：p_final 最高概率 vs 实际胜率）
- 自动结论硬门槛：已回填 n<30 只输出"数据积累期"，n≥30 才启用倒挂/趋势规则

用法：
  python trend_report.py          # 读 corpus.json → trend.html
"""
import json
import math
from collections import defaultdict
from datetime import date
from pathlib import Path

from common import log, ROOT

CORPUS = ROOT / "data" / "04-summaries" / "corpus.json"
OUT = ROOT / "data" / "04-summaries" / "trend.html"
CONCLUSION_MIN_N = 30
ROLLING_WINDOW = 20
# 校准分桶（p_final 三向最大值）
CAL_BINS = [(0.0, 0.40), (0.40, 0.55), (0.55, 0.70), (0.70, 1.01)]


def outcome_idx(r: dict) -> int | None:
    """赛果 → 0主胜/1平/2客胜。"""
    res = r.get("result")
    if not res or "-" not in str(res):
        return None
    try:
        hg, ag = (int(x) for x in str(res).split("-"))
    except ValueError:
        return None
    return 0 if hg > ag else (1 if hg == ag else 2)


def logloss(probs: list[float], oi: int) -> float:
    return -math.log(max(probs[oi], 1e-9))


def market_probs(r: dict) -> list[float] | None:
    """市场基线：corpus 的 p_final 在纯市场锚场次即市场去水概率（dc_used=false 时二者同值，
    dc_used=true 时 p_final 已含模型贡献——此时市场线退化为 p_final 本身，图上注明口径）。"""
    return r.get("p_final")


def normalize_in_plan(v) -> str:
    """in_plan 归一化：'False'/False/None → 未入串；'A'~'D' → 入串方案名。"""
    if v is None or v is False or str(v) in ("False", "false", "0"):
        return "未入串"
    return f"入串{v}" if str(v) in ("A", "B", "C", "D") else "未入串"


def pick_type(pick) -> str:
    """pick 类型：比分（含'-'）vs 方向。"""
    if pick and "-" in str(pick):
        return "比分"
    return "方向"


def grade_label(g) -> str:
    """grade 显示兼容：数字 1~4 → A~D（v4.5）；字母原样（v4.6 经 corpus 归一仍可能透传字母）。"""
    return chr(64 + g) if isinstance(g, int) and 1 <= g <= 4 else str(g)


def build_series(records: list[dict]) -> dict:
    """按轮次（round 字段）聚合的评估序列。"""
    filled = [r for r in records if outcome_idx(r) is not None]
    by_round = defaultdict(list)
    for r in filled:
        by_round[r.get("round", "?")].append(r)
    rounds = sorted(by_round)
    series = {"rounds": [], "cum": {"n": 0, "ll_model": 0.0, "ll_mkt": 0.0, "hit": 0, "score_hit": 0, "score_n": 0}}
    rows_out = []
    for rd in rounds:
        recs = by_round[rd]
        ll_m = ll_k = 0.0
        hit = score_hit = score_n = 0
        for r in recs:
            oi = outcome_idx(r)
            pf = r.get("p_final")
            if pf and len(pf) == 3:
                ll_m += logloss(pf, oi)
                pm = market_probs(r)
                if pm and len(pm) == 3:
                    ll_k += logloss(pm, oi)
            if r.get("directionHit"):
                hit += 1
            pick = r.get("pick")
            if pick and "-" in str(pick):
                score_n += 1
                if r.get("scoreHit"):
                    score_hit += 1
        s = series["cum"]
        s["n"] += len(recs)
        s["ll_model"] += ll_m
        s["ll_mkt"] += ll_k
        s["hit"] += hit
        s["score_hit"] += score_hit
        s["score_n"] += score_n
        row = {
            "round": rd, "n": len(recs),
            "cum_n": s["n"],
            "cum_logloss": round(s["ll_model"] / max(s["n"], 1), 4),
            "cum_logloss_mkt": round(s["ll_mkt"] / max(s["n"], 1), 4),
            "cum_hit_rate": round(s["hit"] / max(s["n"], 1), 4),
            "cum_score_rate": round(s["score_hit"] / max(s["score_n"], 1), 4) if s["score_n"] else None,
        }
        rows_out.append(row)
        series["rounds"] = rows_out
    # 滚动窗口（按已回填场序，非按轮）
    hits = [1 if r.get("directionHit") else 0 for r in filled]
    rolling = []
    for i in range(len(hits)):
        lo = max(0, i - ROLLING_WINDOW + 1)
        win = hits[lo:i + 1]
        rolling.append(round(sum(win) / len(win), 4))
    series["rolling"] = rolling
    series["filled"] = filled
    return series


def clv_of(r: dict) -> float | None:
    v = r.get("clv")
    if v is None:
        v = r.get("clv_approx_dk")
    return v


def build_calibration(filled: list[dict]) -> list[dict]:
    """p_final 最高概率分桶 → 预测概率均值 vs 实际胜率。"""
    buckets = []
    for lo, hi in CAL_BINS:
        members = []
        for r in filled:
            pf = r.get("p_final")
            if not pf or len(pf) != 3:
                continue
            pmax = max(pf)
            if lo <= pmax < hi:
                members.append((pmax, 1 if r.get("directionHit") else 0))
        if members:
            pred = sum(m[0] for m in members) / len(members)
            obs = sum(m[1] for m in members) / len(members)
            buckets.append({"bin": f"{lo:.0%}~{hi:.0%}", "n": len(members),
                            "pred": round(pred, 4), "obs": round(obs, 4)})
        else:
            buckets.append({"bin": f"{lo:.0%}~{hi:.0%}", "n": 0, "pred": None, "obs": None})
    return buckets


def build_buckets(filled: list[dict]) -> dict[str, list]:
    """五维下钻：联赛/星级/等级/pick类型/入串。"""
    dims = {"league": lambda r: r.get("league") or "?",
            "star": lambda r: f"{'★' * r['stars']}" if r.get("stars") else "无",
            "grade": lambda r: f"{grade_label(r['grade'])}级" if r.get("grade") else "?",
            "pick_type": lambda r: pick_type(r.get("pick")),
            "plan": lambda r: normalize_in_plan(r.get("in_plan"))}
    out = {}
    for name, key_fn in dims.items():
        agg = defaultdict(lambda: [0, 0])  # total, hit
        for r in filled:
            k = key_fn(r)
            agg[k][0] += 1
            if r.get("directionHit"):
                agg[k][1] += 1
        out[name] = [{"key": k, "n": t, "hit": h,
                      "rate": round(h / t, 4) if t else 0} for k, (t, h) in
                     sorted(agg.items(), key=lambda kv: -kv[1][0])]
    return out


def build_plans(plans: dict, records: list[dict]) -> list[dict]:
    """方案层准确率：串关全中才算赢。plans key='{round}:{方案名}'，值=场次编号 list（旧 dict 格式跳过）。

    每方案输出：各关命中态 / 全中·断关 / 断关场次（最弱环节）。
    串关惩罚 = 实测方案全中率 vs 单场命中率连乘理论值 的落差。
    """
    rec_by_key = {}
    for r in records:
        rec_by_key[(r.get("round"), r.get("code"))] = r
    out = []
    for key, codes in sorted(plans.items()):
        if not isinstance(codes, list):
            continue  # 旧格式（dict 含 picks/odds）暂不统计，格式统一后自动纳入
        round_id, name = key.split(":", 1)
        legs, breaks = [], []
        hit_all = True
        for code in codes:
            r = rec_by_key.get((round_id, code))
            if not r:
                legs.append({"code": code, "status": "无记录"})
                hit_all = False
                continue
            dh = r.get("directionHit")
            sh = r.get("scoreHit")
            ok = bool(dh) if r.get("pick") and "-" not in str(r.get("pick")) else bool(sh or dh)
            legs.append({"code": code, "match": r.get("match"), "status": "✓" if ok else ("✗" if dh is not None or r.get("result") else "待回填")})
            if r.get("result") and not ok:
                hit_all = False
                breaks.append(code)
            elif not r.get("result"):
                hit_all = False
        n_settled = sum(1 for lg in legs if lg["status"] in ("✓", "✗"))
        n_break = len(breaks)
        out.append({
            "round": round_id, "plan": name, "legs": legs, "n": len(legs),
            "n_settled": n_settled, "breaks": breaks,
            "status": "全中" if (n_settled == len(legs) and n_break == 0 and legs) else
                      (f"断{n_break}关" if breaks else ("待回填" if n_settled < len(legs) else "?")),
        })
    return out


def plan_summary(plan_rows: list[dict], series: dict) -> dict:
    """方案层汇总：全中率 + 串关惩罚量化。"""
    settled = [p for p in plan_rows if p["status"] in ("全中",) or p["breaks"]]
    full_hits = sum(1 for p in settled if p["status"] == "全中")
    # 理论全中率 = 单场命中率^n（按方案平均关数）
    filled = series["filled"]
    single_rate = (sum(1 for r in filled if r.get("directionHit")) / len(filled)) if filled else 0
    avg_legs = (sum(p["n"] for p in settled) / len(settled)) if settled else 0
    theory = single_rate ** avg_legs if avg_legs else 0
    return {
        "n_plans": len(plan_rows), "n_settled": len(settled), "full_hits": full_hits,
        "actual_rate": round(full_hits / len(settled), 4) if settled else None,
        "single_rate": round(single_rate, 4), "avg_legs": round(avg_legs, 2),
        "theory_rate": round(theory, 4),
        "punishment": round((theory - (full_hits / len(settled))) if settled else 0, 4),
    }


ASSERT_MIN_N = 15  # 单断言最小样本（低于则跳过该断言）


def build_assertions(series: dict, cal: list[dict], buckets: dict) -> list[dict]:
    """回归断言 A1~A4：每条输出 结论+置信度n+建议动作。样本不足自动跳过（防小样本噪声）。"""
    filled = series["filled"]
    asserts = []

    def add(name, cond, conclusion_txt, n, action):
        asserts.append({"name": name, "triggered": bool(cond and n >= ASSERT_MIN_N),
                        "n": n, "conclusion": conclusion_txt if cond and n >= ASSERT_MIN_N else None,
                        "action": action if cond and n >= ASSERT_MIN_N else None})

    # A1 校准断言：任一桶 |obs-pred|>12pp
    for c in cal:
        if c["n"] >= 8 and c["obs"] is not None and abs(c["obs"] - c["pred"]) > 0.12:
            gap = c["obs"] - c["pred"]
            tag = "低估" if gap > 0 else "高估"
            add(f"A1校准·{c['bin']}", True,
                f"实际 {c['obs']:.0%} vs 预测 {c['pred']:.0%}（系统性{tag} {abs(gap):.0%}）",
                c["n"], "该概率档的修正系数方向检查；持续触发则重校融合权重")

    # A2 星级断言：★★★★ 实际 vs 预期 65%
    four = next((x for x in buckets["star"] if x["key"] == "★★★★"), None)
    if four and four["n"] >= ASSERT_MIN_N:
        dev = four["rate"] - 0.65
        add("A2星级·四星", abs(dev) > 0.15,
            f"四星实际 {four['rate']:.0%} vs 阈值预期 65%（偏离 {dev:+.0%}）",
            four["n"], "偏离>15pp：升星阈值该校准（偏高→收紧，偏低→放宽）")

    # A3 系数断言：chain 有/无 触发场命中率对比（chain 结构化数组或文本）
    def has_chain(r):
        ch = r.get("chain")
        return bool(ch) and str(ch) not in ("[]", "None", "")
    with_c = [r for r in filled if has_chain(r)]
    without = [r for r in filled if not has_chain(r)]
    if len(with_c) >= ASSERT_MIN_N and len(without) >= ASSERT_MIN_N:
        hw = sum(1 for r in with_c if r.get("directionHit")) / len(with_c)
        ho = sum(1 for r in without if r.get("directionHit")) / len(without)
        add("A3系数·整体", abs(hw - ho) > 0.15,
            f"系数触发场 {hw:.0%}(n={len(with_c)}) vs 未触发 {ho:.0%}(n={len(without)})",
            len(with_c) + len(without), "触发场命中率显著更低→逐系数消融（ablate.py）排查负增益项")

    # A4 模型断言：dc_used true/false 的 log loss 对比
    def dc_flag(r):
        return r.get("dc_used") or r.get("dc")
    dc_t = [r for r in filled if dc_flag(r) and r.get("p_final")]
    dc_f = [r for r in filled if not dc_flag(r) and r.get("p_final")]

    def mean_ll(recs):
        lls = []
        for r in recs:
            oi = outcome_idx(r)
            if oi is not None:
                lls.append(logloss(r["p_final"], oi))
        return (sum(lls) / len(lls)) if lls else None
    ll_t, ll_f = mean_ll(dc_t), mean_ll(dc_f)
    if ll_t and ll_f and len(dc_t) >= ASSERT_MIN_N and len(dc_f) >= ASSERT_MIN_N:
        add("A4模型·DC价值", abs(ll_t - ll_f) > 0.05,
            f"DC场 log loss {ll_t:.4f}(n={len(dc_t)}) vs 纯市场场 {ll_f:.4f}(n={len(dc_f)})",
            len(dc_t) + len(dc_f),
            "DC场显著更差→该批联赛模型重拟合或降 DC 权重；更优→可提 a（calibrate 校验）")
    return asserts


def conclusion(series: dict, cal: list[dict]) -> str:
    filled = series["filled"]
    n = len(filled)
    if n < CONCLUSION_MIN_N:
        return f"数据积累期：已回填 {n}/{CONCLUSION_MIN_N} 场，样本不足不出结论（防小样本噪声）"
    parts = []
    # 星级倒挂检查
    b = build_buckets(filled)
    star = {x["key"]: x for x in b["star"]}
    four, three = star.get("★★★★"), star.get("★★★")
    if four and three and three["rate"] > four["rate"] and three["n"] >= 10 and four["n"] >= 10:
        parts.append(f"⚠️ 星级倒挂：三星 {three['rate']:.0%} 反超四星 {four['rate']:.0%} → 星级阈值需校准")
    # 校准偏差
    for c in cal:
        if c["n"] >= 8 and c["obs"] is not None:
            gap = c["obs"] - c["pred"]
            if abs(gap) > 0.12:
                tag = "低估" if gap > 0 else "高估"
                parts.append(f"⚠️ 校准：{c['bin']} 桶实际 {c['obs']:.0%} vs 预测 {c['pred']:.0%}（系统性{tag} {abs(gap):.0%}）")
    # 模型 vs 市场
    rows = series["rounds"]
    if rows:
        last = rows[-1]
        d = round(last["cum_logloss_mkt"] - last["cum_logloss"], 4)
        if abs(d) > 0.02:
            better = "模型优于市场" if d > 0 else "市场优于模型"
            parts.append(f"累计 log loss：{better}（差 {abs(d):.4f}）")
    return "；".join(parts) if parts else f"n={n}：各项指标在正常区间，暂无触发告警规则"


# ---------- SVG 渲染（深色主题 currentColor，纯内联无外部库） ----------

def _line_chart(points: list[tuple[float, float]], ref_line: float | None, w: int = 860, h: int = 240,
                y_label: str = "", series2: list[tuple[float, float]] | None = None,
                labels: list[str] | None = None) -> str:
    """通用折线：points [(x_idx, y)]，可选参考线/第二序列。"""
    if not points:
        return f'<div class="empty">暂无数据点（待回填后成长）</div>'
    pad_l, pad_r, pad_t, pad_b = 56, 16, 18, 34
    all_vals = [p[1] for p in points] + ([p[1] for p in series2] if series2 else []) + ([ref_line] if ref_line is not None else [])
    y_min, y_max = min(all_vals), max(all_vals)
    if y_max - y_min < 1e-9:
        y_max = y_min + 0.1
    span = y_max - y_min
    y_min, y_max = y_min - span * 0.12, y_max + span * 0.12
    n = max(len(points), len(series2 or []), 1)

    def X(i):
        return pad_l + (w - pad_l - pad_r) * (i / max(n - 1, 1))

    def Y(v):
        return pad_t + (h - pad_t - pad_b) * (1 - (v - y_min) / (y_max - y_min))

    grid = []
    for g in range(5):
        v = y_min + (y_max - y_min) * g / 4
        grid.append(f'<line x1="{pad_l}" y1="{Y(v):.1f}" x2="{w - pad_r}" y2="{Y(v):.1f}" class="grid"/>'
                    f'<text x="{pad_l - 6}" y="{Y(v) + 3:.1f}" class="tick" text-anchor="end">{v:.2f}</text>')
    out = [f'<svg viewBox="0 0 {w} {h}" class="chart">'] + grid
    if ref_line is not None:
        out.append(f'<line x1="{pad_l}" y1="{Y(ref_line):.1f}" x2="{w - pad_r}" y2="{Y(ref_line):.1f}" class="ref"/>')
    polyline = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in points)
    out.append(f'<polyline points="{polyline}" class="main"/>')
    for i, v in points:
        out.append(f'<circle cx="{X(i):.1f}" cy="{Y(v):.1f}" r="3" class="pt"/>')
    if series2:
        poly2 = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in series2)
        out.append(f'<polyline points="{poly2}" class="sub"/>')
    if labels:
        for i, lb in enumerate(labels):
            out.append(f'<text x="{X(i):.1f}" y="{h - 10}" class="tick" text-anchor="middle">{lb}</text>')
    out.append(f'<text x="{pad_l}" y="12" class="ylab">{y_label}</text></svg>')
    return "".join(out)


def _cal_chart(cal: list[dict]) -> str:
    """校准图：pred vs obs 散点 + 对角线（n=0 桶不画）。"""
    pts = [(c["pred"], c["obs"]) for c in cal if c["n"] > 0 and c["pred"] is not None]
    if not pts:
        return '<div class="empty">校准图待数据（需已回填场次含 p_final）</div>'
    w, h = 420, 300
    pad = 46
    def X(v): return pad + (w - 2 * pad) * v
    def Y(v): return h - pad - (h - 2 * pad) * v
    out = [f'<svg viewBox="0 0 {w} {h}" class="chart">',
           f'<line x1="{pad}" y1="{Y(0)}" x2="{w - pad}" y2="{Y(0)}" class="grid"/>',
           f'<line x1="{pad}" y1="{Y(0.5)}" x2="{w - pad}" y2="{Y(0.5)}" class="grid"/>',
           f'<line x1="{X(0)}" y1="{Y(0)}" x2="{X(1)}" y2="{Y(1)}" class="ref"/>']
    for g in (0.0, 0.25, 0.5, 0.75, 1.0):
        out.append(f'<text x="{X(g) - 4:.0f}" y="{Y(0) + 16}" class="tick" text-anchor="middle">{g:.0%}</text>')
        out.append(f'<text x="{pad - 8}" y="{Y(g) + 3:.1f}" class="tick" text-anchor="end">{g:.0%}</text>')
    for c in cal:
        if c["n"] > 0 and c["pred"] is not None:
            out.append(f'<circle cx="{X(c["pred"]):.1f}" cy="{Y(c["obs"]):.1f}" r="5" class="calpt">'
                       f'<title>{c["bin"]} n={c["n"]} 预测{c["pred"]:.0%} 实际{c["obs"]:.0%}</title></circle>')
    out.append(f'<text x="{w / 2}" y="{h - 6}" class="tick" text-anchor="middle">预测概率 →</text>')
    out.append(f'<text x="12" y="{h / 2}" class="tick" transform="rotate(-90 12 {h / 2})" text-anchor="middle">实际胜率 →</text>')
    out.append("</svg>")
    return "".join(out)


STYLE = """
:root{--bg:#0f1420;--panel:#171e2e;--line:#2a3650;--txt:#dde4f0;--dim:#8b97ad;--acc:#4da3ff;--ok:#3ecf8e;--warn:#f5b041;--bad:#ef6b73;--purple:#a78bfa}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--txt);font:15px/1.7 -apple-system,"PingFang SC",sans-serif;padding:28px 20px 70px}
.wrap{max-width:960px;margin:0 auto}
h1{font-size:23px;margin:6px 0 2px}
h2{font-size:17px;color:var(--acc);margin:34px 0 10px;border-bottom:1px solid var(--line);padding-bottom:6px}
.sub{color:var(--dim);font-size:13px;margin-bottom:18px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px 20px;margin:12px 0}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:800px){.grid2{grid-template-columns:1fr}}
table{width:100%;border-collapse:collapse;font-size:13px;margin:6px 0}
th{color:var(--dim);text-align:left;padding:5px 8px;border-bottom:1px solid var(--line)}
td{padding:5px 8px;border-bottom:1px solid #202a40}
.chart{width:100%;height:auto}
.chart .grid{stroke:var(--line);stroke-width:.6}
.chart .tick{fill:var(--dim);font-size:10px}
.chart .ylab{fill:var(--dim);font-size:11px}
.chart .main{fill:none;stroke:var(--acc);stroke-width:2}
.chart .sub{fill:none;stroke:var(--purple);stroke-width:1.4;stroke-dasharray:5 3}
.chart .ref{stroke:var(--warn);stroke-width:1;stroke-dasharray:4 4;opacity:.7}
.chart .pt{fill:var(--acc)}
.chart .calpt{fill:var(--ok);opacity:.85}
.empty{color:var(--dim);font-size:13px;padding:24px;text-align:center;border:1px dashed var(--line);border-radius:8px}
.legend{display:flex;gap:18px;color:var(--dim);font-size:12px;margin:4px 0 8px}
.legend i{display:inline-block;width:18px;height:3px;border-radius:2px;margin-right:5px;vertical-align:3px}
.lg-main{background:var(--acc)}.lg-sub{background:var(--purple)}.lg-ref{background:var(--warn)}
.concl{background:#14231a;border-left:3px solid var(--ok);padding:10px 14px;border-radius:0 8px 8px 0;font-size:13.5px}
.note{color:var(--dim);font-size:12px}
"""


def render(series: dict, cal: list[dict], buckets: dict, concl: str, meta: dict, plans: dict, records: list) -> str:
    rows = series["rounds"]
    labels = [r["round"].replace("2026-", "") for r in rows] if rows else []
    chart1 = _line_chart([(i, r["cum_logloss"]) for i, r in enumerate(rows)] if rows else [],
                         ref_line=None, y_label="累计 log loss（低=好）",
                         series2=[(i, r["cum_logloss_mkt"]) for i, r in enumerate(rows)] if rows else None)
    chart2 = _line_chart([(i, r["cum_hit_rate"]) for i, r in enumerate(rows)] if rows else [],
                         ref_line=0.5, y_label="累计方向命中率",
                         series2=list(enumerate(series["rolling"])) if series["rolling"] else None)
    clv_by_round = []
    filled = series["filled"]
    from collections import defaultdict as dd
    acc = dd(list)
    for r in filled:
        v = clv_of(r)
        if v is not None:
            acc[r.get("round", "?")].append(v)
    rds = sorted(acc)
    clv_pts = [(i, sum(acc[rd]) / len(acc[rd])) for i, rd in enumerate(rds)]
    clv_labels = [rd.replace("2026-", "") for rd in rds]
    chart3 = _line_chart(clv_pts, ref_line=0.0, y_label="CLV 均值（DK近似口径）%", labels=clv_labels)
    chart4 = _cal_chart(cal)
    # 分桶表渲染
    def bucket_table(name: str, data: list) -> str:
        trs = "".join(f"<tr><td>{x['key']}</td><td>{x['n']}</td><td>{x['hit']}</td>"
                      f"<td>{x['rate']:.0%}</td></tr>" for x in data)
        return f'<table><tr><th>{name}</th><th>场次</th><th>命中</th><th>命中率</th></tr>{trs}</table>'

    def fmt_pct(v):
        return f"{v:.0%}" if v is not None else "—"

    cal_trs = "".join(
        f'<tr><td>{c["bin"]}</td><td>{c["n"]}</td><td>{fmt_pct(c["pred"])}</td><td>{fmt_pct(c["obs"])}</td></tr>'
        for c in cal)

    plan_rows = build_plans(plans, records)
    plan_sum = plan_summary(plan_rows, series)
    plan_trs = "".join(
        f'<tr><td>{p["round"].replace("2026-", "")}</td><td>方案{p["plan"]}</td><td>{p["n"]}串</td>'
        f'<td>{"".join(lg["status"] for lg in p["legs"])}</td><td>{p["status"]}</td>'
        f'<td>{", ".join(p["breaks"]) or "—"}</td></tr>'
        for p in plan_rows) or '<tr><td colspan="6" class="note">暂无出票方案记录</td></tr>'

    asserts = build_assertions(series, cal, buckets)
    assert_trs = "".join(
        f'<tr><td>{a["name"]}</td><td>{"⚠️ 触发" if a["triggered"] else "静默"}</td><td>{a["n"]}</td>'
        f'<td>{a["conclusion"] or "—"}</td><td>{a["action"] or "—"}</td></tr>'
        for a in asserts) or '<tr><td colspan="5" class="note">暂无断言数据</td></tr>'

    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>胜率趋势 · {meta['generatedAt']}</title><style>{STYLE}</style></head>
<body><div class="wrap">
<h1>胜率趋势分析</h1>
<div class="sub">生成 {meta['generatedAt']} · 语料 {meta['n_total']} 条 / 已回填 {meta['n_result']} / {meta['n_rounds']} 轮 · 数据源 corpus.json（纯 records 口径）</div>
<div class="concl">{concl}</div>

<h2>① 累计 log loss（评估主指标）vs 市场基线</h2>
<div class="card">
<div class="legend"><span><i class="lg-main"></i>模型累计 log loss</span><span><i class="lg-sub"></i>市场基线（p_final 纯市场锚口径）</span></div>
{chart1}
<div class="note">低=好。模型线在市场线下方 = 跑赢市场（文献基准：纯市场是最强基线）。dc_used=true 场次 p_final 含模型贡献，市场线为混合口径，解读需谨慎。</div>
</div>

<h2>② 累计方向命中率 + 滚动 {ROLLING_WINDOW} 场</h2>
<div class="card">
<div class="legend"><span><i class="lg-main"></i>累计命中率</span><span><i class="lg-sub"></i>滚动{ROLLING_WINDOW}场</span><span><i class="lg-ref"></i>50% 参考线</span></div>
{chart2}
<div class="note">累计线看长期水平；滚动线捕捉近期 regime change（如系数失效、开季噪声）。命中率仅展示口径，评估看图①。</div>
</div>

<h2>③ CLV 均值走势</h2>
<div class="card">
{chart3}
<div class="note">DK 近似口径（体彩 vs DraftKings 收盘）。体彩 12.9% 抽水 → 结构性负值正常，看趋势方向：持续下探 = 选单系统性买贵。</div>
</div>

<h2>④ 校准图（预测概率 vs 实际胜率）</h2>
<div class="grid2">
<div class="card">{chart4}</div>
<div class="card">
<table><tr><th>概率桶</th><th>n</th><th>预测均值</th><th>实际胜率</th></tr>
{cal_trs}
</table>
<div class="note">点在对角线上方=该档系统性低估自身；下方=高估。n&lt;8 的桶仅供观察。分桶简化版（CORP 保序回归待样本充足升级）。</div>
</div>
</div>

<h2>⑤ 分桶下钻（方向命中率）</h2>
<div class="grid2">
<div class="card">{bucket_table('联赛', buckets['league'])}</div>
<div class="card">{bucket_table('星级', buckets['star'])}</div>
<div class="card">{bucket_table('数据等级', buckets['grade'])}</div>
<div class="card">{bucket_table('pick 类型', buckets['pick_type'])}</div>
</div>
<div class="card">{bucket_table('入串方案', buckets['plan'])}</div>

<h2>⑥ 方案准确率（串关层：全中才算赢）</h2>
<div class="card">
<table><tr><th>轮次</th><th>方案</th><th>关数</th><th>各关状态</th><th>结果</th><th>断关</th></tr>
{plan_trs}
</table>
<div class="note">串关惩罚：实测全中率 {fmt_pct(plan_sum['actual_rate'])} vs 理论（单场 {fmt_pct(plan_sum['single_rate'])} 连乘 ×{plan_sum['avg_legs']}关 = {fmt_pct(plan_sum['theory_rate'])}）
→ 落差 {fmt_pct(plan_sum['punishment'])}（正值=修正系数乐观偏差 / 负值=保守中意外之喜）。仅统计已出票方案（旧格式 dict 方案待格式统一后纳入）。</div>
</div>

<h2>⑦ 回归断言（验证结论 → 触发提升动作）★ v4.7</h2>
<div class="card">
<table><tr><th>断言</th><th>状态</th><th>n</th><th>结论</th><th>建议动作</th></tr>
{assert_trs}
</table>
<div class="note">样本门槛 n≥{ASSERT_MIN_N}/断言（不足跳过防噪声）。触发的断言对应提升动作：A1/A4→calibrate.py 重校融合系数（自动，n≥100）；A2/A3→ablate.py 系数消融（人审 diff）。四项全静默 = 模型健康或样本不足。</div>
</div>

<div class="sub" style="margin-top:36px">sszhang pipeline · 回填赛果后自动更新 · 主指标依据 arXiv:1908.08980（log loss）/ arXiv:2008.03033（校准图）</div>
</div></body></html>"""


def main() -> None:
    if not CORPUS.exists():
        log("trend", f"缺 {CORPUS.name}（先跑 corpus.py）")
        return
    c = json.loads(CORPUS.read_text(encoding="utf-8"))
    records = c.get("records", [])
    series = build_series(records)
    cal = build_calibration(series["filled"])
    buckets = build_buckets(series["filled"])
    concl = conclusion(series, cal)
    meta = {"generatedAt": date.today().isoformat(), "n_total": c.get("n_total", 0),
            "n_result": len(series["filled"]), "n_rounds": c.get("n_rounds", 0)}
    OUT.write_text(render(series, cal, buckets, concl, meta, c.get("plans", {}), records), encoding="utf-8")
    log("trend", f"回填 {meta['n_result']} 场 / {meta['n_rounds']} 轮 → {OUT.relative_to(ROOT)}")
    log("trend", f"结论：{concl}")


if __name__ == "__main__":
    main()
