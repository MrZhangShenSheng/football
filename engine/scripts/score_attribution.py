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
from attribution_dims import (lambda_from_had, dim_elo, dim_stage, dim_derby,
                              dim_htft, dim_injury_coarse, classify_script)

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
    # 真实数据 10 场 odds 为空 {}（欧冠/欧罗巴杯赛+拜仁vs莱红牛）：缺三键记 None，
    # 由 build_bill 走 error bill（baseline_or_result_missing）降级路径，禁 KeyError 崩溃。
    had = [float(o[k]) for k in ("h", "d", "a") if k in o] if o else []
    return {
        "date": date_str, "code": code, "league": m.get("league"),
        "match": m["match"], "team_h": team_h, "team_a": team_a,
        "league_dir": league_dir,
        "odds": had if len(had) == 3 else None,
        "pin_close": m.get("pinClose") or None,
        "elo_h": prof_h.get("elo"), "elo_a": prof_a.get("elo"),
        "inj_n_h": inj_h, "inj_n_a": inj_a,
        "rest_days_h": None, "rest_days_a": None,   # Task 5 填
        "result": {"hg": hg, "ag": ag, "ht_hg": ht_hg, "ht_ag": ht_ag},
    }

def fill_rest_days(rec: dict) -> dict:
    """R3 休息差：league 库查两队上一场日期→天数。查不到保持 None。开发者 sszhang"""
    for side, team in (("h", rec["team_h"]), ("a", rec["team_a"])):
        last = _last_match_date(team, rec["league_dir"], rec["date"])
        if last:
            d0 = datetime.fromisoformat(rec["date"]).date()
            rec[f"rest_days_{side}"] = (d0 - datetime.fromisoformat(last).date()).days
    return rec


# 账单组装（Task 7）：常量+league_avg_total+build_bill
DEEP_THRESHOLD = 1.5   # |实际-基线|总偏移≥此值=深挖档（设计§一深度分档）
EXPLAIN_FLOOR = 0.5    # explained_pct<此值且 deep → insufficient（防谬误铁律3）
LEAGUE_AVG_DEFAULT = 2.6  # 画像读不到时的兜底场均总球（evidence 标 default）

def league_avg_total(league: str) -> tuple[float, str]:
    """00-leagues 画像 standings 算场均总球（Σgf/Σplayed×2，两队合计口径）；读不到用默认。开发者 sszhang"""
    path = ROOT / "data" / "00-leagues" / f"{LEAGUE_DIR_MAP.get(league, '')}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        gf = sum(s.get("gf", 0) for s in data.get("standings", []))
        played = sum(s.get("played", 0) for s in data.get("standings", []))
        if gf and played:
            return round(2 * gf / played, 2), "league_profile"
    except Exception:
        pass
    return LEAGUE_AVG_DEFAULT, "default"

def build_bill(rec: dict) -> dict:
    """双栏账单组装（schema 见 impl §账单冻结版）。开发者 sszhang"""
    lam_sum, src = league_avg_total(rec["league"])
    lam = lambda_from_had(rec["odds"], lam_sum) if rec.get("odds") else None
    r = rec["result"]
    if lam is None or r["hg"] is None:
        return {"code": rec["code"], "match": rec["match"], "error": "baseline_or_result_missing"}
    lam_h, lam_a = lam

    offs = [o for o in (dim_elo(rec["elo_h"], rec["elo_a"]),
                        dim_htft(rec)) if o and o["h"] is not None]
    shapes = [s for s in (dim_stage(rec), dim_derby(rec["league"], rec["team_h"], rec["team_a"]))
              if s]
    coarse = dim_injury_coarse(rec)  # 标记型：进 coverage 不进 offsets

    sum_h = lam_h + sum(o["h"] for o in offs)
    sum_a = lam_a + sum(o["a"] for o in offs)
    resid_h, resid_a = r["hg"] - sum_h, r["ag"] - sum_a
    dev = abs(r["hg"] - lam_h) + abs(r["ag"] - lam_a)
    depth = "deep" if dev >= DEEP_THRESHOLD else "light"
    denom = max(dev, 0.01)
    explained = 1.0 - min(abs(resid_h) + abs(resid_a), denom) / denom
    flag = "insufficient" if (depth == "deep" and explained < EXPLAIN_FLOOR) else "ok"

    covered = ["L1"] + (["A1"] if rec["elo_h"] is not None else []) + \
              ["L0", "E2.htft" if r["ht_hg"] is not None else None, "R3",
               "derby" if any(s["dim"] == "derby" for s in shapes) else None] + \
              (["R1.coarse"] if coarse else [])
    return {
        "date": rec["date"], "code": rec["code"], "league": rec["league"],
        "match": rec["match"],
        "result": r, "depth": depth,
        "baseline": {"lam_h": lam_h, "lam_a": lam_a,
                     "source": f"avg_{src}+had_devig"},
        "offsets": offs, "shape_factors": shapes,
        "reconstructed": {"hg": round(sum_h, 3), "ag": round(sum_a, 3)},
        "residual": {"hg": round(resid_h, 3), "ag": round(resid_a, 3)},
        "explained_pct": round(explained, 3), "explain_flag": flag,
        "coverage": [c for c in covered if c],
        "missing": ["A2.xg", "A4", "E1.red", "E3", "E4.gk", "E5.finishing",
                    "weather", "referee", "R2"] +
                   ([] if rec["elo_h"] is not None else ["A1"]) +
                   ([] if r["ht_hg"] is not None else ["E2.htft"]),
        "script": classify_script(rec),
    }


def _all_codes(date_str: str) -> list[str]:
    path = RESULTS_DIR / f"{date_str}.json"
    if not path.exists():
        return []
    return [m["code"] for m in json.loads(path.read_text(encoding="utf-8")).get("matches", [])]

def main() -> None:
    """CLI：single/batch。开发者 sszhang"""
    import argparse
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("single"); p1.add_argument("date"); p1.add_argument("code")
    p2 = sub.add_parser("batch");  p2.add_argument("from_date"); p2.add_argument("to_date")
    args = ap.parse_args()

    out_dir = ROOT / "data" / "04-summaries" / "score-attribution"
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.cmd == "single":
        rec = fill_rest_days(load_match_record(args.date, args.code))
        print(json.dumps(build_bill(rec), ensure_ascii=False, indent=2))
        return
    d0, d1 = args.from_date, args.to_date
    cur, bills = d0, []
    from datetime import timedelta
    while cur <= d1:
        for code in _all_codes(cur):
            rec = load_match_record(cur, code)
            if rec is None:
                continue
            bill = build_bill(fill_rest_days(rec))
            if "error" in bill:   # 缺基线场打印禁静默
                print(f"[缺基线] {cur} {code} {rec.get('match', '')}: {bill['error']}")
            bills.append(bill)
        cur = (datetime.fromisoformat(cur) + timedelta(days=1)).date().isoformat()
    path = out_dir / f"{d0}~{d1}-attribution.json"
    path.write_text(json.dumps(bills, ensure_ascii=False, indent=2), encoding="utf-8")
    deep = sum(1 for b in bills if b.get("depth") == "deep")
    insuf = sum(1 for b in bills if b.get("explain_flag") == "insufficient")
    print(f"账单 {len(bills)} 张落 {path}（deep {deep}·解释力不足 {insuf}·缺基线 {len(bills)-len([b for b in bills if 'baseline' in b])}）")

if __name__ == "__main__":
    main()
