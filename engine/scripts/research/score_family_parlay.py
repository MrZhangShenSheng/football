# -*- coding: utf-8 -*-
"""闯关模式模拟：模型选场 × 选比分 × 2/3/4 串，赔率相乘，真实赛果结算。

选场策略（每天从盲测场里选 N 场）：
  conf   = 模型 top1 概率最高（模型自信）
  gap    = top1 − top2 概率差最大（模型断层）
  edge   = 模型概率 − 市场隐含概率 最大（模型 vs 市场分歧最大）
  random = 随机选（对照：排除"选场"贡献）
对照策略：市场 top1 同结构（排除"串关结构本身"的贡献）。

复式：每场押模型 top1 或 top2（2 选 = 注数×2，任一命中该关即过）。

诚实纪律：
  中奖次数 <5 的行只入脚注不入结论（低概率结构在小样本无法验证）
  4 串全中理论中奖率 = c^4（c≈13%）≈ 0.03%——91 天样本期望中奖 <0.1 次，
  该行注定 0 中，列出只为展示结构，不作为证据。

开发者 sszhang
"""
from __future__ import annotations

import argparse
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import score_family_model as sfm

UNIT = 2.0


def build():
    zh = {}
    for tid, srcs in sfm.load_aliases().items():
        if srcs.get("zh"):
            zh[srcs["zh"]] = tid
    tl = sfm.league_timeline()
    hist = sfm.load_hist()
    blind = []
    for m in hist:
        if m["date"] < sfm.CUT:
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
        if kind == "L" and date < sfm.CUT and fv_h[12] >= sfm.MIN_HIST and fv_a[12] >= sfm.MIN_HIST:
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
        if r[0] == "L" and r[1] < sfm.CUT:
            fam_dist[sfm.family_of(r[4], r[5])][(r[4], r[5])] += 1
    dists = []
    for row in P:
        d = {}
        for ci, cname in enumerate(sfm.CLASSES):
            tot = sum(fam_dist.get(cname, {}).values()) or 1
            for s, c in fam_dist.get(cname, {}).items():
                d[s] = d.get(s, 0.0) + row[ci] * (c / tot)
        dists.append(d)
    # 每场信息包
    packs = []
    for i, m in enumerate(meta):
        act = m["actual"]
        close = m["odds"]
        mkt_sorted = sorted(close.items(), key=lambda kv: kv[1])
        mdl_sorted = sorted(dists[i].items(), key=lambda kv: -kv[1])
        inv = {s: 1 / o for s, o in close.items() if o}
        z = sum(inv.values()) or 1
        packs.append({
            "date": m["date"], "code": m["code"], "actual": act,
            "mkt_top1": mkt_sorted[0][0], "o_mkt": mkt_sorted[0][1],
            "p_mkt_top1": inv[mkt_sorted[0][0]] / z,
            "mdl_top1": mdl_sorted[0][0], "mdl_top2": mdl_sorted[1][0],
            "o_top1": close.get(mdl_sorted[0][0]),
            "o_top2": close.get(mdl_sorted[1][0]),
            "p1": mdl_sorted[0][1], "p2": mdl_sorted[1][1],
            "p_mkt1": inv.get(mdl_sorted[0][0], 0) / z,
            "edge": mdl_sorted[0][1] - inv.get(mdl_sorted[0][0], 0) / z,
        })
    return packs


def pick_day(day_packs, strategy, n, rng):
    """按策略从当天场次选 n 场。"""
    if len(day_packs) < n:
        return []
    if strategy == "conf":
        return sorted(day_packs, key=lambda p: -p["p1"])[:n]
    if strategy == "gap":
        return sorted(day_packs, key=lambda p: -(p["p1"] - p["p2"]))[:n]
    if strategy == "edge":
        return sorted(day_packs, key=lambda p: -p["edge"])[:n]
    if strategy == "market":
        return sorted(day_packs, key=lambda p: -p["p_mkt_top1"])[:n]
    if strategy == "random":
        return rng.sample(day_packs, n)
    return []


