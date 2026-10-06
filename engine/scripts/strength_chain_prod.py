# -*- coding: utf-8 -*-
r"""strength_chain_prod.py —— 生产接入层（2026-10-06 大哥拍板"比分端替换"）。

职责：V3W-v2（HST链+去水v2）替代 crs_fusion/freq_band 在生产链的比分预测位置。
  输入：当日在售场清单（sporttery_matches.json 或 --feed 参数）
  输出：data/03-predictions/{date}-crs.json —— 每场 39 格概率矩阵 top5 + 选场 top4 + 去水标签
  用法：python engine/scripts/strength_chain_prod.py [date] [--feed path] [--force]

协议表位置（替换后）：
  体彩采集 → lineup_watch tick → 本地检索+ESPN → DC融合(方向·旧链保留) →
  **strength_chain_prod（比分端·新链）** → 战意状态机 → 分析评级 → 报告 → 影子层 → 闯关票 → v4b → commit

开发者 sszhang
"""
from __future__ import annotations
import argparse, json, sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import strength_loaders as sl
import strength_chain_eval as sce

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "data" / "03-predictions"
LEAGUES = ["uefa-nations","england-premier","spain-laliga","germany-bundesliga","italy-serie-a",
           "france-ligue1","netherlands-eredivisie","portugal-primeira","korea","japan",
           "denmark","sweden","norway","brazil","saudi","usa","france-ligue2",
           "world-cup","world-cup-qual","euro-qual","uefa-champions","uefa-europa"]
BOOM_V2 = 0.05       # S4 终校
HAD_HOT_V2 = 1.5     # S4 终校
TOP_N_PICKS = 4


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("day", nargs="?", default=date.today().isoformat())
    ap.add_argument("--feed", help="JSON 文件路径（当日在售场）")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    out_path = OUT_DIR / f"{args.day}-crs.json"
    if out_path.exists() and not args.force:
        print(f"[prod] 已存在·跳过: {out_path}")
        return 0

    if args.feed:
        feed = json.loads(Path(args.feed).read_text(encoding="utf-8"))
    else:
        from common import ROOT as _R
        d = json.loads((_R / "engine" / "cache" / "sporttery_matches.json").read_text(encoding="utf-8"))
        feed = [{"matchId": str(m.get("matchId")), "code": m.get("matchNumStr"),
                 "home": m.get("homeTeamAbbName"), "away": m.get("awayTeamAbbName"),
                 "kickoff": f"{m.get('matchDate')} {str(m.get('matchTime'))[:5]}"}
                for blk in (d.get("value") or {}).get("matchInfoList") or []
                for m in blk.get("subMatchList") or [] if m.get("matchId")]

    ctx = sl.build_ctx(LEAGUES)
    z2i = sl.zh_to_id()
    as_of = date.fromisoformat(args.day)
    memo = {}
    matches, cands = [], []
    for m in feed:
        hid, aid = z2i.get(m["home"]), z2i.get(m["away"])
        if not hid or not aid:
            matches.append({"code": m.get("code"), "match": f"{m['home']} v {m['away']}", "skip": "no_alias"})
            continue
        pred = sce._predict_match(hid, aid, as_of, ctx, memo, beta=0.05)
        if not pred or not pred.get("matrix"):
            matches.append({"code": m.get("code"), "match": f"{m['home']} v {m['away']}", "skip": "no_data"})
            continue
        matrix = pred["matrix"]
        top5 = sorted(matrix.items(), key=lambda kv: -kv[1])[:5]
        boom = sum(matrix.get(k, 0.0) for k in ("s1sh", "s1sd", "s1sa"))
        is_water = boom > BOOM_V2
        entry = {"code": m.get("code"), "match": f"{m['home']} v {m['away']}",
                 "matchId": m.get("matchId"),
                 "lam": [round(pred["lam"][0], 3), round(pred["lam"][1], 3)],
                 "had": {k: round(v, 4) for k, v in (pred.get("had") or {}).items()},
                 "crs_top5": [{"pick": k, "p": round(v, 4)} for k, v in top5],
                 "boomP": round(boom, 4), "water": is_water,
                 "flags": pred.get("flags", [])}
        matches.append(entry)
        if not is_water:
            cands.append(entry)

    picks = sorted(cands, key=lambda c: -c["crs_top5"][0]["p"])[:TOP_N_PICKS]
    doc = {"date": args.day, "model": "V3W-v2 (HST chain + dewater v2)",
           "params": {"boom": BOOM_V2, "had_hot": HAD_HOT_V2},
           "generatedAt": datetime.now().isoformat(timespec="seconds"),
           "matches": matches,
           "picks": [{"code": p["code"], "match": p["match"],
                      "pick": p["crs_top5"][0]["pick"], "p": p["crs_top5"][0]["p"]} for p in picks]}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[prod] {len(matches)} 场 · 选场 {len(picks)} → {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
