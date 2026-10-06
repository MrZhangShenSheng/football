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
DC_ROLLING_ENV = 1.35   # 滚动代理联赛进球环境基线（Task12 裁定②·与 paper_strength._xg_z 缺省同源）
ROLLING_SHRINK_K = 5    # 预注册舱 v2 hyperparamsFixed.rollingShrinkK（小样本收缩常数）
OPPONENT_ADJ_K = 0.5    # 预注册舱 v3 hyperparamsFixed.opponentAdjK（对手强度调整系数·保守半额）
# v10 硬仗口径：国家队实力分可信场源（预选赛库剔除=虐鱼污染清洗·2026-10-06 大哥拍板"洗"）
HARD_EXCLUDE = ("world-cup-qual", "euro-qual")   # v10 硬仗口径剔除的预选赛库
DC_ROLLING_N = 10       # 滚动代理窗口=近10场可见赛

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
    """fd/ESPN 显示名 → 规范ID：identity 直通（已是规范ID不动）·kebab 命中优先，espn 别名兜底；None=不可映射（行/键丢弃）。"""
    if name in aliases:
        return name
    kebab = name.lower().replace(" ", "-").replace("'", "")
    if kebab in aliases:
        return kebab
    for tid, srcs in aliases.items():
        if srcs.get("espn") and srcs["espn"].lower() == name.lower():
            return tid
    for tid, srcs in aliases.items():     # fd 字段：fd CSV 缩写名（'Milan'/'Ath Madrid'·A2修复122行xG丢失）
        if srcs.get("fd") and srcs["fd"].lower() == name.lower():
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
        # v11 阶段2（止损回滚·run7/7b实证）：GLOBAL全库Elo双版本均恶化（0.282/0.260 vs 基线0.196）
        # ——库内独立init跨库不可比+国家队库155场Elo区分度弱=噪声。回滚为纯当季档。
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

def _opp_rolling_strength(team: str, day, ctx: dict) -> tuple[float, float]:
    """对手 Y 截至 day 的未修正 rolling (att, def)（B调整一层近似·day−lag 可见场·无递归）。"""
    d = date.fromisoformat(day) if isinstance(day, str) else day
    sc, cc = [], []
    for lg, rows in ctx["timeline"].items():
        for r in as_of_rows(rows, d, LAG_DAYS):
            if not isinstance(r.get("hg"), (int, float)):
                continue
            if r.get("home") == team:
                sc.append(r["hg"]); cc.append(r["ag"])
            elif r.get("away") == team:
                sc.append(r["ag"]); cc.append(r["hg"])
    sc, cc = sc[-DC_ROLLING_N:], cc[-DC_ROLLING_N:]
    if not sc:
        return 0.0, 0.0
    w = len(sc) / (len(sc) + ROLLING_SHRINK_K)
    return ((sum(sc) / len(sc) - DC_ROLLING_ENV) * w,
            (sum(cc) / len(cc) - DC_ROLLING_ENV) * w)

