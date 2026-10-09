# -*- coding: utf-8 -*-
"""v26：选场闸联赛分层验证（判据预注册于 fade-strategy-prereg v26·跑前写死）。

上游：v25 出旗 13 联赛池（两段 Bonferroni）。防循环铁则：黑名单只用 **fit 段**
旗定（fit 段 Bonferroni 过闸），val 段只做验证——用同段数据定名单再验证=用答案
验证答案。

三臂（预注册）：
  A 主臂 = fit 段 Bonferroni 过闸的 CRS 旗联赛（单侧 p<α_bonf·diff<0·n_fit≥100）
  B 敏   = fit 段双池（CRS+HAD）皆旗
  C 敏   = fit 段 CRS diff 最差 5 个（n_fit≥100）

验证：v24.4 同款生产设定——逐日 {boom≤0.05·hh≥1.5 或 None} 过闸池减黑名单 →
模型 topP 降序取 top4 → 选格 = 模型 top1（现行不动）→ 卡面命中率，与现行对比。
配对日 = 两臂同日皆出卡之日；日级 bootstrap（1000 次·种子 20261009）。

判据：① val 段配对日命中改善 ≥2pp 且 CI 下限 >0 → 建议生产选场闸加黑名单
     ② 不过 → 黑名单降为卡面警示标注（影子级）·挂账归档
     ③ 凑不齐 top4 的日数 / 排除后选场构成同报
     ④ fit 段同报作方向参照（非判据）·⑤ B/C 为敏感性非主判据

产出：data/04-summaries/v26-league-gate.json
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
from v11_s4_recalib import FIT_WINDOW, VAL_WINDOW, TOP_N, UNIT, LEAGUES, HIST

ROOT = Path(__file__).resolve().parents[3]
OUT_PATH = ROOT / "data" / "04-summaries" / "v26-league-gate.json"
CACHE_V3 = ROOT / "engine" / "cache" / "strength_chain" / "pre_days_cache_v3.pkl"
DIRS = ("h", "d", "a")
MIN_N = 100
BOOT_N = 1000
SEED = 20261009
BOOM_V2, HAD_HOT_V2 = 0.05, 1.5
IMPROVE_FLOOR = 0.02
A0 = ("意甲", "德甲", "荷甲")


def load_v3():
    key = f"v3|{max((f.stat().st_mtime for f in HIST.glob('crs_hist_*.json')), default=0):.0f}|{len(LEAGUES)}"
    obj = pickle.loads(CACHE_V3.read_bytes())
    assert obj.get("key") == key, "v3 缓存键过期——重跑 v25_census 重建"
    return obj["pre"]


def real_dir(real):
    r = str(real)
    if r.startswith("s1s"):
        return {"s1sh": 0, "s1sd": 1, "s1sa": 2}.get(r)
    if len(r) >= 6 and r[1:3].isdigit() and r[4:6].isdigit():
        g, t = int(r[1:3]), int(r[4:6])
        return 0 if g > t else (1 if g == t else 2)
    return None


def boot_ci(diffs):
    n = len(diffs)
    if n == 0:
        return [0.0, 0.0]
    rng = random.Random(SEED)
    means = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(BOOT_N))
    return [round(means[int(BOOT_N * 0.025)], 6),
            round(means[min(int(BOOT_N * 0.975), BOOT_N - 1)], 6)]


def fit_flags(pre):
    """fit 段逐联赛逐池：n / diff / 单侧 p（模型更差方向）。→ (stats, bonfAlpha)。"""
    per: dict[str, dict[str, list[float]]] = {}
    for day, cands in pre.items():
        if not (FIT_WINDOW[0] <= day <= FIT_WINDOW[1]):
            continue
        for c in cands:
            lg = c.get("league") or "未标"
            slot = per.setdefault(lg, {"crs": [], "had": []})
            cells = c.get("cells") or []
            if len(cells) >= 3:
                idx = next((i for i, x in enumerate(cells) if x["mk"] == c["real"]), None)
                if idx is not None:
                    sm = sum(max(x["p"], 0.0) for x in cells)
                    sk = sum(1.0 / x["odds"] for x in cells)
                    if sm > 0 and sk > 0:
                        lm = -math.log(max(max(cells[idx]["p"], 0.0) / sm, 1e-12))
                        lk = -math.log(max((1.0 / cells[idx]["odds"]) / sk, 1e-12))
                        slot["crs"].append(lk - lm)          # 市场−模型·<0 = 模型更差
            hist, model, o = c.get("hadHist"), c.get("hadModel") or {}, real_dir(c["real"])
            if (o is not None and hist
                    and all(isinstance(hist.get(k), (int, float)) and hist[k] > 1.0 for k in DIRS)
                    and all(isinstance(model.get(k), (int, float)) and model[k] >= 0 for k in DIRS)):
                sm, sk = sum(model[k] for k in DIRS), sum(1.0 / hist[k] for k in DIRS)
                if sm > 0 and sk > 0:
                    lm = -math.log(max(model[DIRS[o]] / sm, 1e-12))
                    lk = -math.log(max((1.0 / hist[DIRS[o]]) / sk, 1e-12))
                    slot["had"].append(lk - lm)          # 市场−模型·<0 = 模型更差

    def one_sided_p(diffs):
        """下尾 p：模型更差方向（diff 显著为负的概率）。"""
        mu = sum(diffs) / len(diffs)
        se = statistics.pstdev(diffs) / math.sqrt(len(diffs))
        if se <= 0:
            return 1.0 if mu < 0 else 0.0
        z = mu / se
        return 0.5 * (1 + math.erf(z / math.sqrt(2)))

    n_tested = sum(1 for lg in per for pool in ("crs", "had")
                   if len(per[lg][pool]) >= MIN_N)
    alpha = 0.05 / max(n_tested, 1)
    stats = {}
    for lg in per:
        row = {}
        for pool in ("crs", "had"):
            d = per[lg][pool]
            if len(d) >= MIN_N:
                row[pool] = {"n": len(d), "diff": round(sum(d) / len(d), 4),
                             "p": one_sided_p(d), "flag": (sum(d) / len(d) < 0
                                                           and one_sided_p(d) < alpha)}
            else:
                row[pool] = {"n": len(d)}
        stats[lg] = row
    return stats, alpha, n_tested


def card_days(pre, window, blacklist):
    """→ {day: 日命中率}（生产设定：过闸→去黑名单→topP top4→模型 top1 格）。"""
    lo, hi = window
    by_day = {}
    for day, cands in pre.items():
        if not (lo <= day <= hi):
            continue
        pool = []
        for c in cands:
            hh = c.get("hh")
            if not ((hh is None or hh >= HAD_HOT_V2) and c.get("boom", 1.0) <= BOOM_V2):
                continue
            if (c.get("league") or "未标") in blacklist:
                continue
            cells = c.get("cells") or []
            if len(cells) < 3:
                continue
            sm = sum(max(x["p"], 0.0) for x in cells)
            if sm <= 0:
                continue
            top = max(cells, key=lambda x: x["p"])
            pool.append({"pGate": top["p"] / sm, "hit": top["mk"] == c["real"],
                         "lg": c.get("league"), "roi": (UNIT * top["odds"] if top["mk"] == c["real"] else 0.0) - UNIT})
        if len(pool) >= TOP_N:
            sel = sorted(pool, key=lambda x: -x["pGate"])[:TOP_N]
            by_day[day] = sum(1 for x in sel if x["hit"]) / TOP_N
    return by_day


def main():
    print("══ v26 选场闸联赛分层验证 ══\n", flush=True)
    print("预注册: fade-strategy-prereg v26（黑名单只用 fit 段旗定·防循环）\n", flush=True)
    pre = load_v3()
    print(f"v3 缓存: {len(pre)} 日\n", flush=True)

    stats, alpha, n_tested = fit_flags(pre)
    crs_flag = sorted(lg for lg, r in stats.items() if r.get("crs", {}).get("flag"))
    had_flag = sorted(lg for lg, r in stats.items() if r.get("had", {}).get("flag"))
    both_flag = sorted(set(crs_flag) & set(had_flag))
    worst5 = sorted((lg for lg, r in stats.items() if r.get("crs", {}).get("n", 0) >= MIN_N),
                    key=lambda lg: stats[lg]["crs"]["diff"])[:5]   # diff 最负=模型最差
    print(f"fit 段旗（Bonferroni α={alpha:.5f}·检验数 {n_tested}）：")
    print(f"  CRS 旗: {crs_flag}")
    print(f"  HAD 旗: {had_flag}")
    print(f"  双池旗: {both_flag}")
    print(f"  最差5(CRS): {worst5}\n", flush=True)

    arms = {"A_主臂CRS旗": set(crs_flag), "B_双池旗": set(both_flag), "C_最差5": set(worst5)}
    result = {"ranAt": "2026-10-09", "preReg": "fade-strategy-prereg v26",
              "fitFlags": {"crs": crs_flag, "had": had_flag, "both": both_flag,
                           "worst5": worst5, "alphaBonferroni": round(alpha, 6),
                           "nTested": n_tested, "stats": stats},
              "arms": {}, "verdict": None}

    for seg, window in (("fit", FIT_WINDOW), ("val", VAL_WINDOW)):
        base = card_days(pre, window, set())
        print(f"════ {seg} 段 · 现行基线: {len(base)} 日出卡 ════", flush=True)
        for name, bl in arms.items():
            ex = card_days(pre, window, bl)
            paired = sorted(set(base) & set(ex))
            if not paired:
                print(f"  [{name}] 黑名单 {len(bl)} 联赛 → 出卡 {len(ex)} 日·配对日 0（池塌缩）", flush=True)
                result["arms"].setdefault(name, {})[seg] = {"days": len(ex), "pairedDays": 0}
                continue
            hb = [base[d] for d in paired]
            he = [ex[d] for d in paired]
            mb, me = sum(hb) / len(hb), sum(he) / len(he)
            ci = boot_ci([e - b for b, e in zip(hb, he)])
            print(f"  [{name}] 黑名单 {len(bl)} → 出卡 {len(ex)} 日·配对 {len(paired)} 日"
                  f" · 命中 {mb*100:.2f}% → {me*100:.2f}%（Δ{(me-mb)*100:+.2f}pp）"
                  f" · 日级CI[{ci[0]*100:+.2f},{ci[1]*100:+.2f}]pp"
                  f"{'  显著' if ci[0] > 0 else ''}", flush=True)
            result["arms"].setdefault(name, {})[seg] = {
                "blacklist": sorted(bl), "days": len(ex), "pairedDays": len(paired),
                "hitBase": round(mb, 4), "hitExcl": round(me, 4),
                "diff": round(me - mb, 4), "ci95": ci}
        print(flush=True)

    a_val = result["arms"]["A_主臂CRS旗"].get("val", {})
    passed = (a_val.get("pairedDays", 0) > 0 and a_val.get("diff", 0) >= IMPROVE_FLOOR
              and a_val.get("ci95", [0, 0])[0] > 0)
    if a_val.get("pairedDays", 0) == 0:
        verdict = "主臂池塌缩（配对日 0）——排除面过宽·此路在生产设定下不可行·黑名单降为卡面警示标注"
    elif passed:
        verdict = (f"判据①过：val 配对 {a_val['pairedDays']} 日命中改善 {a_val['diff']*100:+.2f}pp"
                   f"·CI下限 {a_val['ci95'][0]*100:+.2f}pp>0——建议生产选场闸加 CRS 旗黑名单（另立上线预注册）")
    else:
        verdict = (f"判据②：不过（val Δ{a_val.get('diff', 0)*100:+.2f}pp"
                   f"·CI{a_val.get('ci95', [0,0])}）——黑名单降为卡面警示标注·挂账归档")
    print(f"══ 判定: {verdict} ══", flush=True)
    result["verdict"] = verdict
    OUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"归档 {OUT_PATH.relative_to(ROOT)}", flush=True)


if __name__ == "__main__":
    main()
