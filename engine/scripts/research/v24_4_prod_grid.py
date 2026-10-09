# -*- coding: utf-8 -*-
"""v24.4：生产配置 2×2 上线前验证（判据预注册于 fade-strategy-prereg v24.4）。

缺口：v24.3 ②节「选格换池锚」两段显著，但那是**全池**口径（val 2744 场）；
生产只买每日 top4（val 916 腿）。拿全池结论改 top4 链路属口径外推——本实验
在生产设定下复验。

2×2 = 选场{model（现行）/pool} × 选格{model（现行）/pool a*=0.05}
基准 = 现行（model/model）。逐日过闸场取 top4。

判据（跑前写死）：
  ① 目标格（选场 model·选格 pool）日级配对 CI 下限 >0 → 可改生产选格
  ② CI 跨 0 → 维持现行（如实呈报口径不可外推）
  ③ 四格命中率 + realized ROI 全表同报（ROI 非判据）
  ④ 同报分歧腿子集（top4 内两口径选格不同之腿）命中对照

产出：data/04-summaries/v24_4-prod-grid.json
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
from v24_3_pick_policy import prep, boot_ci

ROOT = Path(__file__).resolve().parents[3]
OUT_PATH = ROOT / "data" / "04-summaries" / "v24_4-prod-grid.json"
GATE_KEY = {"model": "pGate", "pool": "poolGate"}
CELLS = ("model", "pool")


def run_cfg(by_day, sel_arm, cell_arm):
    """→ (逐日命中率列表, 总腿, 总命中, 总派彩)。"""
    dh, tot, hit, pay = [], 0, 0, 0.0
    for day, lst in sorted(by_day.items()):
        if len(lst) < TOP_N:
            continue
        sel = sorted(lst, key=lambda m: -m[GATE_KEY[sel_arm]])[:TOP_N]
        d = 0
        for m in sel:
            t = m["tops"][cell_arm]
            tot += 1
            if t["mk"] == m["real"]:
                hit += 1
                d += 1
                pay += UNIT * t["odds"]
        dh.append(d / len(sel))
    return dh, tot, hit, pay


def main():
    print("══ v24.4 生产配置 2×2 上线前验证 ══\n", flush=True)
    print("预注册: fade-strategy-prereg v24.4（跑前写死）", flush=True)
    print("纪律: 目标=准确度·ROI 诚实同报非判据\n", flush=True)
    pre = load_pre()
    result = {"ranAt": "2026-10-09", "preReg": "fade-strategy-prereg v24.4", "seg": {}}

    for seg, window in (("fit", FIT_WINDOW), ("val", VAL_WINDOW)):
        ms = [m for m in prep(pre, window) if m["passGate"]]
        by_day = {}
        for m in ms:
            by_day.setdefault(m["day"], []).append(m)
        print(f"════ {seg} 段（过闸 {len(ms)} 场 / {len(by_day)} 日）════", flush=True)
        print(f"  {'选场':<8}{'选格':<8}{'日':<6}{'腿':<7}{'命中':<10}{'ROI'}", flush=True)
        grid, dhs = {}, {}
        for sel_arm in CELLS:
            for cell_arm in CELLS:
                dh, tot, hit, pay = run_cfg(by_day, sel_arm, cell_arm)
                k = f"{sel_arm}|{cell_arm}"
                dhs[k] = dh
                grid[k] = {"days": len(dh), "legs": tot,
                           "hit": round(hit / tot, 4) if tot else None,
                           "roi": round((pay - UNIT * tot) / (UNIT * tot), 4) if tot else None}
                tag = " ←现行" if k == "model|model" else (" ←目标" if k == "model|pool" else "")
                print(f"  {sel_arm:<8}{cell_arm:<8}{len(dh):<6}{tot:<7}"
                      f"{(hit/tot*100 if tot else 0):<10.2f}"
                      f"{((pay-UNIT*tot)/(UNIT*tot)*100 if tot else 0):+.1f}{tag}", flush=True)

        base = dhs["model|model"]
        print(f"\n  日级配对 bootstrap vs 现行（model|model）:", flush=True)
        for k, dh in dhs.items():
            if k == "model|model":
                continue
            ci = boot_ci([x - y for x, y in zip(dh, base)])
            grid[k]["ciVsProd"] = ci
            flag = "显著优" if ci[0] > 0 else ("显著劣" if ci[1] < 0 else "跨0")
            print(f"    {k:<16} [{ci[0]*100:+.2f}pp, {ci[1]*100:+.2f}pp]  {flag}", flush=True)

        # ④ 分歧腿子集（top4 内 model 与 pool 选格不同之腿）
        dv_m = dv_p = dv_n = 0
        pay_m = pay_p = 0.0
        dv_pairs = []
        for day, lst in sorted(by_day.items()):
            if len(lst) < TOP_N:
                continue
            for m in sorted(lst, key=lambda x: -x["pGate"])[:TOP_N]:
                tm, tp = m["tops"]["model"], m["tops"]["pool"]
                if tm["mk"] == tp["mk"]:
                    continue
                dv_n += 1
                h_m = 1.0 if tm["mk"] == m["real"] else 0.0
                h_p = 1.0 if tp["mk"] == m["real"] else 0.0
                dv_pairs.append(h_p - h_m)
                if h_m:
                    dv_m += 1
                    pay_m += UNIT * tm["odds"]
                if h_p:
                    dv_p += 1
                    pay_p += UNIT * tp["odds"]
        dvo = {"n": dv_n}
        if dv_n:
            dvo.update({"hitModel": round(dv_m / dv_n, 4), "hitPool": round(dv_p / dv_n, 4),
                        "roiModel": round((pay_m - UNIT * dv_n) / (UNIT * dv_n), 4),
                        "roiPool": round((pay_p - UNIT * dv_n) / (UNIT * dv_n), 4)})
            dvo["ci95"] = boot_ci(dv_pairs)
            print(f"\n  ④ top4 内分歧腿: {dv_n} 腿（占 {dv_n/max(grid['model|model']['legs'],1)*100:.1f}%）"
                  f" · 模型命中 {dv_m/dv_n*100:.2f}% ｜ 池命中 {dv_p/dv_n*100:.2f}%"
                  f" → 差 {(dv_p-dv_m)/dv_n*100:+.2f}pp", flush=True)
            print(f"     腿级配对95%CI（池−模型）: [{dvo['ci95'][0]*100:+.2f}pp, {dvo['ci95'][1]*100:+.2f}pp]"
                  f"  {'显著' if dvo['ci95'][0] > 0 else '跨0'}", flush=True)
            print(f"     realized ROI: 模型 {(pay_m-UNIT*dv_n)/(UNIT*dv_n)*100:+.1f}%"
                  f" ｜ 池 {(pay_p-UNIT*dv_n)/(UNIT*dv_n)*100:+.1f}%", flush=True)
        result["seg"][seg] = {"grid": grid, "divergentLegs": dvo}
        print(flush=True)

    v = result["seg"]["val"]["grid"]
    tgt = v["model|pool"]
    prod = v["model|model"]
    ci = tgt.get("ciVsProd") or [0, 0]
    passed = ci[0] > 0
    gain = (tgt["hit"] or 0) - (prod["hit"] or 0)
    verdict = (f"判据①过：生产设定下选格改池锚显著（val {gain*100:+.2f}pp·CI"
               f"[{ci[0]*100:+.2f},{ci[1]*100:+.2f}]pp）——建议改 strength_chain_prod 选格为池锚 a*=0.05"
               if passed else
               f"判据②：生产设定下不显著（val {gain*100:+.2f}pp·CI[{ci[0]*100:+.2f},{ci[1]*100:+.2f}]pp 跨0）"
               f"——维持现行选格·v24.3 全池显著不足以改 top4 链路（口径不可外推·如实呈报）")
    print(f"══ 判定: {verdict} ══", flush=True)
    result["criteria"] = {"targetBeatsProd": passed, "gain": round(gain, 4), "ci95": ci}
    result["verdict"] = verdict
    result["discipline"] = "目标=准确度·期望锁死1−抽水·ROI同报非判据"
    OUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"归档 {OUT_PATH.relative_to(ROOT)}", flush=True)


if __name__ == "__main__":
    main()
