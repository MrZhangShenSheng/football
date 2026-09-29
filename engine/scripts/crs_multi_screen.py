# -*- coding: utf-8 -*-
"""crs_multi_screen: 预测比分 → 按预测逻辑选多个比分 → 混串 → 算中奖金额。

流程（2026-09-28 大哥指令："先去预测比分，按照预测逻辑选多个比分去做混串，然后算中奖金额"）：
  Step1 预测：freq_band.fused_legs 融合链出每场 CRS 分布（模板频率 q^r · 市场 p^(1-r)，
        r=0.286 冻结）→ 模型输出，非市场去水。
  Step2 选腿：取 top 族（home_clean/home_multi/draw/away_clean/away_multi），族内成员
        =天然多选组；族闸门 28% 关档场默认不出腿（spec §4）。
  Step3 混串：k 选 × n 场 → 注数 Π k_i，算最低/最高/期望中奖金额。
  Step4 收益：fused 模型概率算期望 ROI，并与市场去水口径并列对照——两者之差=模型偏离，
        正收益只可能来自正偏离。

口径纪律（CLAUDE.md 铁律10）：market 列是市场锚不是模型输出，不得混述。
开发者 sszhang
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import re
import sys
from itertools import combinations
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import crs_fusion
import freq_band

ROOT = Path(__file__).resolve().parents[2]
UNIT = 2.0
DEBUG = bool(__import__("os").environ.get("CMS_DEBUG"))


def _fmt(s) -> str:
    return freq_band._fmt_score(s)


def _devig(odds: dict) -> dict:
    inv = {k: 1.0 / v for k, v in odds.items() if v}
    z = sum(inv.values())
    return {k: v / z for k, v in inv.items()} if z else {}


def _pf_and_odds(m, freq_table, form, zh, pool, cfg):
    """复现 freq_band.fused_legs 内部链路，取完整 p_final 分布 + 该场 CRS 赔率。

    fused_legs 只返回 top3 与族摘要，多选需要完整分布，故按同一实现重算
    （平滑→去水→对数池），参数全部走 cfg 同源，不另设默认值。"""
    from crs_fusion import extract_mkt_dist, fuse_crs, shrink_lambda
    crs = m.get("crs") or {}
    blob, lam = freq_band._template_counts(freq_table, m, zh, form, pool)
    if lam is not None:
        e_mkt = freq_band._ttg_market_expect(m.get("ttg"))
        if e_mkt is not None:
            lam_sum, _ = shrink_lambda(lam[0] + lam[1], e_mkt, w=cfg["w"])
            scale = lam_sum / (lam[0] + lam[1])
            lam = (lam[0] * scale, lam[1] * scale)
    q_map = freq_band.shifted_q(blob, lam)
    q = freq_band._smooth_shifted(q_map, blob.get("__n", 0), cfg["alphaLidstone"])
    p_mkt = (extract_mkt_dist(crs)
             if len(crs) >= freq_band.CRS_FUSION_MIN_ITEMS else {})
    pf = fuse_crs(q, p_mkt, r=cfg["r"]) if p_mkt else q
    odds = {}
    for k, v in crs.items():
        g = re.match(r"^(\d+):(\d+)$", str(k)) or re.match(r"^s(\d{2})s(\d{2})$", str(k))
        if not g:
            continue
        try:
            odds[f"{int(g.group(1))}:{int(g.group(2))}"] = float(v)
        except (TypeError, ValueError):
            continue
    return pf, odds


def collect(day=None, family_k=3, include_gated=False):
    from score_ev import build_freq_table
    archives = sorted(glob.glob(str(ROOT / "engine/cache/score_odds/*.json")))
    if not archives:
        raise SystemExit("[crs_multi_screen] 无 score_odds 存档")
    path = (ROOT / f"engine/cache/score_odds/{day}.json") if day else Path(archives[-1])
    if not path.exists():
        raise SystemExit(f"[crs_multi_screen] 存档不存在: {path}")
    odds = json.loads(path.read_text(encoding="utf-8"))
    freq_table = build_freq_table()
    form = freq_band.build_team_form()
    zh = freq_band._zh_alias_map()
    cfg = freq_band.load_fusion_crs_safe()
    pool = freq_band.global_pool(freq_table)
    by_code = {}
    for d in odds.get("matchDays", []):
        for m in d.get("matches", []):
            by_code[m.get("matchNumStr")] = m

    print(f"[crs_multi_screen] 存档={path.name} · 概率源=fused 融合链"
          f"（r={crs_fusion.R_LOG_POOL}）\n")
    print("=" * 80)
    print("Step1-2 · 预测比分 → 按 top 族选多个比分")
    print("=" * 80)
    print(f"  {'编号':7} {'对阵':19} {'top族':11} {'族概率':>7} {'闸门':>4}  选中比分（预测概率@赔率）")

    subs, gated = {}, []
    for d in odds.get("matchDays", []):
        for r in freq_band.fused_legs(d, freq_table, form, zh):
            fams = r.get("families") or []
            if not fams:
                continue
            top = fams[0]
            gate_ok = bool(r["gate"]["pass"])
            raw = by_code.get(r["code"])
            if raw is None:
                continue
            pf, crs_odds = _pf_and_odds(raw, freq_table, form, zh, pool, cfg)
            if DEBUG:
                print(f"    [dbg] {r['code']} pf={len(pf)} odds={len(crs_odds)} "
                      f"pfkey={list(pf)[:2]} oddskey={list(crs_odds)[:3]}")
            members = crs_fusion.FAMILIES.get(top["family"], [])
            picks = []
            for mem in members:
                p = pf.get(mem)
                o = crs_odds.get(_fmt(mem))
                if p is None or not o:
                    continue
                picks.append((_fmt(mem), float(p), float(o)))
            picks.sort(key=lambda t: -t[1])
            picks = picks[:family_k]
            if not picks:
                continue
            shown = ", ".join(f"{s}({p*100:.1f}%@{o:g})" for s, p, o in picks)
            print(f"  {r['code']:7} {r['match'][:19]:19} {top['family']:11} "
                  f"{top['prob']*100:6.1f}% {'过' if gate_ok else '关档':>4}  {shown}")
            dv = _devig(crs_odds)
            entry = {
                "code": r["code"], "vs": r["match"], "family": top["family"],
                "picks": picks, "k": len(picks),
                "cover": sum(p for _, p, _ in picks),
                "mkt_cover": sum(dv.get(s, 0.0) for s, _, _ in picks),
                "ev_unit": sum(p * o for _, p, o in picks) / len(picks),
                "mkt_ev": sum(dv.get(s, 0.0) * o for s, _, o in picks) / len(picks),
                "o_min": min(o for _, _, o in picks),
                "o_max": max(o for _, _, o in picks),
            }
            if gate_ok or include_gated:
                subs[r["code"]] = entry
            else:
                gated.append(entry)
    return subs, gated


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day")
    ap.add_argument("--legs", default="2,3,4")
    ap.add_argument("--max-combo", type=int, default=6)
    ap.add_argument("--family-k", type=int, default=3)
    ap.add_argument("--include-gated", action="store_true")
    a = ap.parse_args()

    subs, gated = collect(a.day, a.family_k, a.include_gated)
    print(f"\n  入串 {len(subs)} 场 · 闸门关档 {len(gated)} 场"
          f"（{'已纳入' if a.include_gated else '已排除'}）")
    if len(subs) < 2:
        raise SystemExit("\n[crs_multi_screen] 可用场次不足 2 场，无法混串")

    print("\n" + "=" * 80)
    print("Step3 · 混串中奖金额（单注 2 元）")
    print("=" * 80)
    print("  命中率/期望=fused 模型概率 · mktROI=市场去水口径对照\n")

    names = list(subs)
    for nl in [int(x) for x in a.legs.split(",")]:
        if nl > len(names):
            continue
        print(f"  ── {nl} 串 " + "─" * 64)
        print(f"     {'组合':24} {'注数':>4} {'投入':>6} {'命中率':>7} "
              f"{'最低中奖':>9} {'最高中奖':>9} {'期望':>7} {'ROI':>7} {'mktROI':>7}")
        combos = sorted(combinations(names, nl),
                        key=lambda cc: -min(subs[c]["cover"] for c in cc))
        for cc in combos[:a.max_combo]:
            bets, cover, ev, mkt_ev, lo, hi = 1, 1.0, 1.0, 1.0, 1.0, 1.0
            for c in cc:
                s = subs[c]
                bets *= s["k"]
                cover *= s["cover"]
                ev *= s["ev_unit"]
                mkt_ev *= s["mkt_ev"]
                lo *= s["o_min"]
                hi *= s["o_max"]
            stake = bets * UNIT
            tag = ",".join(c.replace("周一", "一").replace("周二", "二") for c in cc)
            print(f"     {tag:24} {bets:>4} {stake:>5.0f}元 {cover*100:>6.2f}% "
                  f"{lo*UNIT:>8.0f}元 {hi*UNIT:>8.0f}元 {ev*stake:>6.1f}元 "
                  f"{(ev-1)*100:>+6.1f}% {(mkt_ev-1)*100:>+6.1f}%")
        print()

    print("=" * 80)
    print("Step4 · 收益判定：模型偏离逐场分解")
    print("=" * 80)
    print(f"  {'编号':7} {'对阵':19} {'族':11} {'fused':>7} {'市场':>7} {'偏离':>8} {'单场ROI':>8}")
    pos = 0
    for s in subs.values():
        d = (s["cover"] - s["mkt_cover"]) * 100
        pos += 1 if d > 0 else 0
        print(f"  {s['code']:7} {s['vs'][:19]:19} {s['family']:11} "
              f"{s['cover']*100:6.1f}% {s['mkt_cover']*100:6.1f}% {d:>+7.2f}pp "
              f"{(s['ev_unit']-1)*100:>+7.1f}%")
    print(f"\n  正偏离 {pos}/{len(subs)} 场")
    print("  偏离>0 = 模型认为该族比市场更可能 → 该场是正收益的来源")
    print("  偏离<0 = 模型不如市场看好 → 拖累期望，不该入串")
    print("\n  注：fused 概率含市场成分（r=0.286 对数池，市场权重 71.4%），")
    print("  故偏离幅度天然受限；纯模型口径需 legacy 方法或 DC 缓存联赛。")


if __name__ == "__main__":
    main()
