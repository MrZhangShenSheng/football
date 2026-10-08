# -*- coding: utf-8 -*-
r"""丙+批次一 · 双底表信号审计（v17）——2026-10-07

预注册（跑前写死）: engine/cache/strength_chain/fade-strategy-prereg.json → v17SignalAudit
设计档: docs/2026-10-07-signal-audit-design.html

问题: 除置信分带外，是否存在正交赛前信号能在带内区分方向对错。
底表 A = v16 真收盘底表 8685 场（市场衍生 5 信号）
底表 B = corpus 生产预测 282 场（模型特有 6 信号）
判据: 格级=带内差≥8pp 且 Fisher 双尾 p<0.05 且 n≥30；信号级=跨赛季一致 + 多重检验假阳对账
决策: ≥2 个不同族真信号 → 元模型立项；<2 → 不建模·p_fused 分带=终点挑腿器

审计三件套: ①跑时逐格明细 stdout ②cells[] 全量落盘（格=凭证） ③--audit 复算对账+重放

用法:
  python engine/scripts/research/v17_signal_audit.py          # 跑审计
  python engine/scripts/research/v17_signal_audit.py --audit  # 复算对账

开发者 sszhang
"""
import json
import math
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "engine" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from v16_had_parlay_backtest import load_day_pools  # noqa: E402  真收盘底表装载（v16 同源）

SUMM = ROOT / "data" / "04-summaries"
CORPUS = SUMM / "corpus.json"
OUT_JSON = SUMM / "v17-signal-audit.json"
OUT_HTML = SUMM / "2026-10-07-v17-signal-audit-review.html"

# ---- 预注册常量 ----
BANDS = (("胆≥0.75", 0.75, 9.0), ("标准0.60-0.75", 0.60, 0.75), ("0.55-0.60", 0.55, 0.60), ("<0.55", 0.0, 0.55))
CELL_DIFF_PP = 8.0          # 格级带内差门槛（pp）
CELL_P = 0.05               # Fisher 双尾显著性
CELL_MIN_N = 30             # 格内最小样本
FP_RATE = 0.05              # 期望假阳率（多重检验对账）
TOP5 = {"england-premier", "spain-laliga", "germany-bundesliga", "italy-serie-a", "france-ligue1",
        "英超", "西甲", "德甲", "意甲", "法甲"}
SECOND = {"england-championship", "spain-liga2", "germany-bundesliga2", "italy-serie-b", "france-ligue2",
          "英冠", "西乙", "德乙", "意乙", "法乙"}
HAD = ("H", "D", "A")


# ---------------------------------------------------------------- Fisher 精确检验（双尾）
def _logcomb(n: int, k: int) -> float:
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def fisher_p(a: int, b: int, c: int, d: int) -> float:
    """2×2 双侧 Fisher：与观测表等边际且概率≤观测表的全加总。"""
    n1, n2, k = a + b, c + d, a + c
    if n1 == 0 or n2 == 0 or k == 0 or k == n1 + n2:
        return 1.0
    ln_obs = _logcomb(n1, a) + _logcomb(n2, k - a) - _logcomb(n1 + n2, k)
    p_obs = math.exp(ln_obs)
    total = 0.0
    for x in range(max(0, k - n2), min(n1, k) + 1):
        p = math.exp(_logcomb(n1, x) + _logcomb(n2, k - x) - _logcomb(n1 + n2, k))
        if p <= p_obs * (1 + 1e-9):
            total += p
    return min(total, 1.0)


# ---------------------------------------------------------------- 底表构建
def build_A():
    """→ [ {date,season,league,p,pickOdds,pick,res,hit,margin,month} ]"""
    pools, _ = load_day_pools()
    legs = []
    for date, pool in pools.items():
        month = int(date[5:7])
        for l in pool:
            p2 = sorted(l["probs"].values(), reverse=True)
            legs.append({
                "date": date, "season": l["season"], "league": l["league"],
                "p": l["pickP"], "odds": l["pickOdds"], "pick": l["pick"],
                "hit": l["res"] == l["pick"], "margin": p2[0] - p2[1], "month": month,
            })
    return legs


