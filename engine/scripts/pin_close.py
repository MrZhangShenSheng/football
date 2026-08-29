#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fd Pinnacle 收盘三键匹配：完赛日±1 + 比分 + 联赛 → 去水三向（pinClose）。

P2 归因地基（docs/2026-08-29-attribution-p2-plan.html）：F3/F4 判别的市场锚来源。
ambiguous（同窗同比分同联赛多行）→ 跳过标 ambiguous，统计率驱动 P2.5 种子映射。
"""
import json
import re
from datetime import date, timedelta
from pathlib import Path

from dc_predict import devig

# 中文联赛名（strip 轮次/资格赛后缀后）→ fd odds 文件 league_name
FD_LEAGUE_MAP = {
    "英超": "england-premier", "英冠": "england-championship",
    "西甲": "spain-laliga", "西乙": "spain-liga2",
    "德甲": "germany-bundesliga", "德乙": "germany-bundesliga2",
    "意甲": "italy-serie-a", "意乙": "italy-serie-b",
    "法甲": "france-ligue1", "法乙": "france-ligue2",
    "荷甲": "netherlands-eredivisie", "比甲": "belgium-first-a",
    "葡超": "portugal-liga", "土超": "turkey-super-lig",
    "希腊超": "greece-super", "俄超": "russia-premier",
    "欧冠": "EC0", "欧冠资格赛": "EC0", "欧冠附加赛": "EC0",
    "欧罗巴": "EL0",   # fd 侧 EL0 缓存未拉过（glob 空→none，拉了自动激活）
    "苏超": "SC0",
}
_SUFFIX = re.compile(r"[（(].*?[)）]$")


def parse_fd_date(s: str | None) -> str | None:
    """fd 'dd/mm/yyyy' → 'yyyy-mm-dd'。"""
    if not s or "/" not in str(s):
        return None
    try:
        d, m, y = str(s).split("/")
        return date(int(y), int(m), int(d)).isoformat()
    except (ValueError, TypeError):
        return None


def fd_league_name(league_zh: str | None) -> str | None:
    """'德乙(R3)' → 'germany-bundesliga2'。fd 不覆盖联赛 → None。"""
    if not league_zh:
        return None
    base = _SUFFIX.sub("", str(league_zh)).strip()
    return FD_LEAGUE_MAP.get(base)


def _score_int(s: str | None) -> int | None:
    """比分部件 int 归一（'05'→5·带注释'1（加时）'→None 保守 miss）。"""
    try:
        return int(str(s).strip())
    except (TypeError, ValueError):
        return None


def _date_window(iso: str) -> set[str]:
    d = date.fromisoformat(iso)
    return {(d + timedelta(days=k)).isoformat() for k in (-1, 0, 1)}


def match_pin_close(league_zh: str, match_date_iso: str, result: str,
                    cache_dir: Path) -> tuple[str, list | None]:
    """三键匹配 → (pinSource, pinClose 去水三向 or None)。

    pinSource: fd=唯一匹配 / ambiguous=撞比分 / none=无覆盖或无行。
    """
    lg = fd_league_name(league_zh)
    if lg is None or not match_date_iso or not result or "-" not in str(result):
        return "none", None
    hg, _, ag = str(result).partition("-")
    hg_i, ag_i = _score_int(hg), _score_int(ag)
    if hg_i is None or ag_i is None:
        return "none", None   # 带注释比分（'2-1（加时）'类）→ 保守 miss
    try:
        window = _date_window(match_date_iso)
    except ValueError:
        return "none", None   # matchDate 非 ISO（体彩口径可能带时间）→ 安全降级
    hits: list[dict] = []
    for f in sorted(cache_dir.glob(f"odds_{lg}_*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for row in data.get("matches") or []:
            if parse_fd_date(row.get("date")) not in window:
                continue
            if _score_int(row.get("fthg")) == hg_i and _score_int(row.get("ftag")) == ag_i:
                hits.append(row)
    if not hits:
        return "none", None
    if len(hits) > 1:
        return "ambiguous", None
    r = hits[0]
    try:
        odds = [float(r["pin_h"]), float(r["pin_d"]), float(r["pin_a"])]
    except (KeyError, TypeError, ValueError):
        return "none", None
    return "fd", [round(v, 4) for v in devig(odds)]


def apply_pin_close(rec: dict, match_date_iso: str, cache_dir: Path) -> bool:
    """回填集成点：rec 增补 pinClose/pinSource，返回是否有实质变更（幂等）。

    ambiguous/none 场不冻结——fd 缓存刷新后重跑 backfill 自动救回（幂等键只看 pinClose）。
    写回铁律扩展：此二字段为增补字段，不属于预测锁定字段。
    """
    if rec.get("pinClose"):
        return False
    src, pin = match_pin_close(rec.get("league"), match_date_iso,
                               rec.get("result") or "", cache_dir)
    if rec.get("pinSource") == src and not pin:
        return False   # 结论未变（none→none）不置 dirty，避免每次重写文件
    rec["pinSource"] = src
    if pin:
        rec["pinClose"] = pin
    return True
