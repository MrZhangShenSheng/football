# -*- coding: utf-8 -*-
r"""右尾模型 v2：正经的设计优化（大哥 2026-09-30 批评 v1"没优化就下结论"后立项）。

v1 的四处缺陷（先自陈，后修）：
  ① 档位结构沿用偏好档案（三条带·一律2串），未做过串数×带×M 的联合优化；
  ② 选场只测了"热门腿标价四档"一个假设就宣布选场中性；
  ③ 命中基率无置信区间（桂林带 0.46% 仅 11 中、梅州带 0.13% 仅 3 中）；
  ④ 无样本外验证（铁律 14 第 2 条自己犯）。

v2 设计（判据预注册，跑前锁定）：
  目标函数：单张 2 元票 P(回款≥M)，M∈{50,100,500}。
  设计空间：串数 k∈{2,3,4} × 赔率带（9 条网格）× 腿规则=带内最热（唯一规则，防多重检验失控；
    带内取效率最高等规则列为后续未测项）。
  组票：同日按顺序把有带内腿的场次切成连续 k 场组，每组一张票（不挑场次——Part A 已示中性，
    亦防选择偏差）。
  估计与验证：按日期对半分（前半=设计选择集，后半=验证集）。设计在前半按实测 P(≥M) 择优
    （每设计票数≥300 才入围；报 top-3 而非只报 argmax——防选择偏差），在验证集上报
    实测 P(≥M) + bootstrap 95%CI，并与 v1 基线设计同场对照（配对差 CI）。
  多重比较声明：9带×3串×3M=81 组组合在前半挑最优，前半数字必然偏高，一切结论以后半为准。
  诚实框：期望仍=1−抽水^k·回收率全程同报；优化的是形状（每2元买到的中大奖概率）。

用法：python engine/scripts/research/right_tail_v2.py
开发者 sszhang
"""
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "data" / "04-summaries" / "2026-09-30-right-tail-v2.json"
SEED = 20260930
N_BOOT = 5000
UNIT = 2.0
MIN_TICKETS = 300
MS = (50, 100, 500)
KS = (2, 3, 4)
BANDS = [(4.0, 5.5), (4.5, 6.0), (4.5, 8.0), (5.0, 7.0), (6.0, 9.0),
         (8.0, 12.0), (10.0, 17.0), (12.0, 20.0), (18.0, 28.0)]
V1_BASELINE = {50: ((4.5, 8.0), 2), 100: ((10.0, 17.0), 2), 500: ((18.0, 28.0), 2)}


def load():
    """全量装载：glob 全部 crs_hist_*.json（跨年回采文件），按 (date,home,away) 去重防双计。"""
    rows, seen = [], set()
    for p in sorted((ROOT / "engine/cache/hist_odds").glob("crs_hist_*.json")):
        try:
            ms = json.loads(p.read_text(encoding="utf-8")).get("matches", [])
        except (OSError, json.JSONDecodeError):
            continue
        for m in ms:
            s = str(m.get("score") or "")
            if ":" not in s:
                continue
            try:
                h, a = (int(x) for x in s.split(":")[:2])
                pool = {}
                for k, v in (m.get("crs") or {}).items():
                    if str(k).startswith("other"):
                        continue
                    hh, aa = (int(x) for x in str(k).split(":")[:2])
                    o = float(v)
                    if o > 1.0:
                        pool[(hh, aa)] = o
            except (ValueError, TypeError):
                continue
            key = (str(m.get("date") or "")[:10], m.get("home"), m.get("away"))
            if len(pool) < 20 or key in seen:
                continue
            seen.add(key)
            rows.append({"date": key[0], "home": m.get("home"),
                         "away": m.get("away"), "sc": (h, a), "pool": pool})
    return rows


def build_tickets(rows, band, k):
    """同日连续 k 场一组，每组取各场带内最热比分，一张 k 串 1。返回逐票记录。"""
    lo, hi = band
    by_day = defaultdict(list)
    for r in rows:
        ib = [(o, sc) for sc, o in r["pool"].items() if lo <= o < hi]
        if ib:
            by_day[r["date"]].append((r, min(ib)))
    tickets = []
    for d in sorted(by_day):
        ms = by_day[d]
        for i in range(0, len(ms) - k + 1, k):
            grp = ms[i:i + k]
            prod = 1.0
            allhit = True
            for r, (o, sc) in grp:
                prod *= o
                allhit &= (sc == r["sc"])
            tickets.append({"date": d, "prod": prod,
                            "pay": UNIT * prod if allhit else 0.0,
                            "hit": bool(allhit)})
    return tickets


def p_ge(tickets, M):
    n = len(tickets)
    if not n:
        return None, 0
    c = sum(1 for t in tickets if t["pay"] >= M)
    return c / n, c