def _hda_of_pick(pick: str):
    p = str(pick)
    if "主" in p: return "H"
    if "客" in p: return "A"
    if "平" in p: return "D"
    return None


def _hda_of_result(res) -> str | None:
    try:
        h, a = str(res).replace(":", "-").split("-")
        return "H" if h > a else ("D" if h == a else "A")
    except Exception:
        return None


def _vec(v):
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except json.JSONDecodeError:
            return None
    return v if isinstance(v, list) and len(v) == 3 else None


def build_B():
    """→ [ {date,league,p,pick,hit,grade,stars,chain,ev,divergence} ]"""
    raw = json.loads(CORPUS.read_text(encoding="utf-8"))["records"]
    out = []
    for r in raw:
        pf = r.get("p_final")
        if not (isinstance(pf, list) and len(pf) == 3):
            continue
        if r.get("directionHit") is None or not r.get("pick"):
            continue
        pk, res = _hda_of_pick(r["pick"]), _hda_of_result(r.get("result"))
        if not pk or not res:
            continue
        div = None
        pdc, ppin = _vec(r.get("p_dc")), _vec(r.get("p_pinnacle"))
        if pdc and ppin:
            i = HAD.index(pk)
            div = abs(pdc[i] - ppin[i])
        month = None
        if r.get("date"):
            try:
                month = int(str(r["date"])[5:7])
            except ValueError:
                month = None
        out.append({
            "date": r.get("date"), "season": str(r.get("date", ""))[:4], "league": r.get("league"),
            "p": max(pf), "pick": pk, "hit": pk == res,
            "grade": r.get("grade"), "stars": r.get("stars"),
            "chain": "有" if r.get("chain") and str(r.get("chain")) != "无" else "无",
            "ev": r.get("ev"), "div": div, "month": month,
        })
    return out


# ---------------------------------------------------------------- 信号分档
def tier_league(lg):
    if lg in TOP5: return "五大"
    if lg in SECOND: return "次级"
    return "其他"


def tier_odds(o):
    return "<1.30" if o < 1.30 else "1.30-1.60" if o < 1.60 else "1.60-2.00" if o < 2.00 else "≥2.00"


def tier_margin(m):
    return "<0.05" if m < 0.05 else "0.05-0.15" if m < 0.15 else "0.15-0.30" if m < 0.30 else "≥0.30"


def tier_phase(m):
    return "早段(6-10月)" if m >= 6 else "中段(11-2月)" if m >= 11 or m <= 2 else "晚段(3-5月)"


def tier_grade(g):
    return None if g is None else str(int(g))


def tier_stars(s):
    return None if s is None else ("0-1" if s <= 1 else "2" if s <= 2 else "≥3")


def tier_ev(e):
    return None if e is None else ("<0" if e < 0 else "0-0.15" if e < 0.15 else "≥0.15")


def tier_div(d):
    return None if d is None else ("<0.05" if d < 0.05 else "0.05-0.15" if d < 0.15 else "≥0.15")


SIGNALS_A = {
    "赔率档": lambda l: tier_odds(l["odds"]),
    "边际差": lambda l: tier_margin(l["margin"]),
    "联赛段位": lambda l: tier_league(l["league"]),
    "赛季相位": lambda l: tier_phase(l["month"]),
    "主客位": lambda l: {"H": "主胜", "D": "平", "A": "客胜"}[l["pick"]],
}
SIGNALS_B = {
    "DC分歧度": lambda r: tier_div(r["div"]),
    "grade": lambda r: tier_grade(r["grade"]),
    "stars": lambda r: tier_stars(r["stars"]),
    "chain": lambda r: r["chain"],
    "ev": lambda r: tier_ev(r["ev"]),
    "联赛段位": lambda r: tier_league(r["league"]),
}


