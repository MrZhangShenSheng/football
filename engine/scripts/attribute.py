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
