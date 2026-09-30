# -*- coding: utf-8 -*-
r"""右尾模型 v1（大哥 2026-09-30 立题：怎么抓住右尾的比赛与比分）。

模型三件套：
  【比分端】已验证（right_tail_hitrate.py）：效率峰在热门比分端（赔率4-8·e=0.873），
    腿取带内最热比分。
  【比赛端】本脚本先检验：各场"热门比分腿"的定价是否存在截面粗糙度——
    把 4979 场按热门腿赔率分四档，看各档实测命中率。若命中率平坦而赔率差 20%+，
    则"挑热门腿标价最高的场"是真规则；若命中率随赔率同步下降（市场有效），
    则选场中性，模型重心只在比分与结构。
  【结构端】已验证：2串1、目标 M 恰好可达（50档全热门带/100+桂林带/500+梅州带）。

判据预注册（Part A）：
  ① 四档各自报 n、命中率、e=命中×赔率；
  ② 检验 e(最贵档) − e(最便宜档) 的 bootstrap 95%CI：下界>0 = 粗糙度成立（选场规则有效），
     含 0 = 市场有效（选场中性，如实报告）；
  ③ 不做任何档内再挑选（防选择偏差）。
Part B：右尾卡生成器 + 近期赛日演示结算（演示非验证）。

用法：python engine/scripts/research/right_tail_model.py
开发者 sszhang
"""
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
HIST = (ROOT / "engine" / "cache" / "hist_odds"
        / "crs_hist_2025-10-01_2026-09-28.json")
OUT = ROOT / "data" / "04-summaries" / "2026-09-30-right-tail-model.json"
SEED = 20260930
N_BOOT = 5000
UNIT = 2.0
# M 档 → (赔率带下限, 带上限, 实测命中率基准 from right_tail_hitrate)
TIERS = {
    "50元档": {"band": (4.5, 8.0), "M": 50, "baseHit": 0.0168, "avgWin": 77.4},
    "100元档": {"band": (10.0, 17.0), "M": 100, "baseHit": 0.0046, "avgWin": 220.5},
    "500元档": {"band": (18.0, 28.0), "M": 500, "baseHit": 0.0013, "avgWin": 864.0},
}


def load():
    ms = json.loads(HIST.read_text(encoding="utf-8"))["matches"]
    out = []
    for m in ms:
        s = str(m.get("score") or "")
        crs = m.get("crs") or {}
        if ":" not in s or not crs:
            continue
        try:
            h, a = (int(x) for x in s.split(":")[:2])
            pool = {}
            for k, v in crs.items():
                if str(k).startswith("other"):
                    continue
                hh, aa = (int(x) for x in str(k).split(":")[:2])
                pool[(hh, aa)] = float(v)
        except (ValueError, TypeError):
            continue
        if len(pool) >= 20 and all(x > 1.0 for x in pool.values()):
            out.append({"date": str(m.get("date") or "")[:10], "home": m.get("home"),
                        "away": m.get("away"), "sc": (h, a), "pool": pool})
    return out


def part_a(rows):
    """截面粗糙度检验：热门腿（池内最低赔率比分）赔率四档 vs 实测命中率。"""
    print("=" * 74)
    print("Part A 比赛端检验：热门比分腿的定价粗糙度（4979 场·四档）")
    print("=" * 74)
    data = []
    for r in rows:
        sc, o = min(r["pool"].items(), key=lambda kv: kv[1])
        data.append({"odds": o, "hit": sc == r["sc"], "sc": sc, "r": r})
    odds = np.array([d["odds"] for d in data])
    qs = np.percentile(odds, [25, 50, 75])
    print(f"  热门腿赔率分布：中位 {np.median(odds):.1f} · 四分位 "
          f"{qs[0]:.1f} / {qs[1]:.1f} / {qs[2]:.1f}\n")
    bins = [(odds.min() - .01, qs[0]), (qs[0], qs[1]), (qs[1], qs[2]),
            (qs[2], odds.max() + .01)]
    labels = ["Q1最便宜", "Q2", "Q3", "Q4最贵"]
    stats = []
    print(f"  {'档':<8}{'n':>6}{'热门腿赔率均值':>13}{'实测命中':>9}{'e=命中×赔率':>12}")
    for lab, (lo, hi) in zip(labels, bins):
        g = [d for d in data if lo <= d["odds"] < hi]
        hit = float(np.mean([d["hit"] for d in g]))
        mo = float(np.mean([d["odds"] for d in g]))
        e = hit * mo
        stats.append({"label": lab, "n": len(g), "meanOdds": round(mo, 2),
                      "hit": round(hit, 4), "e": round(e, 4)})
        print(f"  {lab:<8}{len(g):>6}{mo:>13.2f}{hit:>9.1%}{e:>12.3f}")

    top, bot = stats[3], stats[0]
    g_top = np.array([1.0 if d["hit"] else 0.0 for d in data
                      if bins[3][0] <= d["odds"] < bins[3][1]])
    o_top = np.array([d["odds"] for d in data if bins[3][0] <= d["odds"] < bins[3][1]])
    g_bot = np.array([1.0 if d["hit"] else 0.0 for d in data
                      if bins[0][0] <= d["odds"] < bins[0][1]])
    o_bot = np.array([d["odds"] for d in data if bins[0][0] <= d["odds"] < bins[0][1]])
    rng = np.random.default_rng(SEED)
    diffs = []
    for _ in range(N_BOOT):
        it = rng.integers(0, g_top.size, g_top.size)
        ib = rng.integers(0, g_bot.size, g_bot.size)
        et = g_top[it].mean() * o_top[it].mean()
        eb = g_bot[ib].mean() * o_bot[ib].mean()
        diffs.append(et - eb)
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    d = top["e"] - bot["e"]
    print(f"\n  e(Q4最贵) − e(Q1最便宜) = {d:+.3f}  95%CI [{lo:+.3f}, {hi:+.3f}]")
    if lo > 0:
        verdict = ("粗糙度成立：标价贵的场次命中率没有相应下降——"
                   "『挑热门腿标价最高的场』是真规则，选场有效")
    elif hi < 0:
        verdict = ("反向：贵档反而效率更低——应挑便宜档（热门腿标价最低的场）")
    else:
        verdict = ("市场有效：命中率随赔率同步升降，四档效率打平——"
                   "选场中性（挑哪场不改变 P(≥M)/票），模型重心在比分端与结构端")
    print(f"  → {verdict}")
    return {"quartiles": stats,
            "eDiffTopMinusBot": round(float(d), 4),
            "ci": [round(float(lo), 4), round(float(hi), 4)],
            "verdict": verdict}, data


