# -*- coding: utf-8 -*-
"""v22.1：TTG 腿级 e 曲线（混串自由度·第一块证据·秒级）。

背景：主公拍板混串自由度（HAD/CRS/TTG 任意混搭）。TTG 腿级 e 从未测过
（v20 时样本仅 1）。hist_odds 的 ttg 字段全覆盖（8档×8575场/文件）。
本脚本：全量 TTG 腿（无模型·纯 realized 口径）按赔率带 e 曲线
+ 对照 CRS/HAD 同期带（引 v20/v20.1 结论）。

预注册判据（跑前写死）：
  ① TTG 按赔率带（<3/3-5/5-10/≥10）e=命中率×均赔+bootstrap95%CI
  ② 任一带 e>1 且 CI 下限>1 且 n≥100 → TTG 腿有 α·混串池准入
  ③ 全带 e≤1 → TTG 同 CRS 为毒·混串扩展意义=仅方差优化
产出：data/04-summaries/v22_1-ttg-leg-alpha.json
开发者 sszhang
"""
from __future__ import annotations

import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT_PATH = ROOT / "data" / "04-summaries" / "v22_1-ttg-leg-alpha.json"
BANDS = ((0.0, 3.0, "<3"), (3.0, 5.0, "3-5"), (5.0, 10.0, "5-10"), (10.0, 1e9, ">=10"))
BOOT_N = 2000
SEED = 20261008


def main():
    print("══ v22.1 TTG 腿级 e 曲线 ══\n")
    legs = []
    files = sorted((ROOT / "engine/cache/hist_odds").glob("crs_hist_*.json"))
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        for m in d.get("matches", []):
            ttg = m.get("ttg")
            score = m.get("score")
            if not ttg or not score:
                continue
            try:
                h, a = str(score).split(":")
                total = int(h) + int(a)
            except (ValueError, AttributeError):
                continue
            real_key = str(min(total, 7))
            for k, ov in ttg.items():
                try:
                    o = float(ov)
                except (TypeError, ValueError):
                    continue
                if o <= 1.0:
                    continue
                legs.append({"odds": o, "hit": str(k) == real_key})
    print(f"TTG 可判腿: {len(legs)}\n")

    def e_and_ci(sub):
        if not sub:
            return None, 0, None
        n = len(sub)
        e = sum((1.0 if x["hit"] else 0.0) * x["odds"] for x in sub) / n
        rng = random.Random(SEED)
        es = sorted(sum((1.0 if sub[(k := rng.randrange(n))]["hit"] else 0.0) * sub[k]["odds"]
                        for _ in range(n)) / n for _ in range(BOOT_N))
        return e, n, (es[int(BOOT_N * 0.025)], es[int(BOOT_N * 0.975)])

    print(f"{'带':<8}{'n':<8}{'命中率':<9}{'均赔':<8}{'e':<8}{'95%CI':<22}{'判定'}")
    out = {}
    alpha = []
    for lo, hi, name in BANDS:
        sub = [x for x in legs if lo <= x["odds"] < hi]
        e, n, ci = e_and_ci(sub)
        hr = sum(1 for x in sub if x["hit"]) / n if n else 0
        avg = sum(x["odds"] for x in sub) / n if n else 0
        v = ""
        if n >= 100 and e is not None:
            if e > 1.0 and ci and ci[0] > 1.0:
                v = "★有α·混串池准入"
                alpha.append((name, e, n, ci))
            elif e > 1.0:
                v = "e>1但CI下限≤1"
            else:
                v = "e≤1·无α"
        out[name] = {"n": n, "hitRate": round(hr, 4), "avgOdds": round(avg, 2),
                     "e": round(e, 4) if e is not None else None, "ci": ci, "verdict": v}
        ci_s = f"[{ci[0]:.2f},{ci[1]:.2f}]" if ci else ("n<100不可判" if n else "-")
        print(f"{name:<8}{n:<8}{hr*100:<9.2f}{avg:<8.2f}{e:<8.3f}{ci_s:<22}{v}")

    verdict = (f"TTG 有 α（{len(alpha)}带过线·混串池准入）" if alpha else
               "TTG 全带无α——混串扩展意义仅方差优化（与 CRS 同毒或近毒）")
    print(f"\n══ 判定: {verdict} ══")
    out_j = {"ranAt": "2026-10-08", "preReg": "脚本头跑前写死",
             "nLegs": len(legs), "bands": out, "verdict": verdict}
    OUT_PATH.write_text(json.dumps(out_j, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"归档 {OUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
