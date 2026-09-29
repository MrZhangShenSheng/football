# -*- coding: utf-8 -*-
"""方案模型（组合优化器 v1）：选场 → 选腿 → 结构 全链路决策化。

设计=docs/2026-09-29-score-family-model-design.html 增补 §六 +
docs/2026-09-28-score-evidence-model-design.html §四（组合优化器）的实现。

链路：
  族特征模型 31 项分布（唯一概率源·独立于赔率）
  → 四层漏斗：资格 / 质量(gap断层) / 价值(EV·铁律13合法用途) / 排序
  → 结构枚举：k_i 由断层决定（自信单选/不笃定双选）· 预算内合法结构
  → 保本硬约束 R_min ≥ 1.5C · 选优 P(≥2中)×回报
  → 方案卡

验证纪律：权重/阈值只在 dev(2026-04~06) 内调；盲测(2026-07+)只最终确认一次。
基线对照：朴素版（gap 前 2 场固定双选 2串复式·盲测实测 +6.9%）。
开发者 sszhang
"""
from __future__ import annotations

import argparse
import io
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import score_family_model as sfm

UNIT = 2.0
CUT = sfm.CUT
DEV0, DEV1 = "2026-04-01", "2026-07-01"


def build_all():
    zh = {}
    for tid, srcs in sfm.load_aliases().items():
        if srcs.get("zh"):
            zh[srcs["zh"]] = tid
    tl = sfm.league_timeline()
    hist = sfm.load_hist()
    blind, dev = [], []
    for m in hist:
        hid, aid = zh.get(m["home_zh"]), zh.get(m["away_zh"])
        if not (hid and aid):
            continue
        if m["date"] >= DEV1:
            blind.append({**m, "hid": hid, "aid": aid})
        elif m["date"] >= DEV0:
            dev.append({**m, "hid": hid, "aid": aid})
    merged = [("L", d, h, a, hg, ag, None) for d, h, a, hg, ag in tl]
    merged += [("D", m["date"], m["hid"], m["aid"], m["actual"][0], m["actual"][1], i)
               for i, m in enumerate(dev)]
    merged += [("B", m["date"], m["hid"], m["aid"], m["actual"][0], m["actual"][1], i)
               for i, m in enumerate(blind)]
    merged.sort(key=lambda r: (r[1], 0 if r[0] == "L" else 1))
    stats = defaultdict(sfm.TeamStats)
    seg = {"tr": ([], []), "dev": ([], [])}
    X_bl, meta_bl, X_dev, meta_dev = [], [], [], []
    tot_g = tot_n = 0
    for r in merged:
        kind, date, h, a, hg, ag = r[0], r[1], r[2], r[3], r[4], r[5]
        lg_gf = (tot_g / tot_n) if tot_n >= 50 else 2.6
        fv_h = stats[h].vector(0, lg_gf)
        fv_a = stats[a].vector(1, lg_gf)
        ready = fv_h[12] >= sfm.MIN_HIST and fv_a[12] >= sfm.MIN_HIST
        if kind == "L" and date < DEV0 and ready:
            seg["tr"][0].append(sfm.feature_row((fv_h, fv_a)))
            seg["tr"][1].append(sfm.CLASSES.index(sfm.family_of(hg, ag)))
        elif kind == "D":
            meta_dev.append(dev[r[6]])
            X_dev.append(sfm.feature_row((fv_h, fv_a)))
        elif kind == "B":
            meta_bl.append(blind[r[6]])
            X_bl.append(sfm.feature_row((fv_h, fv_a)))
        stats[h].add(hg, ag, True)
        stats[a].add(ag, hg, False)
        tot_g += hg + ag
        tot_n += 1
    model = sfm.train_softmax(seg["tr"][0], seg["tr"][1], len(sfm.CLASSES))
    P_dev = sfm.predict_proba(model, X_dev)
    P_bl = sfm.predict_proba(model, X_bl)
    fam_dist = defaultdict(Counter)
    for r in merged:
        if r[0] == "L" and r[1] < DEV0:
            fam_dist[sfm.family_of(r[4], r[5])][(r[4], r[5])] += 1

    def to_dist(row):
        d = {}
        for ci, cname in enumerate(sfm.CLASSES):
            tot = sum(fam_dist.get(cname, {}).values()) or 1
            for s, c in fam_dist.get(cname, {}).items():
                d[s] = d.get(s, 0.0) + row[ci] * (c / tot)
        return d

    dists_dev = [to_dist(r) for r in P_dev]
    dists_bl = [to_dist(r) for r in P_bl]
    return meta_dev, dists_dev, meta_bl, dists_bl


