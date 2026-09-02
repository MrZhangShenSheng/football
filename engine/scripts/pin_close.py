#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fd Pinnacle 收盘匹配 v2（四键）：完赛日±1 + 联赛 + 队名桥接（主键）+ 比分（校验/回退键）。

P2 归因地基（docs/2026-08-29-attribution-p2-plan.html）：F3/F4 判别的市场锚来源。
v2 修复（docs/2026-09-02-data-backfill-design.html 层2）：
- 队名第四键：_aliases.json zh→fd 名桥接（主路径），消 ambiguous 撞行（英超单轮同比分常现）
- 赛季隔离：按比赛日期只读对应赛季文件，根除跨季错配（上季同窗同比分行被静默误用）
- 桥接失败/未传 match → 回退旧三键（日期+比分），回退路径硬性单季文件——存量行为兼容
ambiguous（多行）→ 跳过标 ambiguous，统计率驱动 P2.5 种子映射。
"""
import json
import re
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from dc_predict import devig

ROOT = Path(__file__).resolve().parent.parent.parent

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


def season_of(iso: str) -> str | None:
    """比赛日 → fd 赛季文件段（跨年制：2026-09-02→'2627'，2026-03-01→'2526'）。"""
    try:
        d = date.fromisoformat(str(iso)[:10])
    except ValueError:
        return None
    y = d.year
    return f"{y % 100:02d}{(y + 1) % 100:02d}" if d.month >= 7 else f"{(y - 1) % 100:02d}{y % 100:02d}"


def _norm_name(s) -> str:
    """队名归一：小写+剔除分隔符，保留词字符（含中文——'Man City'/'man-city'→'mancity'，'女王巡游'→'女王巡游'）。"""
    return re.sub(r"[^\w]", "", str(s or "").lower(), flags=re.UNICODE)


@lru_cache(maxsize=1)
def _zh_to_fd_names() -> dict[str, str]:
    """中文队名（含 variants）→ fd 英文名（均归一）。

    fd 键缺失的队用 teamId 归一兜底（teamId≈kebab-case fd 名，大半直接相等）。
    """
    try:
        aliases = json.loads((ROOT / "data" / "01-teams" / "_aliases.json")
                             .read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    m: dict[str, str] = {}
    for lg, teams in aliases.items():
        if lg.startswith("_") or not isinstance(teams, dict):
            continue
        for team_id, info in teams.items():
            if not isinstance(info, dict):
                continue
            fd_name = _norm_name(info.get("fd") or team_id)
            for zh in [info.get("zh"), *(info.get("variants") or [])]:
                if zh:
                    m[_norm_name(zh)] = fd_name
    return m


def _split_match(match_zh: str | None) -> tuple[str | None, str | None]:
    """'女王巡游 vs 加的夫城' → (主, 客)；无 vs 或拆不出两段 → (None, None)。"""
    if not match_zh:
        return None, None
    parts = re.split(r"\s*vs\s*", str(match_zh), flags=re.I)
    if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
        return None, None
    return parts[0].strip(), parts[1].strip()


def _row_name_match(row_name: str, zh: str) -> bool:
    """fd 行队名 vs 中文队名经 aliases 桥接（L1 精等 / L2 互含≥4字符兜底）。"""
    target = _zh_to_fd_names().get(_norm_name(zh))
    if not target:
        return False
    rn = _norm_name(row_name)
    if rn == target:
        return True
    if len(rn) >= 4 and len(target) >= 4 and (target in rn or rn in target):
        return True   # fd 简写兜底：'Wolves'⊂'wolverhamptonwanderers' 类
    return False


def _read_rows(cache_dir: Path, lg: str, season: str) -> list[dict]:
    f = Path(cache_dir) / f"odds_{lg}_{season}.json"
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data.get("matches") or []


def _row_pin(r: dict) -> tuple[str, list | None]:
    try:
        odds = [float(r["pin_h"]), float(r["pin_d"]), float(r["pin_a"])]
    except (KeyError, TypeError, ValueError):
        return "none", None
    return "fd", [round(v, 4) for v in devig(odds)]


def match_pin_close(league_zh: str, match_date_iso: str, result: str,
                    cache_dir: Path, match_zh: str | None = None) -> tuple[str, list | None]:
    """四键匹配 → (pinSource, pinClose 去水三向 or None)。

    pinSource: fd=唯一匹配 / ambiguous=撞行 / none=无覆盖、无行或队名选中但比分冲突。
    主路径（match_zh 双方可桥接）：日期窗+联赛扫候选，队名双匹配唯一行，比分校验后收。
    回退路径（桥接失败/未传 match）：旧三键（日期+比分），仅当季文件——防跨季错配。
    """
    lg = fd_league_name(league_zh)
    if lg is None or not match_date_iso or not result or "-" not in str(result):
        return "none", None
    hg, _, ag = str(result).partition("-")
    hg_i, ag_i = _score_int(hg), _score_int(ag)
    if hg_i is None or ag_i is None:
        return "none", None   # 带注释比分（'2-1（加时）'类）→ 保守 miss
    try:
        window = _date_window(str(match_date_iso)[:10])
    except ValueError:
        return "none", None   # matchDate 非 ISO（体彩口径可能带时间）→ 安全降级
    season = season_of(str(match_date_iso)[:10])
    if season is None:
        return "none", None
    seasons = {season_of(w) for w in window} - {None}   # ±1 天跨季界时含两季

    # 主路径：队名第四键（跨季窗口文件都扫——队名是强键不会错配）
    zh_home, zh_away = _split_match(match_zh)
    if (zh_home and zh_away
            and _zh_to_fd_names().get(_norm_name(zh_home))
            and _zh_to_fd_names().get(_norm_name(zh_away))):
        cands = [r for s in sorted(seasons)
                 for r in _read_rows(cache_dir, lg, s)
                 if parse_fd_date(r.get("date")) in window]
        if cands:
            team_hits = [r for r in cands
                         if _row_name_match(r.get("home"), zh_home)
                         and _row_name_match(r.get("away"), zh_away)]
            if len(team_hits) == 1:
                r = team_hits[0]
                if _score_int(r.get("fthg")) == hg_i and _score_int(r.get("ftag")) == ag_i:
                    return _row_pin(r)
                return "none", None   # 队名选中但比分冲突 → 数据冲突诚实降级
            if len(team_hits) > 1:
                return "ambiguous", None
            # 队名 0 命中（fd 当日行未更新等）→ 落入回退路径
    # 回退路径：旧三键（日期+比分）——仅当季文件，防跨季错配
    score_hits = [r for r in _read_rows(cache_dir, lg, season)
                  if parse_fd_date(r.get("date")) in window
                  and _score_int(r.get("fthg")) == hg_i
                  and _score_int(r.get("ftag")) == ag_i]
    if not score_hits:
        return "none", None
    if len(score_hits) > 1:
        return "ambiguous", None
    return _row_pin(score_hits[0])


def apply_pin_close(rec: dict, match_date_iso: str, cache_dir: Path) -> bool:
    """回填集成点：rec 增补 pinClose/pinSource，返回是否有实质变更（幂等）。

    ambiguous/none 场不冻结——fd 缓存刷新后重跑 backfill 自动救回（幂等键只看 pinClose）。
    写回铁律扩展：此二字段为增补字段，不属于预测锁定字段。
    """
    if rec.get("pinClose"):
        return False
    src, pin = match_pin_close(rec.get("league"), match_date_iso,
                               rec.get("result") or "", cache_dir,
                               match_zh=rec.get("match"))
    if rec.get("pinSource") == src and not pin:
        return False   # 结论未变（none→none）不置 dirty，避免每次重写文件
    rec["pinSource"] = src
    if pin:
        rec["pinClose"] = pin
    return True