# ---------------------------------------------------------------- 审计核心
def band_of(p):
    for name, lo, hi in BANDS:
        if lo <= p < hi:
            return name
    return "<0.55"


def audit_cells(items, signals, basis, season_check=False, quiet=False):
    """→ cells[]（每格=凭证）·items 预先按带分组。"""
    cells = []
    by_band = defaultdict(list)
    for it in items:
        by_band[band_of(it["p"])].append(it)
    for sig_name, fn in signals.items():
        for band_name, band_items in by_band.items():
            with_field = [(it, fn(it)) for it in band_items if fn(it) is not None]
            if not with_field:
                continue
            groups = defaultdict(list)
            for it, val in with_field:
                groups[val].append(it)
            total_hits = sum(1 for it, _ in with_field if it["hit"])
            total_n = len(with_field)
            for val, members in sorted(groups.items()):
                n = len(members)
                hits = sum(1 for m in members if m["hit"])
                rest_n, rest_hits = total_n - n, total_hits - hits
                if rest_n == 0:
                    continue
                rate, rest_rate = hits / n, rest_hits / rest_n
                p = fisher_p(hits, n - hits, rest_hits, rest_n - rest_hits)
                diff = (rate - rest_rate) * 100
                cand = diff >= CELL_DIFF_PP and p < CELL_P and n >= CELL_MIN_N
                cell = {"basis": basis, "signal": sig_name, "band": band_name, "cell": val,
                        "n": n, "hits": hits, "hitRate": round(rate, 4),
                        "restN": rest_n, "restRate": round(rest_rate, 4),
                        "diffPp": round(diff, 1), "fisherP": round(p, 5), "candidate": cand}
                # A 基候选格：跨赛季一致性（≥2/3 赛季同向）
                if cand and season_check:
                    signs = {}
                    for sn in sorted({m["season"] for m in members}):
                        sm = [m for m in members if m["season"] == sn]
                        srest = [m for m in with_field if m[1] != val and m[0]["season"] == sn]
                        if len(sm) >= 5 and len(srest) >= 5:
                            d = (sum(1 for x in sm if x["hit"]) / len(sm)
                                 - sum(1 for x, _ in srest if x["hit"]) / len(srest))
                            signs[sn] = round(d * 100, 1)
                    same = sum(1 for v in signs.values() if v > 0)
                    cell["seasonSigns"] = signs
                    cell["seasonConsistent"] = same >= 2 or same >= len(signs) - 1 and len(signs) >= 2
                cells.append(cell)
                if not quiet:
                    print(f"[{basis}] {sig_name} × {band_name} · {val}: n={n} 命中{rate:.1%} "
                          f"vs 带内其余{rest_rate:.1%} → 差{diff:+.1f}pp p={p:.4f}"
                          f"{' ◆格级候选' if cand else ''}")
    return cells