def boot_ci(tickets, M):
    pays = np.array([1.0 if t["pay"] >= M else 0.0 for t in tickets])
    n = pays.size
    if not n:
        return None, None
    rng = np.random.default_rng(SEED)
    bs = np.array([rng.choice(pays, n, True).mean() for _ in range(N_BOOT)])
    return tuple(np.percentile(bs, [2.5, 97.5]))


def main():
    rows = load()
    dates = sorted({r["date"] for r in rows})
    mid = dates[len(dates) // 2]
    train = [r for r in rows if r["date"] < mid]
    test = [r for r in rows if r["date"] >= mid]
    print(f"样本 {len(rows)} 场｜前半(设计选择) {len(train)} 场 至 {mid}｜"
          f"后半(验证) {len(test)} 场\n")

    results = {}
    for M in MS:
        print("=" * 76)
        print(f"M={M} 元档：前半选设计 → 后半验证")
        print("=" * 76)
        cand = []
        for band in BANDS:
            for k in KS:
                tt = build_tickets(train, band, k)
                p, c = p_ge(tt, M)
                if p is None or len(tt) < MIN_TICKETS:
                    continue
                rec_t, _ = p_ge(tt, 0.01)   # 全中率
                cand.append({"band": band, "k": k, "n": len(tt),
                             "pTrain": round(p, 5), "trainHits_geM": c})
        cand.sort(key=lambda x: -x["pTrain"])
        if not cand:
            print("  无设计入围（票数门槛未过）")
            continue
        print(f"  前半入围 {len(cand)} 个设计·top3（防只报 argmax）：")
        for c in cand[:3]:
            print(f"    带{c['band']} {c['k']}串  n={c['n']}  "
                  f"P(≥{M})前半={c['pTrain']:.2%} ({c['trainHits_geM']}中)")

        best = cand[0]
        base_band, base_k = V1_BASELINE[M]
        picks = {"best": {**best}, "v1": {"band": base_band, "k": base_k}}
        print(f"\n  ── 后半验证（一切结论以此为准）──")
        ver = {}
        for tag, d in picks.items():
            tt = build_tickets(test, d["band"], d["k"])
            p, c = p_ge(tt, M)
            lo, hi = boot_ci(tt, M)
            rec = sum(t["pay"] for t in tt) / (len(tt) * UNIT) if tt else None
            ver[tag] = {"band": list(d["band"]), "k": d["k"], "n": len(tt),
                        "pTest": round(p, 5) if p is not None else None,
                        "hits": c, "ci": [round(lo, 5), round(hi, 5)],
                        "recovery": round(rec, 4) if rec else None}
            print(f"    [{tag}] 带{d['band']} {d['k']}串  n={len(tt)}  "
                  f"P(≥{M})={p:.2%} ({c}中)  95%CI[{lo:.2%},{hi:.2%}]  "
                  f"回收 {rec:.1%}")
        # 配对差：best vs v1（同票数口径近似——按日对齐取交集日）
        tA = {(t["date"], i): t for i, t in enumerate(build_tickets(test, best["band"], best["k"]))}
        tB = {(t["date"], i): t for i, t in enumerate(build_tickets(test, base_band, base_k))}
        common = min(len(tA), len(tB))
        if common > 50:
            keysA = sorted(tA)[:common]
            keysB = sorted(tB)[:common]
            a = np.array([1.0 if tA[k_]["pay"] >= M else 0.0 for k_ in keysA])
            b = np.array([1.0 if tB[k_]["pay"] >= M else 0.0 for k_ in keysB])
            d = a - b
            rng = np.random.default_rng(SEED)
            bs = np.array([rng.choice(d, d.size, True).mean() for _ in range(N_BOOT)])
            lo, hi = np.percentile(bs, [2.5, 97.5])
            diff_verdict = ("best 显著优于 v1" if lo > 0 else
                            "两者分不开" if lo <= 0 <= hi else "v1 反而更好")
            print(f"    配对差 best−v1 = {d.mean():+.2%} CI[{lo:+.2%},{hi:+.2%}]"
                  f" → {diff_verdict}")
            ver["pairedDiff"] = {"d": round(float(d.mean()), 5),
                                 "ci": [round(float(lo), 5), round(float(hi), 5)],
                                 "verdict": diff_verdict}
        results[f"M{M}"] = {"top3Train": cand[:3], "validation": ver}

    OUT.write_text(json.dumps({
        "ranAt": "2026-09-30", "n": len(rows), "splitDate": mid,
        "preRegistered": "目标P(≥M)/票·设计空间9带×3串×3M·前半选后半验·"
                         "票数≥300入围·报top3·结论以后半为准",
        "results": results,
        "unconsidered": ["带内腿规则只测了'最热'一种（效率最高/目标赔率最近未测）",
                         "选场只测过热门腿标价一个假设（Part A v1）",
                         "同日多票的预算路径目标（P(预算内≥1中)）未形式化",
                         "两腿同日相关性未建模（低进球联赛平局同现可能正相关）"],
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\n→ {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
