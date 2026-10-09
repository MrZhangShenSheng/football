# -*- coding: utf-8 -*-
"""v29（线二）：彩票档门槛口径 A/B 历史回测（判据预注册 prereg v29·跑前写死）。

线二设想：彩票档合格门槛的 p 由「融合 p_fused」换成「市场去水 p_mkt」。
v28 勘察定法：前向样本太薄（2 个月仅 9 条必翻腿，需 ~18 个月才够判），故改历史口径。
v28 另一发现：9 条必翻腿**全为单向**「现入→市落」——模型乐观放进了市场不认的腿，
故本改动在数学上等价于**收紧门槛**，须同报关档风险。

两臂（预注册）：
  A 臂（现行）：p_fused ≥ 0.55 入池
  B 臂（线二）：p_mkt   ≥ 0.55 入池
两臂各按 EV 降序组池（同场限一玩法·铁律9），主读数 = 腿级 stake 加权 ROI
（v27 口径：命中率必配含水隐含概率）。

判据：① B 优于 A 且配对日级 CI 下限 >0 → 建议生产换口径 ② 不过 → 维持现行
     ③ 两臂池 size / 开档日数同报 ④ 期望恒负（0.88^N）不呈报 EV ⑤ val 为准
开发者 sszhang
"""
from __future__ import annotations

import json
import math
import pickle
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from v11_s4_recalib import FIT_WINDOW, VAL_WINDOW, LEAGUES, HIST

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "data" / "04-summaries" / "v29-oddsgate-backtest.json"
CACHE_V3 = ROOT / "engine" / "cache" / "strength_chain" / "pre_days_cache_v3.pkl"
GATE = 0.55
DIRS = ("h", "d", "a")
BOOT_N = 1000
SEED = 20261009


def load_v3():
    key = f"v3|{max((f.stat().st_mtime for f in HIST.glob('crs_hist_*.json')), default=0):.0f}|{len(LEAGUES)}"
    obj = pickle.loads(CACHE_V3.read_bytes())
    assert obj.get("key") == key, "v3 缓存键过期——重跑 v25_census 重建"
    return obj["pre"]


def fuse_ab(p_model, p_mkt, a=0.4, b=1.0):
    """log-odds 意见池（与 dc_predict.fuse / calibrate.fuse_logpool 同式）。"""
    s = a + b
    a, b = a / s, b / s
    z = [a * math.log(max(p, 1e-12)) + b * math.log(max(m, 1e-12))
         for p, m in zip(p_model, p_mkt)]
    mx = max(z)
    e = [math.exp(v - mx) for v in z]
    t = sum(e)
    return [v / t for v in e]


def real_dir(real):
    r = str(real)
    if r.startswith("s1s"):
        return {"s1sh": 0, "s1sd": 1, "s1sa": 2}.get(r)
    if len(r) >= 6 and r[1:3].isdigit() and r[4:6].isdigit():
        g, t = int(r[1:3]), int(r[4:6])
        return 0 if g > t else (1 if g == t else 2)
    return None


def a_for(league):
    """联赛级 a（fusion.json leagueOverrides 的中文联赛近似映射）。
    a=0 联赛 p_fused≡p_mkt → 两臂天然同判（v28 勘察：21.7% 腿属此类）。"""
    return 0.0 if league in ("荷甲", "德甲", "意甲", "西甲", "西乙", "葡超", "法甲") else 0.4


def legs_for_day(cands):
    """逐场构造两臂候选：(p_fused, p_mkt, odds, hit)。同场取 EV 最优（铁律9）。"""
    out = []
    for c in cands:
        o = real_dir(c["real"])
        hist, model = c.get("hadHist"), c.get("hadModel") or {}
        if o is None or not hist:
            continue
        if not all(isinstance(hist.get(k), (int, float)) and hist[k] > 1.0 for k in DIRS):
            continue
        if not all(isinstance(model.get(k), (int, float)) and model[k] >= 0 for k in DIRS):
            continue
        sm = sum(model[k] for k in DIRS)
        if sm <= 0:
            continue
        o3 = [float(hist[k]) for k in DIRS]
        inv = [1.0 / x for x in o3]
        sk = sum(inv)
        p_mkt = [x / sk for x in inv]
        p_mod = [model[k] / sm for k in DIRS]
        p_f = fuse_ab(p_mod, p_mkt, a=a_for(c.get("league") or ""))
        best = None
        for k in range(3):
            ev = p_f[k] * o3[k] - 1.0
            cand = {"pf": p_f[k], "pm": p_mkt[k], "odds": o3[k], "hit": (k == o), "ev": ev,
                    "league": c.get("league")}
            if best is None or ev > best["ev"]:
                best = cand
        if best:
            out.append(best)
    return out


