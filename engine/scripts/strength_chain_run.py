# engine/scripts/strength_chain_run.py
# -*- coding: utf-8 -*-
"""实力链日运行器：串联①-⑥·逐步落盘·幂等（文件在即跳过，--force 才重写）。开发者 sszhang。
用法：python engine/scripts/strength_chain_run.py [YYYY-MM-DD]"""
from __future__ import annotations
import argparse, json, sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import strength_loaders as sl
import paper_strength as ps
import lambda_bridge as lb
import score_matrix as sm
import pick_logic as pk

OUT_ROOT = Path(__file__).resolve().parents[2] / "engine" / "cache" / "strength_chain"
LEAGUES = ["england-premier", "spain-laliga", "germany-bundesliga", "italy-serie-a",
           "france-ligue1", "netherlands-eredivisie", "portugal-primeira", "korea",
           "japan", "denmark", "sweden", "norway", "brazil", "saudi", "usa"]
ENV_GOALS = {"default": 2.7}     # 联赛进球环境基线（②c offset 之前）

def _atomic_write(p: Path, obj) -> None:
    import os
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)

def run_day(day: str, ctx: dict, out_dir: Path, *, feed: list | None = None, beta: float = 0.05,
            now_iso: str = "", aliases_file: Path | None = None, force: bool = False) -> dict:
    """串联六步。feed=当日在售场（缺省从 sporttery_matches.json 读·测试注入）。幂等：summary 在即返回。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "summary.json"
    if summary_path.exists() and not force:
        return json.loads(summary_path.read_text(encoding="utf-8"))
    as_of = date.fromisoformat(day)
    if feed is None:
        feed = _feed_from_cache(aliases_file)
    zh2id = sl.zh_to_id() if aliases_file is None else sl.zh_to_id(
        json.loads(Path(aliases_file).read_text(encoding="utf-8")))
    # ①② 状态与去水
    states = {}
    for m in feed:
        for side in ("home", "away"):
            tid = zh2id.get(m[side], m[side])
            if tid not in states:
                st = sl.team_state_on(tid, as_of, ctx)
                att, df = ps.devig_fame(st)
                states[tid] = {"state": st, "att": att, "def": df}
    # ③④⑤
    compares, lam_out, matrices = {}, {}, {}
    for m in feed:
        hid, aid = zh2id.get(m["home"], m["home"]), zh2id.get(m["away"], m["away"])
        if hid not in states or aid not in states:
            continue
        lg = states[hid]["state"].get("league") or "default"
        env = ENV_GOALS.get(lg, ENV_GOALS["default"])
        home_adv = float((ctx["dc"].get(lg) or {}).get("homeAdv", 0.30))   # 联赛真实主场优势·缺DC才回退0.30
        hfa_v = ps.hfa_value(home_adv, states[hid]["state"]["n_xg"], None)
        cmp_out = ps.compare(states[hid]["att"], states[hid]["def"],
                             states[aid]["att"], states[aid]["def"], hfa_v, env)
        key = str(m.get("matchId", f"{hid}-{aid}"))
        compares[key] = {**cmp_out, "home": hid, "away": aid}
        lam = lb.lambdas(cmp_out, inj_h=None, inj_a=None, beta=beta)   # 伤停数据接入前中性
        lam_out[key] = lam
        rho = (ctx["dc"].get(lg) or {}).get("rho", -0.05)
        matrices[key] = sm.dc_matrix(lam["lam_h"], lam["lam_a"], rho)
    # ⑥ 选场（门1前等权记录）
    cands = [{"matchId": k, "league": (states.get(v["home"]) or {}).get("state", {}).get("league"),
              "matrix": matrices[k], "p_top": max(matrices[k].values()),
              "calibBandOk": True, "leagueCalibOk": True, "infoEvent": False}
             for k, v in compares.items()]
    weights = {k: 0.2 for k in ("s1_entropy", "s2_calib", "s3_league", "s4_info", "s5_disagreement")}
    picks = pk.pick(cands, top_n=2, weights=weights) if cands else []
    # 落盘
    _atomic_write(out_dir / "step1_states.json",
                  {t: {"att": round(s["att"], 4), "def": round(s["def"], 4), "flags": s["state"]["flags"]}
                   for t, s in states.items()})
    _atomic_write(out_dir / "step3_compare.json", {k: {kk: (round(vv, 4) if isinstance(vv, float) else vv)
                                                       for kk, vv in v.items()} for k, v in compares.items()})
    _atomic_write(out_dir / "step4_lambdas.json", {k: {"lam_h": round(v["lam_h"], 4),
                                                       "lam_a": round(v["lam_a"], 4), "contrib": v["contrib"]}
                                                   for k, v in lam_out.items()})
    _atomic_write(out_dir / "step5_matrix.json", {k: {kk: round(vv, 6) for kk, vv in v.items()}
                                                  for k, v in matrices.items()})
    _atomic_write(out_dir / "step6_picks.json", {"weightsNote": "门1前等权记录·门2开始前预注册冻结",
                                                 "picks": picks})
    summary = {"day": day, "generatedAt": now_iso, "matches": len(compares), "picks": len(picks)}
    _atomic_write(summary_path, summary)
    return summary

def _feed_from_cache(aliases_file=None) -> list:
    from common import ROOT
    d = json.loads((ROOT / "engine" / "cache" / "sporttery_matches.json").read_text(encoding="utf-8"))
    out = []
    for blk in (d.get("value") or {}).get("matchInfoList") or []:
        for m in blk.get("subMatchList") or []:
            out.append({"matchId": str(m.get("matchId")), "home": m.get("homeTeamAbbName"),
                        "away": m.get("awayTeamAbbName"), "league": m.get("leagueAbbName"),
                        "kickoff": f"{m.get('matchDate')} {str(m.get('matchTime'))[:5]}"})
    return out

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("day", nargs="?", default=date.today().isoformat())
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    ctx = sl.build_ctx(LEAGUES)
    print(json.dumps(run_day(args.day, ctx, OUT_ROOT / args.day, force=args.force,
                             now_iso=datetime.now().isoformat(timespec="seconds")), ensure_ascii=False))
