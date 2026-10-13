# engine/scripts/tail_dataset.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tail_dataset.py — 右尾肥尾场次数据集构建器（spec docs/2026-10-13-tail-model-design.html）。

防泄漏三道闸：as_of=开赛前一日；league/hst 时间线 lag=2 天（elo 赛前值 lag=0）；
特征市场价只用 crsOpen——ttg/had 封盘只作判据对手盘，禁入特征。
判据预注册 engine/cache/tail-model-prereg.json（跑前冻结·铁律14②）。
开发者 sszhang"""
import json
import math
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import market_lambda as mkl

TG_BIN_MAX = 7                 # tgBin = min(h+a, 7)，7 档=7+球
MIN_POOL_ITEMS = 20            # 与 backtest_crs_batch 同源
LEAK_LAG_DAYS = 2              # league/hst 时间线滞后（铁律14①语义）
DECISION_DAY_OFFSET = -1       # 决策时点=开赛前一日
FEATURE_VERSION = "tail-v1"    # 特征清单版本（meta.json 冻结用）


def label_skeleton(score: str) -> dict:
    """比分 → 骨架三轴标签。cleanSheet：0=客零封 1=主零封 2=无（0:0 归无·口径冻结 prereg）。
    开发者 sszhang"""
    h, a = (int(x) for x in score.split(":"))
    tg = h + a
    if h > a:
        direction, cs = 0, (1 if a == 0 else 2)
    elif h == a:
        direction, cs = 1, 2
    else:
        direction, cs = 2, (0 if h == 0 else 2)
    return {"tg": tg, "tgBin": min(tg, TG_BIN_MAX), "direction": direction, "cleanSheet": cs}


def market_anchor_features(row: dict) -> dict:
    """crsOpen 全套派生 11 维（唯一市场特征源·开盘口径=决策时点合法）。

    三向聚合含 other 归侧；tg 矩/肥尾质量只计精确键（other 不摊）；熵=全分布含 other 原子（口径冻结）。"""
    pool = row.get("crsOpen") or {}
    if len(pool) < MIN_POOL_ITEMS:
        return {}
    p = mkl.crs_power_devig(pool)
    if not p:
        return {}
    exact, p_home, p_draw, p_away, p_other = [], 0.0, 0.0, 0.0, 0.0
    ent = 0.0
    for k, v in p.items():
        if k in ("s1sh", "s1sd", "s1sa"):
            p_other += v
            if k == "s1sh":
                p_home += v
            elif k == "s1sd":
                p_draw += v
            else:
                p_away += v
        elif k.startswith("s") and len(k) == 6 and k[3] == "s":
            h, a = int(k[1:3]), int(k[4:6])
            exact.append((h, a, v))
            if h > a:
                p_home += v
            elif h == a:
                p_draw += v
            else:
                p_away += v
        if v > 0:
            ent -= v * math.log(v)
    if not exact:
        return {}
    e_tg = sum((h + a) * v for h, a, v in exact)
    p_ge4 = sum(v for h, a, v in exact if h + a >= 4)
    p_ge5 = sum(v for h, a, v in exact if h + a >= 5)
    p_diff3 = sum(v for h, a, v in exact if abs(h - a) >= 3)
    p_away_big = sum(v for h, a, v in exact if h == 0 and a >= 3)
    return {
        "mkt_e_tg_open": e_tg,
        "mkt_p_tg_ge4_open": p_ge4,
        "mkt_p_tg_ge5_open": p_ge5,
        "mkt_p_diff_ge3_open": p_diff3,
        "mkt_p_away_cs_big_open": p_away_big,
        "mkt_p_home_open": p_home,
        "mkt_p_draw_open": p_draw,
        "mkt_p_away_open": p_away,
        "mkt_p_max_side_open": max(p_home, p_draw, p_away),
        "mkt_entropy_open": ent,
        "mkt_other_mass_open": p_other,
    }


# ── league/hst 时间线索引（lag 内建·bisect 查窗）────────────────────────────
import bisect

STATS_WINDOWS = (10, 38)
MIN_WINDOW_N = 5
TEAM_STAT_KEYS = ("gf", "ga", "tg4", "blow", "clean", "beCleaned")


class LeagueIndex:
    """league 库（或 hst 缓存）→ per-team 按日期索引；window() 内建 LEAK_LAG_DAYS 截断。

    gf_field/ga_field 参数化：league 库用 hg/ag（进球），hst 缓存用 hst/ast（射正）。"""

    def __init__(self, league_dir: Path, gf_field: str = "hg", ga_field: str = "ag"):
        self._by_team: dict[str, dict] = {}
        loaded_rows = []
        for f in sorted(Path(league_dir).glob("*_matches.json")):
            doc = json.loads(f.read_text(encoding="utf-8"))
            rows = doc if isinstance(doc, list) else (doc.get("rows") or [])
            for r in rows:
                d = str(r.get("date") or "")
                gf, ga = r.get(gf_field), r.get(ga_field)
                if not d or gf is None or ga is None:
                    continue
                loaded_rows.append({"date": d, "home": r.get("home"), "away": r.get("away"),
                                    "hg": gf, "ag": ga})
        self._ingest_rows(self, loaded_rows)

    @classmethod
    def _ingest_rows(cls, idx: "LeagueIndex", rows: list[dict]) -> None:
        """内存行装载（hst/league 复用；见 build_hst_index）。"""
        for r in rows:
            d = str(r.get("date") or "")
            gf, ga = r.get("hg"), r.get("ag")
            if not d or gf is None or ga is None:
                continue
            for team, gfor, gaga, is_home in ((r.get("home"), gf, ga, True),
                                              (r.get("away"), ga, gf, False)):
                slot = idx._by_team.setdefault(team, {"dates": [], "rows": []})
                slot["dates"].append(d)
                slot["rows"].append({"date": d, "gf": int(gfor), "ga": int(gaga),
                                     "isHome": is_home})
        for slot in idx._by_team.values():
            order = sorted(range(len(slot["dates"])), key=lambda i: slot["dates"][i])
            slot["dates"] = [slot["dates"][i] for i in order]
            slot["rows"] = [slot["rows"][i] for i in order]

    def window(self, team: str, as_of: str, n: int) -> list[dict]:
        """as_of（含）前 LEAK_LAG_DAYS 天内可见的最近 n 场（铁律14①语义）。"""
        cutoff = (date.fromisoformat(as_of) - timedelta(days=LEAK_LAG_DAYS)).isoformat()
        slot = self._by_team.get(team)
        if not slot:
            return []
        hi = bisect.bisect_right(slot["dates"], cutoff)
        return slot["rows"][max(0, hi - n):hi]

    def last_date_before(self, team: str, as_of: str) -> str | None:
        """最近一场日期（rest 天数用；同 lag 截断）。"""
        w = self.window(team, as_of, 1)
        return w[-1]["date"] if w else None


def _window_stats(matches: list[dict]) -> dict | None:
    n = len(matches)
    if n < MIN_WINDOW_N:
        return None
    tg4 = sum(1 for m in matches if m["gf"] + m["ga"] >= 4)
    blow = sum(1 for m in matches if abs(m["gf"] - m["ga"]) >= 3)
    clean = sum(1 for m in matches if m["ga"] == 0)
    be_cleaned = sum(1 for m in matches if m["gf"] == 0)
    return {"gf": sum(m["gf"] for m in matches) / n,
            "ga": sum(m["ga"] for m in matches) / n,
            "tg4": tg4 / n, "blow": blow / n,
            "clean": clean / n, "beCleaned": be_cleaned / n}


def team_asof_features(idx: LeagueIndex, home_id: str | None, away_id: str | None,
                       as_of: str) -> dict:
    """两队近10/38场 6统计×(主/客/差) + rest 2维 = 38 维。任一队窗不足→该窗段全 None
    （训练侧 train-mean 填充+nNullTeam 计数·prereg features.impute）。开发者 sszhang"""
    out: dict[str, float | None] = {}
    stats = {}
    for side, team in (("h", home_id), ("a", away_id)):
        stats[side] = {}
        for w in STATS_WINDOWS:
            stats[side][w] = _window_stats(idx.window(team, as_of, w)) if team else None
    for w in STATS_WINDOWS:
        for k in TEAM_STAT_KEYS:
            hs, as_ = stats["h"][w], stats["a"][w]
            hv = hs[k] if hs else None
            av = as_[k] if as_ else None
            out[f"h_{k}_w{w}"] = hv
            out[f"a_{k}_w{w}"] = av
            out[f"d_{k}_w{w}"] = (hv - av) if (hv is not None and av is not None) else None
    for side, team in (("h", home_id), ("a", away_id)):
        last = idx.last_date_before(team, as_of) if team else None
        out[f"rest_{side}"] = ((date.fromisoformat(as_of) - date.fromisoformat(last)).days
                               if last else None)
    return out


# ── hst / elo / 上下文 ────────────────────────────────────────────────────
HST_WINDOW = 20
HST_KEY = "hst_{lg}.json"
ELO_GLOB = "elo_history_*_GLOBAL.json"
# hist 中文名 → 库 key（模板特征路由；未收录→模板 None 诚实降级）
TAIL_LEAGUE_MAP = {
    "英超": "england-premier", "英冠": "england-championship", "西甲": "spain-laliga",
    "西乙": "spain-liga2", "意甲": "italy-serie-a", "意乙": "italy-serie-b",
    "德甲": "germany-bundesliga", "德乙": "germany-bundesliga2", "法甲": "france-ligue1",
    "法乙": "france-ligue2", "荷甲": "netherlands-eredivisie", "葡超": "portugal-primeira",
    "土超": "turkey-super-lig", "希超": "greece-super", "比甲": "belgium-first-a",
    "日职": "japan", "韩职": "korea", "美职": "usa", "挪超": "norway", "瑞超": "sweden",
    "丹超": "denmark", "沙特": "saudi", "巴西": "brazil", "欧冠": "uefa-champions",
    "欧联": "uefa-europa", "欧国联": "uefa-nations", "世预赛": "world-cup-qual",
    "世界杯": "world-cup", "解放者杯": "libertadores",
    # 亚冠精英无对应库（ACL Elite 未建库）——不映射=模板 None 诚实降级，禁止错挂 asian-u23（review MEDIUM 裁决）
}


def build_hst_index(cache_dir: Path) -> LeagueIndex:
    """hst_{lg}.json 族 → LeagueIndex（gf=射正 hst·ga=被射正 ast）。

    文件名 pattern 与 LeagueIndex 的 *_matches.json glob 不同——重命名临时副本不可取，
    改为直接扫 hst_*.json 装载（复用 LeagueIndex 的行装载逻辑，注入文件清单）。"""
    files = sorted(Path(cache_dir).glob("hst_*.json"))
    rows = []
    for f in files:
        doc = json.loads(f.read_text(encoding="utf-8"))
        for r in doc.get("rows") or []:
            d = str(r.get("date") or "")
            if d and r.get("hst") is not None and r.get("ast") is not None:
                rows.append({"date": d, "home": r.get("home"), "away": r.get("away"),
                             "hg": r.get("hst"), "ag": r.get("ast")})
    return _LeagueIndexFromRows(rows)


def _LeagueIndexFromRows(rows: list[dict]) -> LeagueIndex:
    """由内存行构造 LeagueIndex（绕过文件 glob；hst 装载用）。"""
    idx = LeagueIndex.__new__(LeagueIndex)
    idx._by_team = {}
    LeagueIndex._ingest_rows(idx, rows)
    return idx


class EloIndex:
    """elo_history_*_GLOBAL.json → per-team (dates, elo_pre)。赛前值不含该场赛果
    （strength_loaders 口径注释：lag=0 无答案泄漏）。"""

    def __init__(self, rows: list[dict]):
        self._by_team: dict[str, dict] = {}
        for r in rows:
            d = str(r.get("date") or "")
            if not d:
                continue
            for team, elo in ((r.get("home"), r.get("elo_home_pre")),
                              (r.get("away"), r.get("elo_away_pre"))):
                if team and elo is not None:
                    slot = self._by_team.setdefault(team, {"dates": [], "elos": []})
                    slot["dates"].append(d)
                    slot["elos"].append(float(elo))
        for slot in self._by_team.values():
            order = sorted(range(len(slot["dates"])), key=lambda i: slot["dates"][i])
            slot["dates"] = [slot["dates"][i] for i in order]
            slot["elos"] = [slot["elos"][i] for i in order]

    def elo(self, team: str, as_of: str) -> float | None:
        slot = self._by_team.get(team)
        if not slot:
            return None
        hi = bisect.bisect_right(slot["dates"], as_of)
        return slot["elos"][hi - 1] if hi > 0 else None


def build_elo_index(cache_dir: Path) -> EloIndex:
    rows = []
    for f in sorted(Path(cache_dir).glob(ELO_GLOB)):
        doc = json.loads(f.read_text(encoding="utf-8"))
        rows.extend(doc.get("rows") or [])
    return EloIndex(rows)


def ctx_features(lg_idx: LeagueIndex, league_zh: str, as_of: str) -> dict:
    """联赛 as-of 模板 3 维（大球率/屠杀率/客零封大胜率·全库行无 league 维故用
    TAIL_LEAGUE_MAP 路由到主库口径=该库全部场次聚合·as_of 前可见）+ 月份 sin/cos 2 维。

    模板聚合走 LeagueIndex 全队窗口代价高——直接对库文件按日期重扫（轻量·一次性/场）。
    库文件行在 matches 键下（strength_loaders 同口径）。开发者 sszhang"""
    out: dict[str, float | None] = {}
    lg = TAIL_LEAGUE_MAP.get(league_zh)
    if lg:
        f = ROOT / "data" / "02-results" / "league" / f"{lg}_matches.json"
        if f.exists():
            doc = json.loads(f.read_text(encoding="utf-8"))
            rows = doc if isinstance(doc, list) else (doc.get("matches") or [])
            cutoff = (date.fromisoformat(as_of) - timedelta(days=LEAK_LAG_DAYS)).isoformat()
            visible = [r for r in rows if str(r.get("date") or "") <= cutoff
                       and r.get("hg") is not None and r.get("ag") is not None]
            if visible:
                n = len(visible)
                out["lg_p_tg4"] = sum(1 for r in visible if int(r["hg"]) + int(r["ag"]) >= 4) / n
                out["lg_p_blow"] = sum(1 for r in visible
                                       if abs(int(r["hg"]) - int(r["ag"])) >= 3) / n
                out["lg_p_away_big"] = sum(1 for r in visible
                                           if int(r["hg"]) == 0 and int(r["ag"]) >= 3) / n
    out.setdefault("lg_p_tg4", None)
    out.setdefault("lg_p_blow", None)
    out.setdefault("lg_p_away_big", None)
    m = date.fromisoformat(as_of).month
    angle = 2 * math.pi * (m - 1) / 12
    out["ctx_month_sin"] = math.sin(angle)
    out["ctx_month_cos"] = math.cos(angle)
    return out


def hst_elo_features(hst_idx: LeagueIndex, elo_idx: EloIndex,
                     home_id: str | None, away_id: str | None, as_of: str) -> dict:
    """hst 近 HST_WINDOW 场场均射正 for/against（主客各2维）+ elo_diff = 5 维。"""
    out: dict[str, float | None] = {}
    for side, team in (("h", home_id), ("a", away_id)):
        ms = hst_idx.window(team, as_of, HST_WINDOW) if team else []
        if len(ms) >= MIN_WINDOW_N:
            out[f"{side}_hst_for"] = sum(m["gf"] for m in ms) / len(ms)
            out[f"{side}_hst_against"] = sum(m["ga"] for m in ms) / len(ms)
        else:
            out[f"{side}_hst_for"] = None
            out[f"{side}_hst_against"] = None
    eh = elo_idx.elo(home_id, as_of) if home_id else None
    ea = elo_idx.elo(away_id, as_of) if away_id else None
    out["elo_diff"] = (eh - ea) if (eh is not None and ea is not None) else None
    return out