def _parse_score_key(k):
    """池键 → (h, a) 元组；兼容元组与 'h:a' 字符串两种形态。"""
    if isinstance(k, (tuple, list)) and len(k) >= 2:
        try:
            return int(k[0]), int(k[1])
        except (ValueError, TypeError):
            return None
    s = str(k)
    if s.startswith("other"):
        return None
    if ":" in s:
        try:
            h, a = (int(x) for x in s.split(":")[:2])
            return h, a
        except (ValueError, TypeError):
            return None
    return None


def make_card(day_rows, tier_cfg, pick_matches_rule="any"):
    """右尾卡生成器：day_rows=当日场次(带 crs 池) → 一张 2串1 卡的两腿。"""
    lo, hi = tier_cfg["band"]
    need = tier_cfg["M"] / UNIT
    cands = []
    for r in day_rows:
        pool = r.get("pool") or r.get("crs") or {}
        in_band = []
        hot_odds = None
        for k, v in pool.items():
            try:
                o = float(v)
            except (ValueError, TypeError):
                continue
            sc = _parse_score_key(k)
            if sc is None or o <= 1.0:
                continue
            if hot_odds is None or o < hot_odds:
                hot_odds = o
            if lo <= o < hi:
                in_band.append((o, sc))
        if not in_band:
            continue
        o, sc = min(in_band)                      # 带内最热
        cands.append({"r": r, "odds": o, "score": sc, "hot": hot_odds})
    if pick_matches_rule == "highest_priced_hot":
        cands.sort(key=lambda c: -(c["hot"] or 0))
    for i in range(len(cands)):
        for j in range(i + 1, len(cands)):
            if cands[i]["odds"] * cands[j]["odds"] >= need:
                return [cands[i], cands[j]]
    return None


def part_b(rows, rule):
    print("\n" + "=" * 74)
    print(f"Part B 右尾卡生成器演示（选场规则={rule}·用最近有≥2场的赛日·演示非验证）")
    print("=" * 74)
    by_day = defaultdict(list)
    for r in rows:
        by_day[r["date"]].append(r)
    demo_days = [d for d in sorted(by_day) if len(by_day[d]) >= 2][-3:]
    out = []
    for d in demo_days:
        print(f"\n  ── {d}（{len(by_day[d])} 场在售）──")
        for tier, cfg in TIERS.items():
            card = make_card(by_day[d], cfg, pick_matches_rule=rule)
            if not card:
                print(f"    {tier}: 带内无可配腿")
                continue
            (c1, c2) = card
            prod = c1["odds"] * c2["odds"]
            hit = (c1["score"] == c1["r"]["sc"]) and (c2["score"] == c2["r"]["sc"])
            pay = UNIT * prod if hit else 0.0
            line = (f"    {tier}: {c1['r']['home']} {c1['score'][0]}:{c1['score'][1]}"
                    f"@{c1['odds']:.1f} × {c2['r']['home']} {c2['score'][0]}:{c2['score'][1]}"
                    f"@{c2['odds']:.1f}  合赔 {prod:.0f}")
            line += f"  → 实际 {'✅中 回' + str(round(pay,1)) + '元' if hit else '未中'}"
            print(line)
            out.append({"date": d, "tier": tier,
                        "legs": [{"match": f"{c['r']['home']} vs {c['r']['away']}",
                                  "score": f"{c['score'][0]}:{c['score'][1]}",
                                  "odds": c["odds"]} for c in card],
                        "prod": round(prod, 1), "hit": bool(hit),
                        "pay": round(pay, 1)})
    return out


def main():
    rows = load()
    print(f"样本 {len(rows)} 场\n")
    a, data = part_a(rows)
    rule = "any" if "中性" in a["verdict"] else "highest_priced_hot"
    b = part_b(rows, rule)
    OUT.write_text(json.dumps({
        "ranAt": "2026-09-30", "n": len(rows),
        "partA": a, "selectionRule": rule, "demo": b,
        "tiers": {k: {kk: vv for kk, vv in v.items()} for k, v in TIERS.items()},
        "modelSpec": {
            "match": ("选场中性（Part A 判定）" if rule == "any"
                      else "挑热门腿标价最高的场"),
            "score": "带内最热比分（效率峰端）",
            "structure": "2串1×1注×2元·合赔恰好≥M/2·M分档选带(50/100/500)",
            "honesty": "P(≥M)基准来自实测频率·期望仍=1−抽水²·形状优化非逃抽水",
        },
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\n→ {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
