#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""采集脚本公共工具：别名表加载 + 球队画像文件读写骨架。"""
import json
import sys
from datetime import date
from pathlib import Path

# Windows 控制台中文乱码：统一强制 UTF-8 输出（Python 3.7+）
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
TEAMS_DIR = ROOT / "data" / "01-teams"
ALIASES_PATH = TEAMS_DIR / "_aliases.json"


def load_aliases() -> dict:
    """返回扁平映射：规范ID -> {league, zh, clubelo, understat, espn}。"""
    raw = json.loads(ALIASES_PATH.read_text(encoding="utf-8"))
    flat = {}
    for league, teams in raw.items():
        if league.startswith("_"):
            continue
        for team_id, srcs in teams.items():
            flat[team_id] = {"league": league, **srcs}
    return flat


def team_path(team_id: str, league: str) -> Path:
    return TEAMS_DIR / league / f"{team_id}.json"


def load_team(team_id: str, league: str) -> dict:
    p = team_path(team_id, league)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"team": None, "league": league, "season": None, "lastUpdated": date.today().isoformat()}


def save_team(team_id: str, league: str, data: dict, zh: str = None) -> Path:
    p = team_path(team_id, league)
    p.parent.mkdir(parents=True, exist_ok=True)
    if zh and not data.get("team"):
        data["team"] = zh
    data["league"] = league
    data["lastUpdated"] = date.today().isoformat()
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def log(tag: str, msg: str) -> None:
    print(f"[{tag}] {msg}")