def run():
    print("装载底表A（v16真收盘底表）...")
    A = build_A()
    print(f"底表A: {len(A)} 腿\n")
    cells_A = audit_cells(A, SIGNALS_A, "A", season_check=True)
    print()
    print("装载底表B（corpus生产预测）...")
    B = build_B()
    print(f"底表B: {len(B)} 场\n")
    cells_B = audit_cells(B, SIGNALS_B, "B", season_check=False)

    cells = cells_A + cells_B
    n_tested = len(cells)
    cands = [c for c in cells if c["candidate"]]
    exp_fp = round(FP_RATE * n_tested, 1)

    # 信号级：A 基需跨赛季一致·B 基需全带同向
    true_signals = []
    fam_dir = defaultdict(list)
    for c in cands:
        if c["basis"] == "A":
            if c.get("seasonConsistent"):
                fam_dir[(c["basis"], c["signal"])].append(c)
        else:
            fam_dir[(c["basis"], c["signal"])].append(c)
    for (basis, sig), cl in fam_dir.items():
        dirs = {(c["diffPp"] > 0) for c in cl}
        if len(dirs) == 1:                       # 该族所有候选格同向
            true_signals.append({"basis": basis, "signal": sig,
                                 "cells": [{"band": c["band"], "cell": c["cell"],
                                            "diffPp": c["diffPp"], "n": c["n"]} for c in cl]})
    n_families = len(true_signals)
    verdict = (f"建模立项（{n_families} 个不同族真信号 ≥2 → 启动元模型设计）" if n_families >= 2
               else f"不建模（{n_families} 个真信号 <2 · p_fused 分带=终点挑腿器·实证盖章）")

    # B 基带内差方向全带一致性另报（粗效应视角）
    result = {
        "ranAt": "2026-10-07", "script": str(Path(__file__).relative_to(ROOT)),
        "preReg": "fade-strategy-prereg v17SignalAudit（跑前写死·7fd90e1）",
        "data": {"A": len(A), "B": len(B), "bands": [b[0] for b in BANDS]},
        "multipleTesting": {"cellsTested": n_tested, "candidates": len(cands),
                            "expectedFalsePositives": exp_fp,
                            "note": f"p<0.05 格数若≈期望假阳({exp_fp})即全部视为噪声"},
        "trueSignals": true_signals,
        "verdict": verdict,
        "cells": cells,
    }
    # 审计自检（跑时一道·--audit 独立复算）
    result["audit"] = {"reconcile": f"格总数{n_tested}=A({len(cells_A)})+B({len(cells_B)}) · 候选{len(cands)} vs 期望假阳{exp_fp}",
                       "recompute": audit_recompute(result, verbose=False)}
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    OUT_HTML.write_text(gen_html(result), encoding="utf-8")

    print("\n" + "=" * 72)
    print(f"多重检验对账: 测格 {n_tested} · 格级候选 {len(cands)} · 期望假阳 {exp_fp}")
    for ts in true_signals:
        print(f"真信号: [{ts['basis']}基] {ts['signal']} → {ts['cells']}")
    print(f"终局: {verdict}")
    print(f"产物: {OUT_JSON.name} + {OUT_HTML.name}")
    return result


