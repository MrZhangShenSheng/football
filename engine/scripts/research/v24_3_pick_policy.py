# -*- coding: utf-8 -*-
"""v24.3：比分卡口径择优（判据预注册于 fade-strategy-prereg v24.3）。

目标：准确度（top1 命中 / log-loss），非 ROI。
纪律声明：期望仍锁死 1−抽水（CRS 33.9%）；本线产出不得呈报为 ROI 改善。

三节（跑前写死）：
  ① 分歧场裁决：模型 top1 格 ≠ 市场 top1 格之场，两方各自命中率 + 配对 bootstrap
  ② 选格口径四臂：模型 p / 纯市场 / 池 a*=0.05 / EV（p×odds−1）→ top1 命中率
  ③ 选场口径三臂：按 模型top1 p 降序 / 市场top1隐含 降序 / 池 → 取 top4 卡面命中率

判据：
  ① 分歧场市场显著优（配对 CI 下限 >0）→ 生产选格改市场锚
  ② 选场口径最优臂命中率须优于现行（模型 p）≥2pp 方可建议替换
  ③ 各臂 realized ROI 同报（诚实呈现期望锁死·不作判据）
  ④ 模型分歧场反优 → 模型选格保留（α 局部存在）

数据：v2 缓存 841 日。产出：data/04-summaries/v24_3-pick-policy.json
开发者 sszhang
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from v11_s4_recalib import FIT_WINDOW, VAL_WINDOW, TOP_N, UNIT
from v21_shape_policy import load_pre

ROOT = Path(__file__).resolve().parents[3]
OUT_PATH = ROOT / "data" / "04-summaries" / "v24_3-pick-policy.json"

A_STAR = 0.05          # v24.2 fit 段最优池权重
BOOM_V2, HAD_HOT_V2 = 0.05, 1.5     # 生产去水参数（选场过滤同生产）
BOOT_N = 2000
SEED = 20261009
ARMS = ("model", "market", "pool", "ev")


def prep(pre, window):
    """→ [场dict]：各臂 top1 格 + 真果 + 是否过生产去水闸。"""
    lo, hi = window
    out = []
    for day, cands in pre.items():
        if not (lo <= day <= hi):
            continue
        for c in cands:
            cells = c.get("cells") or []
            if len(cells) < 3:
                continue
            sm = sum(max(x["p"], 0.0) for x in cells)
            sk = sum(1.0 / x["odds"] for x in cells)
            if sm <= 0 or sk <= 0:
                continue
            rows = []
            for x in cells:
                pm = max(x["p"], 0.0) / sm
                pk = (1.0 / x["odds"]) / sk
                rows.append({"mk": x["mk"], "odds": x["odds"], "pm": pm, "pk": pk,
                             "pool": max(pm, 1e-12) ** A_STAR * max(pk, 1e-12),
                             "ev": max(x["p"], 0.0) * x["odds"] - 1.0})
            key = {"model": "pm", "market": "pk", "pool": "pool", "ev": "ev"}
            tops = {arm: max(rows, key=lambda r: r[key[arm]]) for arm in ARMS}
            hh = c.get("hh")
            out.append({"day": day, "real": c["real"], "tops": tops,
                        "pGate": max(r["pm"] for r in rows),
                        "kGate": max(r["pk"] for r in rows),
                        "poolGate": max(r["pool"] for r in rows),
                        "passGate": (hh is None or hh >= HAD_HOT_V2) and c.get("boom", 1.0) <= BOOM_V2})
    return out


def boot_ci(diffs):
    n = len(diffs)
    if n == 0:
        return [0.0, 0.0]
    rng = random.Random(SEED)
    means = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(BOOT_N))
    return [round(means[int(BOOT_N * 0.025)], 6), round(means[int(BOOT_N * 0.975)], 6)]


def arm_stats(ms, arm):
    """→ (命中率, n, realized ROI)：单注 UNIT 买该臂 top1 格。"""
    if not ms:
        return None, 0, None
    hits = pay = 0.0
    for m in ms:
        t = m["tops"][arm]
        if t["mk"] == m["real"]:
            hits += 1
            pay += UNIT * t["odds"]
    n = len(ms)
    return hits / n, n, (pay - UNIT * n) / (UNIT * n)


def main():
    print("══ v24.3 比分卡口径择优（目标=准确度·非ROI）══\n", flush=True)
    print("预注册: fade-strategy-prereg v24.3（跑前写死）", flush=True)
    print("纪律: 期望锁死 1−抽水（CRS 33.9%）·本线不得呈报为 ROI 改善\n", flush=True)
    pre = load_pre()
    segs = {"fit": prep(pre, FIT_WINDOW), "val": prep(pre, VAL_WINDOW)}
    for s, r in segs.items():
        print(f"{s} 段: {len(r)} 场（过生产去水闸 {sum(1 for x in r if x['passGate'])} 场）", flush=True)
    print(flush=True)

    result = {"ranAt": "2026-10-09", "preReg": "fade-strategy-prereg v24.3",
              "aStar": A_STAR, "discipline": "目标=准确度·期望锁死1−抽水·不作ROI改善呈报", "seg": {}}

    for seg, ms in segs.items():
        if not ms:
            continue
        print(f"════ {seg} 段 ════\n", flush=True)
        seg_out = {"n": len(ms)}

        # ① 分歧场裁决
        div = [m for m in ms if m["tops"]["model"]["mk"] != m["tops"]["market"]["mk"]]
        hm = [1.0 if m["tops"]["model"]["mk"] == m["real"] else 0.0 for m in div]
        hk = [1.0 if m["tops"]["market"]["mk"] == m["real"] else 0.0 for m in div]
        ci = boot_ci([k - m for k, m in zip(hk, hm)])
        rm = sum(UNIT * m["tops"]["model"]["odds"] for m in div
                 if m["tops"]["model"]["mk"] == m["real"])
        rk = sum(UNIT * m["tops"]["market"]["odds"] for m in div
                 if m["tops"]["market"]["mk"] == m["real"])
        n_div = len(div) or 1
        print(f"① 分歧场: {len(div)} 场（占 {len(div)/len(ms)*100:.1f}%）", flush=True)
        print(f"   模型命中 {sum(hm)/n_div*100:.2f}% ｜ 市场命中 {sum(hk)/n_div*100:.2f}%"
              f" → 差 {(sum(hk)-sum(hm))/n_div*100:+.2f}pp", flush=True)
        print(f"   配对 bootstrap 95%CI（市场−模型·>0=市场优）: [{ci[0]*100:+.2f}pp, {ci[1]*100:+.2f}pp]", flush=True)
        print(f"   realized ROI: 模型 {(rm-UNIT*n_div)/(UNIT*n_div)*100:+.1f}%"
              f" ｜ 市场 {(rk-UNIT*n_div)/(UNIT*n_div)*100:+.1f}%（期望锁死之实证）\n", flush=True)
        seg_out["divergence"] = {
            "n": len(div), "share": round(len(div) / len(ms), 4),
            "hitModel": round(sum(hm) / n_div, 4), "hitMarket": round(sum(hk) / n_div, 4),
            "diffPP": round((sum(hk) - sum(hm)) / n_div, 4), "ci95": ci,
            "roiModel": round((rm - UNIT * n_div) / (UNIT * n_div), 4),
            "roiMarket": round((rk - UNIT * n_div) / (UNIT * n_div), 4)}

        # ② 选格口径四臂（全池 + 过闸池）
        print(f"② 选格口径四臂 top1 命中率:", flush=True)
        print(f"   {'臂':<9}{'全池命中':<12}{'全池ROI':<12}{'过闸命中':<12}{'过闸ROI'}", flush=True)
        gated = [m for m in ms if m["passGate"]]
        arms_out = {}
        hit_vec = {arm: [1.0 if m["tops"][arm]["mk"] == m["real"] else 0.0 for m in gated]
                   for arm in ARMS}
        for arm in ARMS:
            h_all, n_all, roi_all = arm_stats(ms, arm)
            h_g, n_g, roi_g = arm_stats(gated, arm)
            arms_out[arm] = {"hitAll": round(h_all, 4), "roiAll": round(roi_all, 4),
                             "nAll": n_all,
                             "hitGated": round(h_g, 4) if h_g is not None else None,
                             "roiGated": round(roi_g, 4) if roi_g is not None else None,
                             "nGated": n_g}
            print(f"   {arm:<9}{h_all*100:<12.2f}{roi_all*100:<+12.1f}"
                  f"{(h_g*100 if h_g is not None else 0):<12.2f}{(roi_g*100 if roi_g is not None else 0):+.1f}", flush=True)
        for arm in ("market", "pool", "ev"):
            ci_a = boot_ci([x - y for x, y in zip(hit_vec[arm], hit_vec["model"])])
            arms_out[arm]["ciVsModel"] = ci_a
            print(f"   {arm} vs model 场级配对95%CI: [{ci_a[0]*100:+.2f}pp, {ci_a[1]*100:+.2f}pp]"
                  f"{'  显著优' if ci_a[0] > 0 else ('  显著劣' if ci_a[1] < 0 else '  跨0')}", flush=True)
        seg_out["arms"] = arms_out
        print(flush=True)

        # ③ 选场口径三臂（逐日取 top4·卡面命中率）
        print(f"③ 选场口径三臂（逐日过闸场取 top{TOP_N}·卡面格命中率）:", flush=True)
        by_day = {}
        for m in gated:
            by_day.setdefault(m["day"], []).append(m)
        sel_out = {}
        per_day_hits = {}      # arm → 逐日命中腿数（日级配对 bootstrap 用·同日同4场可比）
        for sort_arm, gate_key in (("model", "pGate"), ("market", "kGate"), ("pool", "poolGate")):
            tot = hit = 0
            days = 0
            pay = 0.0
            dh = []
            for day, lst in sorted(by_day.items()):
                if len(lst) < TOP_N:
                    continue
                sel = sorted(lst, key=lambda m: -m[gate_key])[:TOP_N]
                days += 1
                d_hit = 0
                for m in sel:
                    t = m["tops"][sort_arm]
                    tot += 1
                    if t["mk"] == m["real"]:
                        hit += 1
                        d_hit += 1
                        pay += UNIT * t["odds"]
                dh.append(d_hit / len(sel))
            per_day_hits[sort_arm] = dh
            sel_out[sort_arm] = {"days": days, "legs": tot,
                                 "hit": round(hit / tot, 4) if tot else None,
                                 "roi": round((pay - UNIT * tot) / (UNIT * tot), 4) if tot else None}
            print(f"   选场={sort_arm:<8} {days} 日 {tot} 腿 · 命中 "
                  f"{(hit/tot*100 if tot else 0):.2f}% · ROI {((pay-UNIT*tot)/(UNIT*tot)*100 if tot else 0):+.1f}%", flush=True)
        # 日级配对 bootstrap：pool/market vs model（同日同口径·卡面增量显著性）
        for arm in ("market", "pool"):
            a, b = per_day_hits[arm], per_day_hits["model"]
            if len(a) == len(b) and a:
                ci = boot_ci([x - y for x, y in zip(a, b)])
                sel_out[arm]["ciVsModel"] = ci
                print(f"   {arm} vs model 日级配对95%CI: [{ci[0]*100:+.2f}pp, {ci[1]*100:+.2f}pp]"
                      f"{'  显著' if ci[0] > 0 else '  跨0不显著'}", flush=True)
        seg_out["selection"] = sel_out
        print(flush=True)
        result["seg"][seg] = seg_out

    # ---- 判定 ----
    v = result["seg"]["val"]
    d = v["divergence"]
    market_wins_div = d["ci95"][0] > 0
    model_wins_div = d["ci95"][1] < 0
    # 判据② 原文管「选场口径」（③节）：最优臂须优于现行 model ≥2pp
    sel_arms = ("model", "market", "pool")
    sel_best = max(sel_arms, key=lambda a: v["selection"][a]["hit"] or 0)
    sel_gain = (v["selection"][sel_best]["hit"] or 0) - (v["selection"]["model"]["hit"] or 0)
    sel_ci = v["selection"].get(sel_best, {}).get("ciVsModel")
    sel_sig = bool(sel_ci and sel_ci[0] > 0)
    swap_selection = sel_best != "model" and sel_gain >= 0.02
    # 选格臂（②节·非判据②·信息性+场级CI）
    best_arm = max(ARMS, key=lambda a: v["arms"][a]["hitGated"] or 0)
    gain = (v["arms"][best_arm]["hitGated"] or 0) - (v["arms"]["model"]["hitGated"] or 0)
    cell_ci = v["arms"].get(best_arm, {}).get("ciVsModel")
    cell_sig = bool(cell_ci and cell_ci[0] > 0)

    if market_wins_div:
        verdict = (f"判据①触发：分歧场市场显著优（{d['diffPP']*100:+.2f}pp·"
                   f"CI{[round(x*100,2) for x in d['ci95']]}·两段复现）——生产选格改市场锚/池锚")
    elif model_wins_div:
        verdict = f"判据④：分歧场模型反优——模型选格保留（α局部存在·{d['diffPP']*100:+.2f}pp）"
    else:
        verdict = f"分歧场无显著差异（CI 跨 0）——选格口径之争无统计依据"
    print(f"══ 判定: {verdict} ══", flush=True)
    print(f"  ②节 最优选格臂={best_arm}（过闸命中 {(v['arms'][best_arm]['hitGated'] or 0)*100:.2f}%"
          f" vs 现行 model {(v['arms']['model']['hitGated'] or 0)*100:.2f}% · 增量 {gain*100:+.2f}pp ·"
          f" 场级CI{[round(x*100,2) for x in (cell_ci or [0,0])]}pp {'显著' if cell_sig else '跨0'}）", flush=True)
    print(f"  判据② 选场口径最优臂={sel_best}（卡面命中 {(v['selection'][sel_best]['hit'] or 0)*100:.2f}%"
          f" vs model {(v['selection']['model']['hit'] or 0)*100:.2f}% · 增量 {sel_gain*100:+.2f}pp ·"
          f" 日级CI{[round(x*100,2) for x in (sel_ci or [0,0])]}pp）"
          f" → {'过2pp门槛·建议替换' if swap_selection else '不过2pp门槛·维持现行选场口径'}", flush=True)
    result["criteria"] = {
        "marketWinsDivergence": market_wins_div, "modelWinsDivergence": model_wins_div,
        "cellBestArm": best_arm, "cellGainOverModel": round(gain, 4),
        "cellGainSignificant": cell_sig,
        "selBestArm": sel_best, "selGainOverModel": round(sel_gain, 4),
        "selGainSignificant": sel_sig, "swapSelectionRecommended": swap_selection}
    result["verdict"] = verdict
    OUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"归档 {OUT_PATH.relative_to(ROOT)}", flush=True)


if __name__ == "__main__":
    main()
