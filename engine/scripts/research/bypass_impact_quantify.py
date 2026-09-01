#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""D3 复审补算：P1 融合旁路（a=0.4→0）对已选腿概率的实际扰动（2026-09-01 会话）。

问题：融合公式 σ(0.4·logit(p_DC) + 1.0·logit(p_mkt)) 中市场本就全权重，
旁路 a 对倾向项概率扰动多大？是否改变 65% 准入归类与倾向方向？
—— 判定 P1 是"性能变更"还是"口径诚实+简化"。
"""
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "engine" / "scripts"))
from backtest import dc_three  # noqa: E402
from dc_fit import load_matches  # noqa: E402
from dc_predict import devig  # noqa: E402

CACHE = ROOT / "engine" / "cache"

FD_LEAGUES = [
    "england-premier", "england-championship", "spain-laliga", "germany-bundesliga",
    "germany-bundesliga2", "italy-serie-a", "italy-serie-b", "france-ligue1",
    "france-ligue2", "netherlands-eredivisie", "portugal-primeira",
    "belgium-first-a", "turkey-super-lig", "greece-super",
]

A, B = 0.4, 1.0  # engine/cache/fusion.json 现值
THRESHOLD = 0.65


def logit(p):
    p = min(max(p, 1e-9), 1 - 1e-9)
    return math.log(p / (1 - p))


def main():
    diffs = []           # 倾向项 |fused - mkt|
    argmax_flip = 0      # 倾向方向不一致
    gate_in = gate_out = 0  # 65% 门槛进出（mkt 判定 vs fused 判定）
    n = n_dc = 0
    groups = {"both_ge65": [0, 0], "dc_pulled_in": [0, 0], "mkt_only_out": [0, 0], "both_lt65": [0, 0]}
    for lg in FD_LEAGUES:
        dc_path = CACHE / f"{lg}_dc.json"
        if not dc_path.exists():
            continue
        dc = json.loads(dc_path.read_text(encoding="utf-8"))
        for season in ("2526", "2627"):
            op = CACHE / f"odds_{lg}_{season}.json"
            if not op.exists():
                continue
            data = json.loads(op.read_text(encoding="utf-8"))
            for m in data.get("matches", []):
                if m.get("fthg") is None or not m.get("pin_h"):
                    continue
                try:
                    odds = (float(m["pin_h"]), float(m["pin_d"]), float(m["pin_a"]))
                except (ValueError, TypeError):
                    continue
                n += 1
                res = dc_three(dc["teams"], dc["homeAdv"], dc["rho"], m["home"], m["away"])
                if res is None:
                    continue
                n_dc += 1
                p_dc, _ = res
                p_mkt = devig(odds)
                p_fused = []
                for i in range(3):
                    z = A * logit(p_dc[i]) + B * logit(p_mkt[i])
                    p_fused.append(1 / (1 + math.exp(-z)))
                mi = max(range(3), key=lambda i: p_mkt[i])
                fi = max(range(3), key=lambda i: p_fused[i])
                if mi != fi:
                    argmax_flip += 1
                diffs.append(abs(p_fused[mi] - p_mkt[mi]))
                if p_mkt[mi] >= THRESHOLD and p_fused[mi] < THRESHOLD:
                    gate_out += 1
                if p_mkt[mi] < THRESHOLD and p_fused[mi] >= THRESHOLD:
                    gate_in += 1
                # 分组命中：判定口径用市场倾向项 mi（旁路后的选腿视角）
                outcome = 0 if int(m["fthg"]) > int(m["ftag"]) else (1 if int(m["fthg"]) == int(m["ftag"]) else 2)
                mkt_ge = p_mkt[mi] >= THRESHOLD
                fused_ge = p_fused[mi] >= THRESHOLD
                if mkt_ge and fused_ge:
                    g = "both_ge65"
                elif not mkt_ge and fused_ge:
                    g = "dc_pulled_in"
                elif mkt_ge and not fused_ge:
                    g = "mkt_only_out"
                else:
                    g = "both_lt65"
                groups[g][0] += 1
                groups[g][1] += (mi == outcome)

    diffs.sort()
    mean_d = sum(diffs) / len(diffs)
    p50 = diffs[len(diffs) // 2]
    p90 = diffs[int(len(diffs) * 0.9)]
    p99 = diffs[int(len(diffs) * 0.99)]
    print(f"样本：{n} 场有收盘，其中 DC 可算 {n_dc} 场")
    print(f"倾向项 |融合-市场| 概率扰动：均值 {mean_d:.3%} | p50 {p50:.3%} | p90 {p90:.3%} | p99 {p99:.3%} | max {diffs[-1]:.3%}")
    print(f"倾向方向翻转（argmax 不一致）：{argmax_flip}/{n_dc} = {argmax_flip / n_dc:.1%}")
    print(f"65% 准入门槛翻转：市场达标→融合掉出 {gate_out} 场 | 市场不达→融合拉入 {gate_in} 场")
    print(f"门槛翻转合计占 65%±扰动带外判定：{(gate_in + gate_out) / n_dc:.2%} of {n_dc}")
    print("\n分组倾向项命中（判定=市场倾向，旁观路前后准入集合质量）：")
    labels = {
        "both_ge65": "双达标（市场≥65 且融合≥65）",
        "dc_pulled_in": "DC 拉入（市场<65 但融合≥65）",
        "mkt_only_out": "融合拉出（市场≥65 但融合<65）",
        "both_lt65": "双不达标",
    }
    for g, (cnt, hit) in groups.items():
        if cnt:
            print(f"  {labels[g]}: {cnt} 场 | 命中 {hit} = {hit / cnt:.1%}")


if __name__ == "__main__":
    main()
