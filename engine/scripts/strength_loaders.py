# engine/scripts/strength_loaders.py
# -*- coding: utf-8 -*-
"""实力链数据装载器：四砖（league赛果/DC/自建Elo/fd xG）as-of 读取。
铁律14：timeline/xG 的 as-of 语义 = 只见 ≤ as_of - lag_days 的行（lag=2 与 common.strict_merged 一致）；
Elo 砖 lag=0 —— elo_*_pre 是赛前值不含该场赛果，date ≤ as_of 即可见，无答案泄漏。
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

def _norm_team(name: str, aliases: dict) -> str | None:
    """fd/ESPN 显示名 → 规范ID：kebab 命中优先，espn 别名兜底；None=不可映射（行/键丢弃）。"""
    kebab = name.lower().replace(" ", "-").replace("'", "")
    if kebab in aliases:
        return kebab
    for tid, srcs in aliases.items():
        if srcs.get("espn") and srcs["espn"].lower() == name.lower():
            return tid
    return None

def build_ctx(leagues: list[str], *, leagues_dir: Path = LEAGUES_DIR, cache_dir: Path = CACHE_DIR,
              aliases: dict | None = None) -> dict:
    """预装载全部四砖（一次性读盘，team_state_on 纯内存过滤）。
    xG/elo 双赛季档（2526+2627）合并后按日期升序：2526 档 hxg/axg 全 None（fd 旧季 CSV 无 xG 回填），
    单档偏好会让 xG 成死砖；且赛季初滚动窗须跨季取行，合并行统一走 team_state_on 的 as-of 过滤。
    三砖（xG/elo/DC）队名装载时归一为规范ID（fd 显示名经 _norm_team），不可映射行/键丢弃并计入 ctx["unmapped"][联赛]。"""
    if aliases is None:
        aliases = load_aliases()
    ctx = {"timeline": {}, "dc": {}, "elo": {}, "xg": {}, "unmapped": {}}
    for lg in leagues:
        raw_tl = _read(leagues_dir / f"{lg}_matches.json", [])
        ctx["timeline"][lg] = raw_tl.get("matches", []) if isinstance(raw_tl, dict) else raw_tl
        dropped = 0
        dc_raw = _read(cache_dir / f"{lg}_dc.json", {})
        dc_teams = {}
        for name, spec in (dc_raw.get("teams") or {}).items():
            tid = _norm_team(name, aliases)
            if tid is None:
                dropped += 1
            else:
                dc_teams[tid] = spec
        ctx["dc"][lg] = {**dc_raw, "teams": dc_teams}
        elo_files = [_read(cache_dir / f"elo_history_{lg}_{season}.json", {}) for season in ("2526", "2627")]
        elo_merged = sorted((r for f in elo_files for r in f.get("rows", [])),
                            key=lambda r: str(r.get("date", "")))
        elo_rows = []
        for r in elo_merged:
            h, a = _norm_team(r.get("home") or "", aliases), _norm_team(r.get("away") or "", aliases)
            if h is None or a is None:
                dropped += 1
            else:
                elo_rows.append({**r, "home": h, "away": a})
        hfa = next((f["hfa"] for f in reversed(elo_files) if f.get("hfa") is not None), None)
        ctx["elo"][lg] = {"hfa": hfa, "rows": elo_rows}
        xg_merged = [m for season in ("2526", "2627")
                     for m in _read(cache_dir / f"odds_{lg}_{season}.json", {}).get("matches", [])]
        xg_merged.sort(key=lambda m: _ddmmyyyy(m.get("date", "")) or date.min)
        xg_rows = []
        for m in xg_merged:
            h, a = _norm_team(m.get("home") or "", aliases), _norm_team(m.get("away") or "", aliases)
            if h is None or a is None:
                dropped += 1
            else:
                xg_rows.append({**m, "home": h, "away": a})
        ctx["xg"][lg] = xg_rows
        ctx["unmapped"][lg] = dropped
    return ctx

def team_state_on(team: str, as_of: date, ctx: dict, lag_days: int = LAG_DAYS) -> dict:
    """队的 as-of 快照：滚动xG(近N场)/联赛内Elo(最近pre值)/DC参数。降级记 flags（报告忠实度）。"""
    st = {"team": team, "league": None, "elo": None, "xg_att": None, "xg_def": None,
          "n_xg": 0, "dc_att": 0.0, "dc_def": 0.0, "flags": []}
    for lg, rows in ctx["timeline"].items():
        if any(team in (r.get("home"), r.get("away")) for r in as_of_rows(rows, as_of, lag_days)):
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