def enrich(meta, dists):
    """每场 → 决策包：排序、断层、EV、市场对照。"""
    out = []
    for i, m in enumerate(meta):
        d = dists[i]
        close = m["odds"]
        ranked = sorted(d.items(), key=lambda kv: -kv[1])
        top1, top2 = ranked[0], ranked[1]
        o1 = close.get(top1[0])
        o2 = close.get(top2[0])
        ev1 = top1[1] * o1 - 1 if o1 else -1
        ev2 = top2[1] * o2 - 1 if o2 else -1
        out.append({
            "date": m["date"], "code": m["code"], "league": m["league"],
            "actual": m["actual"],
            "top1": top1[0], "p1": top1[1], "o1": o1, "ev1": ev1,
            "top2": top2[0], "p2": top2[1], "o2": o2, "ev2": ev2,
            "gap": top1[1] - top2[1],
            "best_ev": max(ev1, ev2),
        })
    return out


def plan_day(day_packs, theta_gap, theta_ev, max_legs=3, budget=30.0):
    """方案模型：一天 → 结构化方案（或 None）。"""
    # 四层漏斗
    elig = [p for p in day_packs if p["gap"] >= theta_gap and p["best_ev"] >= theta_ev]
    if len(elig) < 2:
        return None
    elig.sort(key=lambda p: -(p["gap"] * (1 + max(0.0, p["best_ev"]))))
    # k_i：断层大单选、小双选
    for p in elig:
        p["k"] = 1 if p["gap"] >= 0.045 else 2
        p["legs"] = ([(p["top1"], p["o1"])] if p["k"] == 1
                     else [(p["top1"], p["o1"]), (p["top2"], p["o2"])])
    # 结构枚举（预算内）
    cands = []
    for n_legs in (2, 3):
        if len(elig) < n_legs:
            continue
        legs = elig[:n_legs]
        bets = 1
        for p in legs:
            bets *= p["k"]
        cost = bets * UNIT
        if cost > budget:
            continue
        o_min = 1.0
        for p in legs:
            o_min *= min(o for _, o in p["legs"])
        r_min = o_min * UNIT
        if r_min < 1.5 * cost:
            continue     # 保本硬约束
        # P(≥2中)：至少两关命中的概率（独立近似）
        ps = []
        for p in legs:
            ps.append(sum(sf_p for sf_p in [p["p1"], p["p2"]][:p["k"]]))
        from itertools import combinations
        p_ge2 = 0.0
        for combo in combinations(range(n_legs), 2):
            pr = 1.0
            for j in range(n_legs):
                pr *= ps[j] if j in combo else (1 - ps[j])
            p_ge2 += pr
        if n_legs == 2:
            p_ge2 = ps[0] * ps[1]   # 2串须全中
        cands.append({
            "legs": legs, "bets": bets, "cost": cost,
            "r_min": r_min, "roi_min": (r_min - cost) / cost,
            "p_ge2": p_ge2, "n_legs": n_legs,
        })
    if not cands:
        return None
    cands.sort(key=lambda c: -(c["p_ge2"] * c["roi_min"]))
    return cands[0]


def eval_plan(days, plan_fn):
    tot_c = tot_p = 0.0
    plan_days = 0
    wins = 0
    for day in sorted(days):
        plan = plan_fn(days[day])
        if not plan:
            continue
        plan_days += 1
        tot_c += plan["cost"]
        # 结算：全关过关才派彩（M串1）；每关命中=实开 ∈ 该关 legs
        all_pass = True
        mult = 1.0
        for p in plan["legs"]:
            scores = {s: o for s, o in p["legs"]}
            if p["actual"] in scores:
                mult *= scores[p["actual"]]
            else:
                all_pass = False
                break
        if all_pass:
            tot_p += mult * UNIT
            wins += 1
    return {"days": plan_days, "cost": tot_c, "pay": tot_p,
            "roi": (tot_p - tot_c) / tot_c * 100 if tot_c else 0, "wins": wins}