def team_state_on(team: str, as_of: date, ctx: dict, lag_days: int = LAG_DAYS,
                  dc_rolling: bool = False, hard_only: bool = False) -> dict:
    """队的 as-of 快照：滚动xG(近N场)/联赛内Elo(最近pre值)/DC参数。降级记 flags（报告忠实度）。
    dc_rolling=True（门1评估器模式·Task12 裁定②）：dc_att/dc_def 改用 as-of 可见近≤10场的
    场均进/失滚动代理（(场均进−1.35)与(场均失−1.35)·DC字段口径 def 负=强防），完全不读
    ctx["dc"] 当前缓存——当前缓存是全历史拟合，历史 as-of 场用了会泄漏未来赛果；flags 记
    'dc_source:rolling'。默认 False 保持当前缓存行为不变。"""
    st = {"team": team, "league": None, "elo": None, "xg_att": None, "xg_def": None,
          "n_xg": 0, "dc_att": 0.0, "dc_def": 0.0, "flags": []}
    # 跨库合并（2026-10-05 修复2）：一队多赛事（france 分布欧国联/世预赛/世界杯/欧预赛），
    # 收集全部库的 as-of 可见场次；主联赛=场次最多的库（供联赛级参数查找）。旧版锁单库丢35~77%数据。
    lg_counts, team_rows = {}, []
    for lg, rows in ctx["timeline"].items():
        if hard_only and lg in ("world-cup-qual", "euro-qual"):
            continue                               # v10 虐鱼清洗：预选赛库剔除·其余库照收
        mine = [r for r in as_of_rows(rows, as_of, lag_days)
                if team in (r.get("home"), r.get("away"))]
        if mine:
            lg_counts[lg] = len(mine)
            team_rows.extend(mine)
    if not lg_counts:
        st["flags"].append("no_league")
        return st
    st["league"] = max(lg_counts, key=lg_counts.get)
    lg = st["league"]
    dc_team = (ctx["dc"].get(lg, {}).get("teams") or {}).get(team)
    if not dc_rolling and dc_team:
        st["dc_att"], st["dc_def"] = float(dc_team["attack"]), float(dc_team["defense"])
    else:
        # rolling 代理：评估器防泄漏模式，或该队无 DC 缓存条目（如 uefa-nations 国家队库）的
        # 生产回退——缓存优先，缺失回退（2026-10-05 欧国联实弹暴露 no_dc 中性伪预测后修复）
        # 扫描范围=跨库合并的全部 as-of 可见场（按日期排序后取近10）
        scored, conceded = [], []
        adj_rows = []                                   # (进, 失, 对手, 场日期) 供B对手调整
        for r in sorted(team_rows, key=lambda r: str(r.get("date", ""))):
            hg, ag = r.get("hg"), r.get("ag")
            if not isinstance(hg, (int, float)) or not isinstance(ag, (int, float)):
                continue
            if r.get("home") == team:
                opp = r.get("away")
                scored.append(hg); conceded.append(ag)
            elif r.get("away") == team:
                opp = r.get("home")
                scored.append(ag); conceded.append(hg)
            else:
                continue
            adj_rows.append((hg, ag, opp, str(r.get("date", ""))[:10], r.get("home") == team))
        adj_rows = adj_rows[-DC_ROLLING_N:]
        scored, conceded = scored[-DC_ROLLING_N:], conceded[-DC_ROLLING_N:]
        if scored:
            # B 对手强度调整（预注册舱v3·opponentAdjK=0.5）：进球按对手烂防打折/失球按对手强攻豁免
            # ——一层近似（对手强度用其未修正rolling值·不递归）。方向：adj_goal=goal−κ·opp_def、
            # adj_conc=conc−κ·opp_att（opp_def正=烂防·opp_att正=强攻）
            adj_goals, adj_concs = [], []
            for hg, ag, opp, d_str, is_home in adj_rows:
                goal, conc = (hg, ag) if is_home else (ag, hg)
                opp_att, opp_def = _opp_rolling_strength(opp, d_str, ctx)
                adj_goals.append(goal - OPPONENT_ADJ_K * opp_def)
                adj_concs.append(conc - OPPONENT_ADJ_K * opp_att)
            # 缺陷③修复(预注册舱v2·rollingShrinkK=5)：小样本向联赛均值(0)收缩 ×n/(n+K)——
            # 2~9场国家队/世预赛虐鱼均值不再全额兑现（罗马尼亚λ6.36级爆炸根治·run2实证必要）
            w_shrink = len(scored) / (len(scored) + ROLLING_SHRINK_K)
            st["dc_att"] = (sum(adj_goals) / len(adj_goals) - DC_ROLLING_ENV) * w_shrink     # 攻强=对手调整后场均进−环境
            st["dc_def"] = (sum(adj_concs) / len(adj_concs) - DC_ROLLING_ENV) * w_shrink     # DC字段：场均失−环境（负=强防）
            st["flags"].append("dc_source:rolling")
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
