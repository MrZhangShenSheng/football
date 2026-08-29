#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""预测偏差归因引擎：对错题逐场判别偏差因子，落 attribution.json。

设计：docs/2026-08-29-attribution-design.html（四层12因子 + 判别树 + 消融门）。
P1 范围：F5（dc vs fused·低置信）/ F9（兜底）/ F10（赔率漂移）。
  F3/F4 待 P2 真收盘三向（反解循环论证失效，见 impl-plan 设计修正说明）。
数据源：data/02-results/*.json 主文件（非 corpus——需 dc/fused 数组做 F5 判别）。
"""
import json
from collections import defaultdict
from pathlib import Path

from common import log, ROOT

OUT = ROOT / "data" / "04-summaries" / "attribution.json"
RESULTS_DIR = ROOT / "data" / "02-results"
SCORE_ODDS_DIR = ROOT / "engine" / "cache" / "score_odds"

# 方向 → 三向数组下标
_DIR_IDX = {"主胜": 0, "胜": 0, "平": 1, "平局": 1, "客胜": 2}


def pick_to_index(play: str, pick: str) -> int | None:
    """'HAD 客胜' → 2。非 HAD 或无法解析 → None。"""
    if play != "HAD":
        return None
    return _DIR_IDX.get(pick.strip())


def result_to_idx(result: str) -> int | None:
    """'3-1'→0(主胜) / '0-2'→2(客胜) / '2-2'→1(平)。"""
    if not result or "-" not in str(result):
        return None
    parts = str(result).split("-")
    try:
        h, a = int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return None
    if h > a:
        return 0
    if h < a:
        return 2
    return 1


def _parse_pick(pick: str) -> tuple[str, str]:
    """'HAD 客胜' → ('HAD','客胜')。"""
    if " " in pick:
        p, d = pick.split(" ", 1)
        return p.strip(), d.strip()
    return "", pick.strip()


def correction_flipped(dc: list, fused: list, result_idx: int) -> bool | None:
    """F5 近似（R4 低置信）：dc 最高向==结果（DC 原本对）且 fused 最高向!=结果（融合后错）。

    P1 无法区分 chain 修正乘子 vs 融合 a/b 配比导致 → 统一归 F5；
    F4（纯融合稀释）待 P2 落盘 chainSteps[] 后从 F5 中分离。
    数据不足（无 dc/fused 或 result_idx 越界）→ None。
    """
    if not dc or not fused or result_idx is None or result_idx >= 3:
        return None
    dc_best = dc.index(max(dc))
    fused_best = fused.index(max(fused))
    return dc_best == result_idx and fused_best != result_idx


def classify(rec: dict) -> dict:
    """主判别树：错题 → {primary, secondary, evidence, confidence}。

    P1 判别顺序：①F5(dc对fused错) → ②F9(兜底)。
    F3/F4 待 P2 真收盘三向（反解循环论证失效，见 impl-plan 设计修正说明）。
    F10 执行层在 build() 中独立叠加（不在此函数，因需 score_odds 外部数据）。
    """
    play, direction = _parse_pick(rec.get("pick") or "")
    pick_idx = pick_to_index(play, direction)
    result_idx = result_to_idx(rec.get("result") or "")
    dc = rec.get("dc")
    fused = rec.get("fused")
    ev = {"pfinalPick": None, "dcBest": None, "fusedBest": None,
          "pickIdx": pick_idx, "resultIdx": result_idx,
          "scoreBias": rec.get("result")}

    # 非 HAD 或结果不可解析 → F9 低置信（R7 变体待 P2）
    if pick_idx is None or result_idx is None:
        return {"primary": "F9", "secondary": [], "evidence": ev, "confidence": "low"}

    if fused and pick_idx < len(fused):
        ev["pfinalPick"] = round(float(fused[pick_idx]), 4)
    if dc:
        ev["dcBest"] = dc.index(max(dc))
    if fused:
        ev["fusedBest"] = fused.index(max(fused))

    # ① F5 修正/融合背锅（dc 对 + fused 错）
    if correction_flipped(dc, fused, result_idx):
        return {"primary": "F5", "secondary": [], "evidence": ev, "confidence": "low"}

    # ② F9 随机兜底（dc 也错，或 dc/fused 同向错）
    return {"primary": "F9", "secondary": [], "evidence": ev, "confidence": "high"}
