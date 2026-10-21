# engine/scripts/score_attribution.py
"""比分归因引擎·期1：forward 卷适配 + 账单组装 + CLI。
设计 docs/plans/2026-10-20-score-attribution-design.html。开发者 sszhang"""
import json
import sys
from datetime import date, datetime
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import strength_loaders as sl  # load_aliases/zh_to_id 复用

ROOT = SCRIPTS_DIR.parents[1]
RESULTS_DIR = ROOT / "data" / "02-results"
LINEUPS_DIR = ROOT / "data" / "05-trends"
TEAMS_DIR = ROOT / "data" / "01-teams"

# 02-results 中文联赛名 → league 库目录名（ rested/stage 判定用；未映射联赛相应维度标缺）
LEAGUE_DIR_MAP = {
    "意甲": "italy-serie-a", "英超": "england-premier", "西甲": "spain-laliga",
    "德甲": "germany-bundesliga", "法甲": "france-ligue1", "荷甲": "netherlands-eredivisie",
    "葡超": "portugal-primeira", "美职": "usa", "欧冠": "", "亚冠": "",
}

def _parse_score(s: str | None) -> tuple[int, int] | None:
    """'4-0' → (4,0)；'1:0'（半场）同构。失败返回 None。开发者 sszhang"""
    if not s:
        return None
    for sep in ("-", ":"):
        if sep in s:
            a, b = s.split(sep)[:2]
            return int(a), int(b)
    return None

def _find_in_results(date_str: str, code: str) -> dict | None:
    """02-results/{date}.json 按 code 找 match 原始行。开发者 sszhang"""
    path = RESULTS_DIR / f"{date_str}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    for m in data.get("matches", []):
        if m.get("code") == code:
            return m
    return None

def _team_profile(team_id: str | None) -> dict:
    """规范ID→画像 dict（elo/standing/form）；无档案返回 {}。开发者 sszhang"""
    if not team_id:
        return {}
    hits = list(TEAMS_DIR.glob(f"*/{team_id}.json"))
    if not hits:
        return {}
    return json.loads(hits[0].read_text(encoding="utf-8"))

def _injury_count(m: dict) -> tuple[int, int]:
    """lineup 轨最后快照伤停人数 (h,a)；无轨返回 (0,0)。开发者 sszhang"""
    # 05-trends/{开赛日}-lineups.json 以 matchId 为键（开赛日=行内 date，非销售日）——brief 原稿按
    # (销售日, code) 查在真实数据上必落空（静默恒 0），按仓库实际结构改键；键为字符串需 str()。
    match_id = m.get("matchId")
    kickoff_day = str(m.get("date") or "")[:10]
    if match_id is None or not kickoff_day:
        return 0, 0
    path = LINEUPS_DIR / f"{kickoff_day}-lineups.json"
    if not path.exists():
        return 0, 0
    data = json.loads(path.read_text(encoding="utf-8"))
    rec = (data.get("matches") or {}).get(str(match_id)) or {}
    snaps = rec.get("injSnapshots") or []
    if not snaps:
        return 0, 0
    inj = snaps[-1].get("inj") or {}
    return len(inj.get("h") or []), len(inj.get("a") or [])

def _last_match_date(team_id: str, league_dir: str, before: str) -> str | None:
    """league 库该队 before 前最近一场日期（算休息天数用）。开发者 sszhang"""
    path = RESULTS_DIR / "league" / f"{league_dir}_matches.json"
    if not path.exists() or not team_id:
        return None
    rows = json.loads(path.read_text(encoding="utf-8"))
    rows = rows if isinstance(rows, list) else rows.get("matches", [])
    cands = [r["date"] for r in rows
             if r["date"] < before and team_id in (r.get("home"), r.get("away"))]
    return max(cands) if cands else None

def load_match_record(date_str: str, code: str) -> dict | None:
    """forward 卷单场 → MatchRecord（账单引擎唯一输入）。开发者 sszhang"""
    m = _find_in_results(date_str, code)
    if m is None:
        return None
    zh_map = sl.zh_to_id()
    home_zh, away_zh = m["match"].split(" vs ")
    team_h, team_a = zh_map.get(home_zh), zh_map.get(away_zh)
    prof_h, prof_a = _team_profile(team_h), _team_profile(team_a)
    hg, ag = _parse_score(m.get("result")) or (None, None)
    ht_hg, ht_ag = _parse_score(m.get("half")) or (None, None)
    inj_h, inj_a = _injury_count(m)
    o = m.get("odds") or {}
    league_dir = LEAGUE_DIR_MAP.get(m.get("league"), "")
    return {
        "date": date_str, "code": code, "league": m.get("league"),
        "match": m["match"], "team_h": team_h, "team_a": team_a,
        "league_dir": league_dir,
        "odds": [float(o["h"]), float(o["d"]), float(o["a"])],
        "pin_close": m.get("pinClose") or None,
        "elo_h": prof_h.get("elo"), "elo_a": prof_a.get("elo"),
        "inj_n_h": inj_h, "inj_n_a": inj_a,
        "rest_days_h": None, "rest_days_a": None,   # Task 5 填
        "result": {"hg": hg, "ag": ag, "ht_hg": ht_hg, "ht_ag": ht_ag},
    }
