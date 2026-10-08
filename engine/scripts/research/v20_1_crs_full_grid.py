# -*- coding: utf-8 -*-
"""v20.1：CRS 全格口径 e 曲线——分辩"选择偏差 vs 模型 CRS 概率高估"。

背景：v20 腿α鉴定发现 CRS≥5 选中腿 e=0.203（60腿）。两假说待分：
  H1 选择偏差：模型矩阵整体格质量尚可，但 topP 挑格动作专挑定价错误格
  H2 模型高估：模型 CRS 概率相对体彩赔率全面高估（高赔格尤甚）
数据：S5 preload 框架（模型矩阵 39 格 × 体彩 crs 赔率 × 真果·全格口径）。

预注册判据（跑前写死）：
  ① 全选项口径：全部 odds>1 的格（约每日 10-20 格×841 日）按赔率带 e 曲线
  ② 选择口径：同数据下 topP 每场选 1 格的 e（=v20 CRS 口径复算·同代码同源）
  ③ 判读：全选项 e 显著高于选择口径 → H1 选择偏差实锤（R1 改挑格方式）；
     全选项 e 同样 ≤0.3 → H2 模型高估实锤（R1 维持禁高赔比分格）
  ④ 附：模型概率分桶校准表（p_model vs realized 命中率）
产出：data/04-summaries/v20_1-crs-full-grid.json
开发者 sszhang
"""
from __future__ import annotations

import json
import pickle
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import strength_loaders as sl
from v11_s4_recalib import FIT_WINDOW, LEAGUES, HIST
from v11_s5_recalib import preload_days

ROOT = Path(__file__).resolve().parents[3]
OUT_PATH = ROOT / "data" / "04-summaries" / "v20_1-crs-full-grid.json"
CACHE_PATH = ROOT / "engine" / "cache" / "strength_chain" / "pre_days_cache.pkl"


def load_or_preload(ctx, z2i, memo):
    key = f"v2|{max((f.stat().st_mtime for f in HIST.glob('crs_hist_*.json')), default=0):.0f}|{len(LEAGUES)}"
    if CACHE_PATH.exists():
        try:
            obj = pickle.loads(CACHE_PATH.read_bytes())
            if obj.get("key") == key:
                print("preload 命中磁盘缓存", flush=True)
                return obj["pre"]
        except Exception:
            pass
    pre = preload_days(ctx, z2i, memo)
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_bytes(pickle.dumps({"key": key, "pre": pre}))
    print("preload 已写磁盘缓存", flush=True)
    return pre
BANDS = ((0.0, 3.0, "<3"), (3.0, 5.0, "3-5"), (5.0, 10.0, "5-10"), (10.0, 1e9, ">=10"))
P_BANDS = ((0.0, 0.05, "<5%"), (0.05, 0.10, "5-10%"), (0.10, 0.20, "10-20%"), (0.20, 1.01, ">=20%"))
BOOT_N = 2000
SEED = 20261008


def e_and_ci(cells):
    """cells: [(p, odds, hit)] → (e, n, ci或None)。"""
    if not cells:
        return None, 0, None
    n = len(cells)
    e = sum((1.0 if h else 0.0) * o for _, o, h in cells) / n
    if n < 30:
        return e, n, None
    rng = random.Random(SEED)
    es = sorted(sum((1.0 if cells[(k := rng.randrange(n))][2] else 0.0) * cells[k][1]
                    for _ in range(n)) / n for _ in range(BOOT_N))
    return e, n, (es[int(BOOT_N * 0.025)], es[int(BOOT_N * 0.975)])


def main():
    print("══ v20.1 CRS 全格口径 e 曲线 ══\n", flush=True)
    print("预注册: 脚本头跑前写死\n", flush=True)
    ctx = sl.build_ctx(LEAGUES)
    z2i = sl.zh_to_id()
    memo = {}
    print("── preload（带磁盘缓存）──", flush=True)
    pre = load_or_preload(ctx, z2i, memo)
    print(f"preload 完: {len(pre)}日\n", flush=True)

    all_cells, picked_cells = [], []
    model_cal = []
    for day, cands in pre.items():
        if not (FIT_WINDOW[0] <= day <= FIT_WINDOW[1]):
            continue
        for c in cands:
            top = max(c["cells"], key=lambda x: x["p"])
            for cell in c["cells"]:
                rec = (cell["p"], cell["odds"], cell["mk"] == c["real"])
                all_cells.append(rec)
                model_cal.append(rec)
                if cell["mk"] == top["mk"]:
                    picked_cells.append(rec)

    print(f"全格样本: {len(all_cells)} 格 · 选中口径: {len(picked_cells)} 格\n")

    def fmt(x):
        return f"{x:.3f}" if x is not None else "n/a"

    print("── A) 赔率带 e：全选项 vs 选择口径 ──")
    print(f"{'带':<8}{'全n':<8}{'全e':<8}{'全CI':<18}{'选n':<6}{'选e':<8}{'选CI'}")
    out_bands = {}
    for lo, hi, name in BANDS:
        a = [c for c in all_cells if lo <= c[1] < hi]
        s = [c for c in picked_cells if lo <= c[1] < hi]
        ea, na, cia = e_and_ci(a)
        es, ns, cis = e_and_ci(s)
        out_bands[name] = {"all": {"n": na, "e": round(ea, 4) if ea else None, "ci": cia},
                           "picked": {"n": ns, "e": round(es, 4) if es else None, "ci": cis}}
        print(f"{name:<8}{na:<8}{fmt(ea):<8}{str(cia):<18}{ns:<6}{fmt(es):<8}{cis}")

    print("\n── B) 模型概率分桶校准（全格）──")
    print(f"{'p_model带':<10}{'n':<8}{'meanP':<8}{'realized':<10}{'dev':<8}")
    out_cal = {}
    for lo, hi, name in P_BANDS:
        b = [c for c in model_cal if lo <= c[0] < hi]
        if not b:
            continue
        mp = sum(c[0] for c in b) / len(b)
        act = sum(1 for c in b if c[2]) / len(b)
        out_cal[name] = {"n": len(b), "meanP": round(mp, 4), "realized": round(act, 4),
                         "dev": round(mp - act, 4)}
        print(f"{name:<10}{len(b):<8}{mp:<8.4f}{act:<10.4f}{mp-act:<+8.4f}")

    ea_low = out_bands.get(">=10", {}).get("all", {}).get("e")
    es_low = out_bands.get(">=10", {}).get("picked", {}).get("e")
    if ea_low is not None and es_low is not None:
        if ea_low > es_low + 0.15:
            judge = "H1选择偏差实锤——topP挑格专挑定价错误格·决策函数R1改挑格方式"
        elif ea_low <= 0.3:
            judge = "H2模型CRS高估实锤——维持禁高赔比分格·且模型CRS概率端须打折"
        else:
            judge = "混合——带级细看"
    else:
        judge = "样本不足"
    print(f"\n══ 判读: {judge} ══")

    result = {"ranAt": "2026-10-08", "preReg": "脚本头跑前写死",
              "nAll": len(all_cells), "nPicked": len(picked_cells),
              "bandsByOdds": out_bands, "modelCalibration": out_cal, "judge": judge}
    OUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"归档 {OUT_PATH.relative_to(ROOT)}", flush=True)


if __name__ == "__main__":
    main()