def arm_stats(pre, window, key):
    """→ (日级 ROI 序列 dict, 汇总)。key='pf' 或 'pm' 决定门槛用哪个 p。"""
    lo, hi = window
    by_day, flat = {}, []
    for day, cands in pre.items():
        if not (lo <= day <= hi):
            continue
        pool = [L for L in legs_for_day(cands) if L[key] >= GATE]
        if not pool:
            continue
        pool.sort(key=lambda x: -x["ev"])
        ret = sum(L["odds"] for L in pool if L["hit"])
        by_day[day] = (ret - len(pool)) / len(pool)
        flat.extend(pool)
    if not flat:
        return by_day, None
    n = len(flat)
    summary = {"nLegs": n, "nDays": len(by_day),
               "hitRate": round(sum(1 for L in flat if L["hit"]) / n, 4),
               "meanImpliedQ": round(statistics.fmean(L["pm"] for L in flat), 4),
               "roi": round((sum(L["odds"] for L in flat if L["hit"]) - n) / n, 4),
               "meanPoolSize": round(n / max(len(by_day), 1), 2)}
    return by_day, summary


def paired_ci(a_days, b_days):
    common = sorted(set(a_days) & set(b_days))
    d = [b_days[k] - a_days[k] for k in common]
    if not d:
        return None, 0, [0.0, 0.0]
    rng = random.Random(SEED)
    means = sorted(sum(d[rng.randrange(len(d))] for _ in range(len(d))) / len(d)
                   for _ in range(BOOT_N))
    return (statistics.fmean(d), len(d),
            [round(means[int(BOOT_N * 0.025)], 6),
             round(means[min(int(BOOT_N * 0.975), BOOT_N - 1)], 6)])


def main():
    print("══ v29（线二）彩票档门槛口径 A/B 历史回测 ══\n", flush=True)
    print("预注册: fade-strategy-prereg v29（跑前写死·v28 勘察定法）", flush=True)
    print("纪律: 期望恒负(0.88^N)·只报两臂相对优劣·不作 EV 呈报\n", flush=True)
    pre = load_v3()
    print(f"v3 缓存: {len(pre)} 日\n", flush=True)

    result = {"ranAt": "2026-10-09", "gate": GATE, "arms": {}, "verdict": None,
              "discipline": "期望恒负·只报相对优劣·不作 EV 呈报"}
    val_pass = None
    for seg, win in (("fit", FIT_WINDOW), ("val", VAL_WINDOW)):
        a_days, a_sum = arm_stats(pre, win, "pf")
        b_days, b_sum = arm_stats(pre, win, "pm")
        print(f"════ {seg} 段 ════", flush=True)
        if not (a_sum and b_sum):
            print("  样本不足·跳过", flush=True)
            continue
        for nm, s in (("A_现行 p_fused", a_sum), ("B_线二 p_mkt", b_sum)):
            print(f"  [{nm}] 腿 {s['nLegs']}·开档 {s['nDays']} 日·池均 {s['meanPoolSize']}"
                  f" · 命中 {s['hitRate'] * 100:.2f}%（含水隐含 {s['meanImpliedQ'] * 100:.2f}%）"
                  f" · ROI {s['roi'] * 100:+.2f}%", flush=True)
        mu, n_pair, ci = paired_ci(a_days, b_days)
        print(f"  配对 {n_pair} 日·B−A 日级 ROI 差均值 {mu * 100:+.2f}pp"
              f"·bootstrap CI [{ci[0] * 100:+.2f}, {ci[1] * 100:+.2f}]pp", flush=True)
        closed = a_sum["nDays"] - b_sum["nDays"]
        print(f"  关档风险：B 臂少开 {closed} 日（门槛收紧之代价）\n", flush=True)
        result["arms"][seg] = {"A": a_sum, "B": b_sum, "pairedDays": n_pair,
                               "meanDiff": round(mu, 6), "ci95": ci, "daysClosed": closed}
        if seg == "val":
            val_pass = ci[0] > 0

    if val_pass is None:
        result["verdict"] = "样本不足·无判决"
    elif val_pass:
        result["verdict"] = "判据①：过——建议生产彩票档门槛换市场 p 口径"
    else:
        v = result["arms"].get("val", {})
        result["verdict"] = (f"判据②：不过（val B−A {v.get('meanDiff', 0) * 100:+.2f}pp·"
                            f"CI{v.get('ci95')}）——维持现行 p_fused 门槛·归档挂账")
    print(f"══ 判定: {result['verdict']} ══", flush=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"归档 {OUT.relative_to(ROOT)}", flush=True)


if __name__ == "__main__":
    main()