def settle(legs, k):
    """k 选复式串关结算：每关命中=act ∈ 该关 k 个比分。全关过才赔。"""
    bets = k ** len(legs)
    all_pass = True
    mult = 1.0
    for leg in legs:
        passed = False
        for s, o in leg:
            if leg and s == None:
                continue
        # legs[i] = [(score, odds), ...] k 项
        scores = [s for s, _ in leg]
        if leg["act"] if False else False:
            pass
    return 0.0, bets


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    rng = random.Random(a.seed)

    packs = build()
    by_day = defaultdict(list)
    for p in packs:
        by_day[p["date"]].append(p)
    print("=" * 82)
    print(f"闯关模式模拟 · {len(packs)} 场 / {len(by_day)} 天 · k=1 单选 · 赔率相乘")
    print("=" * 82)
    print(f"  {'策略':10} {'串数':>4} {'注数/天':>7} {'天数':>5} {'投入':>8} {'中奖':>5} "
          f"{'回款':>9} {'ROI':>8} {'最大单注':>9}")

    strategies = ["conf", "gap", "edge", "market", "random"]
    for n_legs in (2, 3, 4):
        for strat in strategies:
            tot_cost = tot_pay = 0.0
            days = wins = 0
            best = 0.0
            for day in sorted(by_day):
                legs = pick_day(by_day[day], strat, n_legs, rng)
                if len(legs) < n_legs:
                    continue
                days += 1
                cost = UNIT   # 每天一注 2 元（1 选 × n 串1）
                tot_cost += cost
                if all(p["actual"] == p["mdl_top1"] for p in legs):
                    mult = 1.0
                    for p in legs:
                        mult *= p["o_top1"]
                    pay = mult * UNIT
                    tot_pay += pay
                    wins += 1
                    best = max(best, pay)
            if days:
                roi = (tot_pay - tot_cost) / tot_cost * 100
                print(f"  {strat:10} {n_legs:>4} {1:>7} {days:>5} {tot_cost:>7.0f}元 "
                      f"{wins:>5} {tot_pay:>8.0f}元 {roi:>7.1f}% {best:>8.0f}元")
        print()

    # 复式版：每场押 top1+top2（2 选），2/3 串
    print("=" * 82)
    print("复式版：每场押模型 top1+top2（2 选·任一命中过关·注数×2）")
    print("=" * 82)
    print(f"  {'策略':10} {'串数':>4} {'注数/天':>7} {'天数':>5} {'投入':>8} {'中奖':>5} "
          f"{'回款':>9} {'ROI':>8} {'最大单注':>9}")
    for n_legs in (2, 3, 4):
        bets_per = 2 ** n_legs
        for strat in strategies:
            tot_cost = tot_pay = 0.0
            days = wins = 0
            best = 0.0
            for day in sorted(by_day):
                legs = pick_day(by_day[day], strat, n_legs, rng)
                if len(legs) < n_legs:
                    continue
                days += 1
                cost = bets_per * UNIT
                tot_cost += cost
                # 全关过关才派彩（复式 M 串 1）；赔率取命中那个比分的赔率
                all_pass = True
                mult = 1.0
                for p in legs:
                    if p["actual"] == p["mdl_top1"]:
                        mult *= p["o_top1"]
                    elif p["actual"] == p["mdl_top2"]:
                        mult *= p["o_top2"]
                    else:
                        all_pass = False
                        break
                if all_pass:
                    pay = mult * UNIT
                    tot_pay += pay
                    wins += 1
                    best = max(best, pay)
            if days:
                roi = (tot_pay - tot_cost) / tot_cost * 100
                mark = "" if wins >= 5 else "  ⚠中奖<5"
                print(f"  {strat:10} {n_legs:>4} {bets_per:>7} {days:>5} {tot_cost:>7.0f}元 "
                      f"{wins:>5} {tot_pay:>8.0f}元 {roi:>7.1f}% {best:>8.0f}元{mark}")
        print()

    print("=" * 82)
    print("结构提示")
    print("=" * 82)
    print("  · k=1 4串1 全中理论率 ≈ 13%^4 ≈ 0.03% → 91 天期望中奖 <0.1 次，")
    print("    该行 0 中是样本量所致，不是策略被证伪；也永远无法在小样本验证。")
    print("  · 铁律 13 复利：单关 -1.7% → 4 串理论 -6.6%。选场策略若真实有效，")
    print("    表现为高置信子集命中率 > 13.2%（单关基准）——见上方 conf/gap/edge 行。")
    print("  · 随机行 = 对照组：排除'结构/运气'贡献。策略须跑赢 random 才算数。")


if __name__ == "__main__":
    main()