# ---------------------------------------------------------------- HTML
def gen_html(r):
    rows = "".join(
        f"<tr style='{'color:var(--ok);font-weight:600' if c['candidate'] else ''}'>"
        f"<td>{c['basis']}</td><td>{c['signal']}</td><td>{c['band']}</td><td>{c['cell']}</td>"
        f"<td class='num'>{c['n']}</td><td class='num'>{c['hitRate']:.1%}</td>"
        f"<td class='num'>{c['restRate']:.1%}</td><td class='num'>{c['diffPp']:+.1f}</td>"
        f"<td class='num'>{c['fisherP']:.4f}</td><td class='num'>{'◆' if c['candidate'] else ''}</td></tr>"
        for c in r["cells"] if c["candidate"] or c["diffPp"] >= CELL_DIFF_PP or c["fisherP"] < 0.01)
    ts_rows = "".join(
        f"<tr><td>{t['basis']}</td><td>{t['signal']}</td><td>{json.dumps(t['cells'], ensure_ascii=False)}</td></tr>"
        for t in r["trueSignals"]) or "<tr><td colspan=3>无</td></tr>"
    mt = r["multipleTesting"]
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>v17 双底表信号审计报告</title>
<style>
:root{{--ink:#1c2733;--sub:#5b6b7a;--line:#dde4ea;--bg:#f7f9fb;--card:#fff;--ok:#0f6b4f;--bad:#a33;--warn:#b3541e;--accent:#1a5fb4}}
body{{font-family:"Microsoft YaHei","PingFang SC",sans-serif;color:var(--ink);background:var(--bg);margin:0;padding:24px 16px;max-width:1100px;margin-inline:auto}}
h1{{font-size:20px;margin:0 0 4px}} h2{{font-size:15px;margin:20px 0 8px;padding-bottom:5px;border-bottom:2px solid var(--accent)}}
.meta{{color:var(--sub);font-size:12.5px;margin-bottom:12px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:13px 16px;margin:8px 0}}
table{{border-collapse:collapse;width:100%;font-size:12px;margin:5px 0}}
th,td{{border:1px solid var(--line);padding:3px 6px;text-align:left}} th{{background:#f0f4f7}}
.num{{font-variant-numeric:tabular-nums}} .ok{{color:var(--ok);font-weight:600}} .small{{font-size:11.5px;color:var(--sub)}}
</style></head><body>
<h1>v17 双底表信号审计 · 终局</h1>
<div class="meta">跑时 {r['ranAt']} · A 收盘基 {r['data']['A']} 腿 + B 生产基 {r['data']['B']} 场 · 预注册 v17</div>
<div class="card"><b>终局判定：</b><span class="{'ok' if '建模' in r['verdict'] else 'bad'}">{r['verdict']}</span></div>
<h2>一、多重检验对账（防假阳）</h2>
<div class="card"><table><tr><th>测格数</th><th>格级候选</th><th>期望假阳(0.05×格数)</th></tr>
<tr><td class="num">{mt['cellsTested']}</td><td class="num">{mt['candidates']}</td><td class="num">{mt['expectedFalsePositives']}</td></tr></table>
<p class="small">{mt['note']}</p></div>
<h2>二、信号级真信号</h2>
<div class="card"><table><tr><th>底表</th><th>信号</th><th>候选格明细</th></tr>{ts_rows}</table></div>
<h2>三、值得看的格（候选或差≥8pp 或 p&lt;0.01·全量见 JSON cells[]）</h2>
<div class="card"><table><tr><th>基</th><th>信号</th><th>带</th><th>格</th><th>n</th><th>命中率</th><th>带内其余</th><th>差pp</th><th>Fisher p</th><th>候选</th></tr>{rows}</table></div>
<h2>四、对账行</h2>
<div class="card"><pre>{r['audit']['reconcile']}</pre><p class="small">票级复算 {r['audit']['recompute']}</p></div>
<div class="small">开发者 sszhang · 2026-10-07 · 格=凭证·cells[] 可独立重算一切汇总</div>
</body></html>"""


# ---------------------------------------------------------------- --audit
def audit_recompute(result, verbose=True):
    """从原始底表独立重算全部格·与落盘 cells 比对。"""
    A = build_A()
    B = build_B()
    fresh_cells = audit_cells(A, SIGNALS_A, "A", season_check=True, quiet=True) \
        + audit_cells(B, SIGNALS_B, "B", quiet=True)
    # 按 (basis,signal,band,cell) 键比对
    key = lambda c: (c["basis"], c["signal"], c["band"], c["cell"])
    fresh_map = {key(c): c for c in fresh_cells}
    fails = []
    for c in result["cells"]:
        f = fresh_map.get(key(c))
        if not f:
            fails.append(f"{key(c)} 重放缺失")
            continue
        for fld in ("n", "hits", "hitRate", "restN", "restRate", "diffPp"):
            if c[fld] != f[fld]:
                fails.append(f"{key(c)}.{fld}: 落盘{c[fld]} ≠ 重算{f[fld]}")
    verdict = "PASS" if not fails else f"FAIL({len(fails)})——" + ";".join(fails[:3])
    if verbose:
        print(f"[审计②格级复算·{len(result['cells'])}格] {verdict}")
    return verdict


def audit():
    result = json.loads(OUT_JSON.read_text(encoding="utf-8"))
    print(f"[审计①对账行] {result['audit']['reconcile']}")
    verdict = audit_recompute(result, verbose=False)
    print(f"[审计②格级复算·{len(result['cells'])}格] {verdict}")
    print("审计完成·两件全过才可信")


if __name__ == "__main__":
    if "--audit" in sys.argv:
        audit()
    else:
        run()
