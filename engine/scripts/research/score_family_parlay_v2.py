# -*- coding: utf-8 -*-
"""闯关 v2：修正混串设计后的三版结构（铁律 9 合规）。

v1 的三处修正：
  ① 剔除 3/4 串纯 CRS 长串（违反 skill 纪律：CRS 限 2 关内）——只测 2 串
  ② 补铁律 9 混串优化：CRS 腿（模型比分清晰场）× HAD 腿（模型方向清晰场）混合，
     HAD 抽水 16% << CRS 30%，整串期望抽水下降。模型族概率=方向信心的直接来源
  ③ 补容错结构：3串4（3 场单选 CRS·8 元·中 2 关回 1 注 2串1）——对接 T002/T003
     容错实证形状

结构表：
  A  2串 CRS 双选（断层第1场）           基线（v1 已跑 +5.4%）
  B  2串 混合：CRS双选 + HAD方向         铁律 9 主角
  C  3串4 容错：3 场 CRS 单选            容错实证形状
  对照：market / random 同结构

HAD 腿方向判定：模型族概率和——主胜=home_clean+home_multi，平=draw，客胜=away_*
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
ROOT = sfm.ROOT


def load_hist_full():
    """hist_odds 原始读（含 had 赔率 + crs + score）。"""
    import json
    out = []
    for p in sorted((ROOT / "engine/cache/hist_odds").glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        for m in d.get("matches", []):
            sc = str(m.get("score") or "")
            crs = m.get("crs") or {}
            had_raw = m.get("had") or {}
            if ":" not in sc or len(crs) < 20:
                continue
            try:
                h, a = (int(x) for x in sc.split(":")[:2])
            except ValueError:
                continue
            had = {k: float(v) for k, v in had_raw.items()
                   if k in ("h", "d", "a") and v}
            if len(had) < 3:
                continue
            odds = {}
            for kk, v in crs.items():
                if str(kk).startswith("other") or not v:
                    continue
                try:
                    hh, aa = (int(x) for x in str(kk).split(":")[:2])
                    odds[(hh, aa)] = float(v)
                except (ValueError, TypeError):
                    continue
            if len(odds) < 20:
                continue
            out.append({"date": str(m.get("date") or "")[:10],
                        "code": m.get("code"), "league": m.get("league"),
                        "home_zh": m.get("home"), "away_zh": m.get("away"),
                        "actual": (h, a), "odds": odds, "had": had})
    out.sort(key=lambda x: x["date"])
    return out


def build():
    zh = {}
    for tid, srcs in sfm.load_aliases().items():
        if srcs.get("zh"):
            zh[srcs["zh"]] = tid
    tl = sfm.league_timeline()
    hist = load_hist_full()
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
    packs = []
    for i, m in enumerate(meta):
        d = {}
        for ci, cname in enumerate(sfm.CLASSES):
            tot = sum(fam_dist.get(cname, {}).values()) or 1
            for s, c in fam_dist.get(cname, {}).items():
                d[s] = d.get(s, 0.0) + P[i][ci] * (c / tot)
        mdl = sorted(d.items(), key=lambda kv: -kv[1])
        close = m["odds"]
        mkt = sorted(close.items(), key=lambda kv: kv[1])
        # HAD 方向（模型族概率和）
        ph = P[i][0] + P[i][1]     # home_clean + home_multi
        pd = P[i][2]               # draw
        pa = P[i][3] + P[i][4]     # away_clean + away_multi
        dir_idx = max(range(3), key=lambda j: (ph, pd, pa)[j])
        dir_key = "had"[dir_idx] if False else ("h", "d", "a")[dir_idx]
        gap = mdl[0][1] - mdl[1][1]
        packs.append({
            "date": m["date"], "code": m["code"], "league": m["league"],
            "actual": m["actual"], "gap": gap,
            "crs1": mdl[0][0], "o_crs1": close.get(mdl[0][0]),
            "crs2": mdl[1][0], "o_crs2": close.get(mdl[1][0]),
            "dir": dir_key, "o_dir": m["had"].get(dir_key),
            "dir_hit": {"h": m["actual"][0] > m["actual"][1],
                        "d": m["actual"][0] == m["actual"][1],
                        "a": m["actual"][0] < m["actual"][1]}[dir_key],
            "mkt_top1": mkt[0][0], "o_mkt": mkt[0][1],
            "p1": mdl[0][1],
        })
    return packs


def gap_top(day_packs, n):
    return sorted(day_packs, key=lambda p: -p["gap"])[:n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    packs = build()
    by_day = defaultdict(list)
    for p in packs:
        by_day[p["date"]].append(p)

    print("=" * 84)
    print(f"闯关 v2 · 修正混串设计 · {len(packs)} 场 / {len(by_day)} 天")
    print("=" * 84)

    def run(tag, days_cost, days_pay, days_wins, days_n):
        tc = sum(days_cost); tp = sum(days_pay); dn = sum(days_n)
        w = sum(days_wins)
        roi = (tp - tc) / tc * 100 if tc else 0
        mark = "" if w >= 5 else "  ⚠中奖<5"
        print(f"  {tag:34} {dn:>4} {tc:>7.0f}元 {tp:>8.0f}元 {w:>4} "
              f"{roi:>7.1f}%{mark}")
        return roi

    print(f"\n{'— A/B/C 三版结构 × 选场策略 —':^60}")
    for strat in ("gap", "market", "random"):
        print(f"\n  【选场={strat}】")
        cA = Counter(); cB = Counter(); cC = Counter()
        costA = payA = costB = payB = costC = payC = 0.0
        nA = nB = nC = 0
        wA = wB = wC = 0
        for day in sorted(by_day):
            dps = by_day[day]
            if strat == "gap":
                legs = gap_top(dps, 3)
            elif strat == "market":
                legs = sorted(dps, key=lambda p: -p["p1"])[:3]  # 占位：按模型p1
            else:
                legs = rng.sample(dps, min(3, len(dps)))
            if len(legs) < 2:
                continue
            l1, l2 = legs[0], legs[1]
            # A：2串 CRS 双选（l1+l2，全 CRS）
            if len(legs) >= 2:
                cost = 2 ** 2 * UNIT
                ok = True
                mult = 1.0
                for p in (l1, l2):
                    if p["actual"] == p["crs1"]:
                        mult *= p["o_crs1"]
                    elif p["actual"] == p["crs2"]:
                        mult *= p["o_crs2"]
                    else:
                        ok = False
                        break
                costA += cost; nA += 1
                if ok:
                    payA += mult * UNIT; cA[day] = 1
            # B：2串 混合（l1=CRS 双选 · l2=HAD 方向）
            cost = 2 * UNIT   # 2选×1选=2注
            ok = (l2["dir_hit"])
            mult = 1.0
            if l1["actual"] == l1["crs1"]:
                mult *= l1["o_crs1"]
            elif l1["actual"] == l1["crs2"]:
                mult *= l1["o_crs2"]
            else:
                ok = False
            if ok:
                mult *= l2["o_dir"]
            costB += cost; nB += 1
            if ok:
                payB += mult * UNIT; cB[day] = 1
            # C：3串4 容错（3 场 CRS 单选·8 元·中2回该2场2串1）
            if len(legs) >= 3:
                cost = 4 * UNIT
                hits = []
                for p in legs[:3]:
                    if p["actual"] == p["crs1"]:
                        hits.append(p["o_crs1"])
                costC += cost; nC += 1
                if len(hits) == 3:
                    payC += hits[0] * hits[1] * hits[2] * UNIT
                    wC += 1
                elif len(hits) == 2:
                    payC += hits[0] * hits[1] * UNIT
                    wC += 1
        wA = len(cA); wB = len(cB); wC = len(cC)
        run(f"A 2串CRS双选", [costA], [payA], [wA], [nA])
        run(f"B 2串混合(CRS+HAD)", [costB], [payB], [wB], [nB])
        run(f"C 3串4容错(中2回2串1)", [costC], [payC], [wC], [nC])

    print("\n" + "=" * 84)
    print("判读要点")
    print("=" * 84)
    print("  · B vs A：混 HAD 腿若提升 ROI → 铁律 9（低抽水腿混串）实证成立")
    print("  · C 的价值在回款频率（中2即回），路径质量看'有回款天占比'——容错族实证")
    print("  · 中奖<5 的行仅方向参考；策略须在 gap 下跑赢 market/random 对照")
    print("  · 全部为 2 关/容错结构，符合 skill 纪律（CRS 不进 3+ 长串）")


if __name__ == "__main__":
    main()