def naive_plan(day_packs):
    """朴素基线：gap 前 2 场固定双选 2串复式（盲测 +6.9% 那个）。"""
    if len(day_packs) < 2:
        return None
    legs = sorted(day_packs, key=lambda p: -p["gap"])[:2]
    for p in legs:
        p["k"] = 2
        p["legs"] = [(p["top1"], p["o1"]), (p["top2"], p["o2"])]
    cost = 4 * UNIT
    o_min = (min(p["o1"], p["o2"]) for p in legs)
    om = 1.0
    for p in legs:
        om *= min(p["o1"], p["o2"])
    return {"legs": legs, "bets": 4, "cost": cost, "r_min": om * UNIT,
            "roi_min": (om * UNIT - cost) / cost, "p_ge2": 0, "n_legs": 2}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--theta-gap", type=float, default=0.02)
    ap.add_argument("--theta-ev", type=float, default=-0.20)
    a = ap.parse_args()

    meta_dev, dists_dev, meta_bl, dists_bl = build_all()
    dev_packs = enrich(meta_dev, dists_dev)
    bl_packs = enrich(meta_bl, dists_bl)
    by_dev = defaultdict(list)
    for p in dev_packs:
        by_dev[p["date"]].append(p)
    by_bl = defaultdict(list)
    for p in bl_packs:
        by_bl[p["date"]].append(p)

    print("=" * 80)
    print("方案模型（组合优化器 v1）· dev 调参 → 盲测一次")
    print("=" * 80)
    print(f"  dev {len(dev_packs)} 场/{len(by_dev)} 天 · 盲测 {len(bl_packs)} 场/{len(by_bl)} 天")
    print(f"  参数：θ_gap={a.theta_gap} · θ_ev={a.theta_ev} · 预算 30 元 · R_min≥1.5C\n")

    # ---- dev 调参：方案模型 vs 朴素基线 ----
    print("— Dev 内部验证（选优·不碰盲测）—")
    base = eval_plan(by_dev, naive_plan)
    print(f"  朴素基线（gap前2固定双选）: {base['days']} 天 · 投入 {base['cost']:.0f} · "
          f"回款 {base['pay']:.0f} · ROI {base['roi']:+.1f}% · 中奖 {base['wins']}")
    best = None
    print("  （全参数表·诊断用）")
    for tg in (0.015, 0.02, 0.025, 0.03, 0.04):
        for te in (-0.30, -0.20, -0.10):
            r = eval_plan(by_dev, lambda dp: plan_day(dp, tg, te))
            print(f"  θ_gap={tg:.3f} θ_ev={te:+.2f}: {r['days']:>3} 天 · "
                  f"投入 {r['cost']:>5.0f} · 回款 {r['pay']:>6.0f} · "
                  f"ROI {r['roi']:+6.1f}% · 中奖 {r['wins']}")
            if r["days"] < 10 or r["wins"] < 3:
                continue
            if best is None or r["roi"] > best[0]["roi"]:
                best = (r, tg, te)
    if not best:
        print("  dev 上无合格参数组合（中奖<3 或天数<10）→ 方案模型不通过 dev，止步")
        return
    r, tg, te = best
    print(f"\n  dev 最优：θ_gap={tg} θ_ev={te} · ROI {r['roi']:+.1f}% "
          f"vs 基线 {base['roi']:+.1f}% → {'方案模型胜' if r['roi'] > base['roi'] else '朴素基线胜'}\n")

    # ---- 盲测最终确认（一次）----
    print("— 盲测最终确认（第 4 次使用·带多重比较折扣）—")
    use_plan = (lambda dp: plan_day(dp, tg, te)) if r["roi"] > base["roi"] else naive_plan
    tag = "方案模型" if r["roi"] > base["roi"] else "朴素基线"
    rb = eval_plan(by_bl, use_plan)
    mb = eval_plan(by_bl, naive_plan)
    print(f"  {tag:10} {rb['days']} 天 · 投入 {rb['cost']:.0f}元 · 回款 {rb['pay']:.0f}元 · "
          f"ROI {rb['roi']:+.1f}% · 中奖 {rb['wins']}")
    print(f"  {'朴素基线':10} {mb['days']} 天 · 投入 {mb['cost']:.0f}元 · 回款 {mb['pay']:.0f}元 · "
          f"ROI {mb['roi']:+.1f}% · 中奖 {mb['wins']}")
    print(f"  市场天花板（单关）: -16.8% · 抽水线")
    print(f"\n  注：盲测第 4 次使用——任何正 ROI 均须打多重比较折扣；")
    print(f"  真伪判定交影子层 ≥300 场自然样本。")


if __name__ == "__main__":
    main()
