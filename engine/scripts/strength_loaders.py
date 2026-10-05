# engine/scripts/strength_loaders.py
# -*- coding: utf-8 -*-
"""实力链数据装载器：四砖（league赛果/DC/自建Elo/fd xG）统一 as-of 读取。
铁律14：as-of 语义 = 只见 ≤ as_of - lag_days 的行（lag=2 与 common.strict_merged 一致）。
开发者 sszhang"""
from __future__ import annotations
import json, sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, load_aliases

LEAGUES_DIR = ROOT / "data" / "02-results" / "league"
CACHE_DIR = ROOT / "engine" / "cache"
XG_WINDOW_N = 10        # 预注册舱 hyperparamsFixed.xgWindowN
LAG_DAYS = 2

def _read(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default

def _ddmmyyyy(s: str) -> date | None:
    try:
        return datetime.strptime(s, "%d/%m/%Y").date()
    except (ValueError, TypeError):
        return None

def zh_to_id(aliases: dict | None = None) -> dict[str, str]:
    """体彩中文队名 → 规范ID（zh+variants 全收录）。"""
    out = {}
    for tid, srcs in (aliases if aliases is not None else load_aliases()).items():
        for zh in [srcs.get("zh"), *(srcs.get("variants") or [])]:
            if zh:
                out.setdefault(zh, tid)
    return out

def as_of_rows(rows: list[dict], as_of: date, lag_days: int = LAG_DAYS) -> list[dict]:
    """行 date(ISO字符串) ≤ as_of - lag_days 才可见。"""
    cutoff = (as_of - timedelta(days=lag_days)).isoformat()
    return [r for r in rows if str(r.get("date", ""))[:10] <= cutoff]

def build_ctx(leagues: list[str], *, leagues_dir: Path = LEAGUES_DIR, cache_dir: Path = CACHE_DIR) -> dict:
    """预装载全部四砖（一次性读盘，team_state_on 纯内存过滤）。"""
    ctx = {"timeline": {}, "dc": {}, "elo": {}, "xg": {}}
    for lg in leagues:
        ctx["timeline"][lg] = _read(leagues_dir / f"{lg}_matches.json", [])
        ctx["dc"][lg] = _read(cache_dir / f"{lg}_dc.json", {})
        ctx["elo"][lg] = (_read(cache_dir / f"elo_history_{lg}_2526.json", {})
                          or _read(cache_dir / f"elo_history_{lg}_2627.json", {}))
        ctx["xg"][lg] = (_read(cache_dir / f"odds_{lg}_2526.json", {})
                         or _read(cache_dir / f"odds_{lg}_2627.json", {})).get("matches", [])
    return ctx

def team_state_on(team: str, as_of: date, ctx: dict, lag_days: int = LAG_DAYS) -> dict:
    """队的 as-of 快照：滚动xG(近N场)/联赛内Elo(最近pre值)/DC参数。降级记 flags（报告忠实度）。"""
    st = {"team": team, "league": None, "elo": None, "xg_att": None, "xg_def": None,
          "n_xg": 0, "dc_att": 0.0, "dc_def": 0.0, "flags": []}
    for lg, rows in ctx["timeline"].items():
        if any(team in (r.get("home"), r.get("away")) for r in rows):
            st["league"] = lg
            break
    if st["league"] is None:
        st["flags"].append("no_league")
        return st
    lg = st["league"]
    dc_team = (ctx["dc"].get(lg, {}).get("teams") or {}).get(team)
    if dc_team:
        st["dc_att"], st["dc_def"] = float(dc_team["attack"]), float(dc_team["defense"])
    else:
        st["flags"].append("no_dc")
    # 自建 Elo：最近一次 pre 值（as-of）
    elo_rows = [r for r in (ctx["elo"].get(lg) or {}).get("rows", [])
                if team in (r.get("home"), r.get("away")) and str(r.get("date")) <= as_of.isoformat()]
    if elo_rows:
        r = elo_rows[-1]
        st["elo"] = float(r["elo_home_pre"] if r["home"] == team else r["elo_away_pre"])
    else:
        st["flags"].append("no_elo")
    # xG 滚动窗（近 N 场我方攻/对方对我的防）
    xs = []
    for m in ctx["xg"].get(lg, []):
        d = _ddmmyyyy(m.get("date", ""))
        if d is None or (as_of - timedelta(days=lag_days)) < d:
            continue
        if m.get("home") == team and m.get("hxg") is not None:
            xs.append((float(m["hxg"]), float(m["axg"])))
        elif m.get("away") == team and m.get("axg") is not None:
            xs.append((float(m["axg"]), float(m["hxg"])))
    xs = xs[-XG_WINDOW_N:]
    if xs:
        st["n_xg"] = len(xs)
        st["xg_att"] = sum(x[0] for x in xs) / len(xs)
        st["xg_def"] = sum(x[1] for x in xs) / len(xs)
    else:
        st["flags"].append("no_xg")
    return st
