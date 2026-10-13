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
import re
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
            # 真实库两态：33 文件 dict(matches 键) + asian-u23 list 直存——与 ctx_features /
            # 生产 strength_loaders 同口径（rows 键会静默空读 33 文件=38 维全 None 假数据集）
            rows = doc if isinstance(doc, list) else (doc.get("matches") or [])
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


# ── 组装与落盘（Task 5）────────────────────────────────────────────────────
import hashlib
import strength_loaders as sl
from backtest_crs_batch import load_hist_window


def _zh_alias_maps():
    aliases = sl.load_aliases()
    return sl.zh_to_id(aliases), aliases


def build_rows(date_lo: str, date_hi: str, *, hist_dir: Path | None = None,
               league_dir: Path | None = None, cache_dir: Path | None = None
               ) -> tuple[list[dict], dict]:
    """hist 窗口 → 标签+四族特征行。映射失败队→homeId/awayId=None+球队特征全 None
    （featMask 语义·诚实降级不剔除）。开发者 sszhang"""
    lg_dir = league_dir or (ROOT / "data" / "02-results" / "league")
    c_dir = cache_dir or (ROOT / "engine" / "cache")
    lg_idx = LeagueIndex(lg_dir)
    hst_idx = build_hst_index(c_dir)
    elo_idx = build_elo_index(c_dir)
    zh, aliases = _zh_alias_maps()

    def one(name: str):
        return zh.get(name) or sl._norm_team(name, aliases)

    out, mapped, total = [], 0, 0
    for row in load_hist_window(date_lo, date_hi, hist_dir=hist_dir):
        if not re.fullmatch(r"\d+:\d+", str(row["score"] or "")):
            continue    # 无效场次（体彩取消·全库59行）无标签可产——跳过（13574-59=13515 口径）
        hid, aid = one(row["home"]), one(row["away"])
        total += 1
        if hid and aid:
            mapped += 1
        as_of = (date.fromisoformat(row["date"])
                 + timedelta(days=DECISION_DAY_OFFSET)).isoformat()
        feats: dict[str, float | None] = {}
        feats.update(market_anchor_features(row))          # 池不足→空dict（11维缺失）
        team = team_asof_features(lg_idx, hid, aid, as_of)
        feats.update(team)
        feats.update(hst_elo_features(hst_idx, elo_idx, hid, aid, as_of))
        feats.update(ctx_features(lg_idx, row["league"], as_of))
        n_team = sum(1 for v in team.values() if v is None)
        n_other = sum(1 for k in (*feats,)
                      if k.startswith(("mkt_", "h_", "a_", "d_", "rest_", "lg_"))
                      or k.endswith(("_hst_for", "_hst_against")) or k == "elo_diff")
        n_other = n_other - n_team if n_other > n_team else 0
        feats["nNullTeam"] = n_team
        feats["nNullOther"] = n_other
        # ttg/had/crs 顶层快照：tail_train.market_event_probs 对拍消费（封盘价=市场对手盘）
        # 计数键双挂：features 内（61 维特征清单冻结）+ 行顶层（brief 接口定义·Task 6 消费）
        out.append({"date": row["date"], "code": row["code"], "league": row["league"],
                    "homeId": hid, "awayId": aid, "score": row["score"],
                    "ttg": row["ttg"], "had": row["had"], "crs": row["crs"],
                    "label": label_skeleton(row["score"]),
                    "nNullTeam": n_team, "nNullOther": n_other, "features": feats})
    return out, {"count": len(out), "mappedRate": round(mapped / total, 4) if total else None}


def _feature_names_ordered(sample: dict) -> list[str]:
    """键序冻结：市场锚(11)→球队(38)→hst/elo(5)→ctx(5)→计数(2)——由插入序保证。"""
    return list(sample.keys())


def write_dataset(rows: list[dict], out_dir: Path) -> dict:
    """dataset.jsonl + meta.json（fingerprint=行规范化 sha256 前16位·幂等重建可 diff）。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    names = _feature_names_ordered(rows[0]["features"]) if rows else []
    lines = []
    for r in rows:
        r2 = dict(r)
        r2["features"] = {k: r["features"].get(k) for k in names}
        lines.append(json.dumps(r2, ensure_ascii=False, sort_keys=True))
    blob = "\n".join(lines)
    meta = {"version": FEATURE_VERSION, "count": len(rows),
            "dateRange": [rows[0]["date"], rows[-1]["date"]] if rows else None,
            "featureNames": names, "nFeatures": len(names),
            "fingerprint": hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]}
    (out_dir / "dataset.jsonl").write_text(blob + "\n", encoding="utf-8")
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    return meta


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="右尾肥尾场次数据集构建器")
    ap.add_argument("cmd", choices=["build"])
    ap.add_argument("--from", dest="date_lo", default="2023-09-01")
    ap.add_argument("--to", dest="date_hi", default="2026-09-28")
    ap.add_argument("--out", default=str(ROOT / "engine" / "cache" / "tail_dataset"))
    args = ap.parse_args()
    rows, stat = build_rows(args.date_lo, args.date_hi)
    meta = write_dataset(rows, Path(args.out))
    print(json.dumps({**meta, "mappedRate": stat["mappedRate"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
