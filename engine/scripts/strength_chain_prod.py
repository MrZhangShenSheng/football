# -*- coding: utf-8 -*-
r"""strength_chain_prod.py —— 生产比分端接入层（v35-P4·D2+D4 三层降级换件·2026-10-11）。

三层降级（市场优先·零画像依赖）：
  层1 市场 CRS 31 格 power 去水主干（market_crs_devig·U2 凭证）
  层2 市场反解 λ 矩阵：TTG 期望 + HAD 反解 λh·ρ=0（market_lambda_solver·U2+U4 凭证）
  层3 V3W-v2 兜底（HST链+去水v2·原链自 2026-10-06 主干位降级；V3W 可算时挂 legacy 对照一周）
启动闸：surviving_chain.require_component 两组件（铁律15·无审计不入链·缺凭证拒跑）。

  输入：当日在售场清单（sporttery_matches.json 或 --feed 参数）+ score_odds 存档（day±1·businessDate 对齐）
  输出：data/03-predictions/{date}-crs.json —— 每场 top5 概率 + src 分层 + 选场 top4 + 水闸标签
  用法：python engine/scripts/strength_chain_prod.py [date] [--feed path] [--force]

协议表位置：
  体彩采集 → lineup_watch tick → 本地检索+ESPN → DC融合(方向·旧链保留) →
  **strength_chain_prod（比分端·三层降级）** → 战意状态机 → 分析评级 → 报告 → 影子层 → 闯关票 → v4b → commit

开发者 sszhang
"""
from __future__ import annotations
import argparse, json, sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import strength_loaders as sl
import strength_chain_eval as sce
import market_lambda as mkl
import surviving_chain as sch
import score_matrix as sm

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "data" / "03-predictions"
LEAGUES = ["uefa-nations","england-premier","spain-laliga","germany-bundesliga","italy-serie-a",
           "france-ligue1","netherlands-eredivisie","portugal-primeira","korea","japan",
           "denmark","sweden","norway","brazil","saudi","usa","france-ligue2",
           "world-cup","world-cup-qual","euro-qual","uefa-champions","uefa-europa",
           "england-championship","germany-bundesliga2","spain-liga2","italy-serie-b",
           "belgium-first-a","turkey-super-lig","greece-super","SC0"]
BOOM_V2 = 0.05       # S4 终校（层3 V3W 语义·params 沿袭）
HAD_HOT_V2 = 1.5     # S4 终校（层3 V3W 语义·params 沿袭）
TOP_N_PICKS = 4
BOOM_GATE = 0.05        # water 闸：层1=ttg_boom(TTG 6+7档) · 层2/层3=矩阵 other 溢出桶（同值 0.05 沿袭）
LEGACY_UNTIL = "2026-10-18"   # 影子对照一周（大哥拍板后移除 legacy 字段）
CRS_OTHER_ZH = {"胜其他": "other_h", "平其他": "other_d", "负其他": "other_a"}  # 存档中文键→引擎键
TTG_PREFIX = "s"        # 存档 ttg 键 s0..s7 前缀（引擎口径 0..7）
# v25 联赛质量普查旗（两段 Bonferroni·CRS 端）——v26 实证：排除不改命中（LL 弱≠选格弱），
# 故仅作卡面警示标注（fade-strategy-prereg v26 判据②余烬·2026-10-09），不动选场。
LEAGUE_WARN_V25 = {"意甲", "德甲", "西甲", "欧冠", "欧罗巴", "日职", "韩职", "瑞超", "葡超"}


def load_day_odds(day: str, *, odds_dir: Path | None = None) -> dict:
    """扫 score_odds 相邻三日存档·按块 businessDate==day 合并·键=matchNumStr（存档无 matchId）。

    存档文件日=抓取日≠比赛日（一日存档含多日块）——开赛日口径铁律。
    归一适配（2026-10-11 存档实证键形）：crs 中文 other 三键→other_*·ttg s 前缀剥除。
    同 businessDate 块在多文件出现时后扫覆盖（最新抓取价优先）。
    """
    base = odds_dir or (ROOT / "engine" / "cache" / "score_odds")
    d0 = date.fromisoformat(day)
    out = {}
    for off in (-1, 0, 1):
        f = base / f"{(d0 + timedelta(days=off)).isoformat()}.json"
        if not f.exists():
            continue
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except Exception:                  # noqa: BLE001 —— 脏存档跳过·不阻断生产
            continue
        for blk in doc.get("matchDays") or []:
            if str(blk.get("businessDate")) != day:
                continue
            for m in blk.get("matches") or []:
                if not m.get("matchNumStr"):
                    continue
                out[str(m["matchNumStr"])] = {
                    "crs": {CRS_OTHER_ZH.get(k, k): v for k, v in (m.get("crs") or {}).items()},
                    "ttg": {k[len(TTG_PREFIX):] if k.startswith(TTG_PREFIX) else k: v
                            for k, v in (m.get("ttg") or {}).items()},
                    "had": dict(m.get("had") or {}),
                }
    return out


def pick_source(odds: dict) -> dict | None:
    """三层降级前两层（市场侧·纯函数）：层1 CRS去水主干 → 层2 反解λ矩阵(ρ=0)。None=走层3 V3W。"""
    crs_p = mkl.crs_power_devig(odds.get("crs") or {})
    if crs_p:
        top5 = sorted(crs_p.items(), key=lambda kv: -kv[1])[:5]
        return {"src": "market_crs", "lam": None,
                "top5": [{"pick": k, "p": round(v, 4)} for k, v in top5],
                "boom": mkl.ttg_boom(odds.get("ttg") or {})}
    ttg, had = odds.get("ttg") or {}, odds.get("had") or {}
    if len(ttg) >= 6 and had.get("h") and had.get("d") and had.get("a"):
        lam_sum = mkl.lam_sum_from_ttg(ttg)
        p3 = mkl.had_devig(had["h"], had["d"], had["a"])
        lh = mkl.solve_lamh(lam_sum, p3[0])
        mat = sm.dc_matrix(lh, lam_sum - lh, mkl.RHO_ZERO)     # D4: ρ=0
        top5 = sorted(mat.items(), key=lambda kv: -kv[1])[:5]
        boom = sum(mat.get(k, 0.0) for k in ("s1sh", "s1sd", "s1sa"))
        return {"src": "market_lambda", "lam": [round(lh, 3), round(lam_sum - lh, 3)],
                "top5": [{"pick": k, "p": round(v, 4)} for k, v in top5],
                "boom": round(boom, 4)}
    return None


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

    sch.require_component("market_crs_devig")       # 铁律15 机器闸·缺凭证 SystemExit(2)
    sch.require_component("market_lambda_solver")

    if args.feed:
        feed = json.loads(Path(args.feed).read_text(encoding="utf-8"))
    else:
        from common import ROOT as _R
        d = json.loads((_R / "engine" / "cache" / "sporttery_matches.json").read_text(encoding="utf-8"))
        # 双结构兼容：旧 value.matchInfoList（matchNumStr/homeTeamAbbName）与新归一 matches
        # （code/home/away/league·2026-10-08 实证旧路径对新缓存静默出空——盲区修复一并落此）
        feed = [{"matchId": str(m.get("matchId")), "code": m.get("matchNumStr"),
                 "home": m.get("homeTeamAbbName"), "away": m.get("awayTeamAbbName"),
                 "league": m.get("leagueAbbName") or m.get("league"),
                 "kickoff": f"{m.get('matchDate')} {str(m.get('matchTime'))[:5]}"}
                for blk in (d.get("value") or {}).get("matchInfoList") or []
                for m in blk.get("subMatchList") or [] if m.get("matchId")]
        feed += [{"matchId": str(m.get("matchId")), "code": m.get("code"),
                  "home": m.get("home"), "away": m.get("away"), "league": m.get("league"),
                  "kickoff": m.get("kickoff")}
                 for m in d.get("matches") or [] if m.get("matchId")]

    odds_map = load_day_odds(args.day)
    hit = sum(1 for m in feed if m.get("code") in odds_map)
    print(f"[prod] 市场盘 join 命中 {hit}/{len(feed)} 场（池 {len(odds_map)}）·businessDate={args.day}")
    if odds_map and hit == 0:
        print("[prod] 警告: 市场盘池非空但 join 命中 0——code 错位将全场降级层3", file=sys.stderr)

    ctx = sl.build_ctx(LEAGUES)
    z2i = sl.zh_to_id()
    as_of = date.fromisoformat(args.day)
    memo = {}
    matches, cands = [], []
    for m in feed:
        src = pick_source(odds_map.get(m.get("code") or "") or {})
        # —— V3W 旧链照算（legacy 对照一周 + 层3 兜底 + S2/S4 画像）——
        v3w = None
        hid, aid = z2i.get(m["home"]), z2i.get(m["away"])
        pred = None
        if hid and aid:
            pred = sce._predict_match(hid, aid, as_of, ctx, memo, beta=0.05)
            if pred and pred.get("matrix"):
                mtop5 = sorted(pred["matrix"].items(), key=lambda kv: -kv[1])[:5]
                v3w = {"lam": [round(pred["lam"][0], 3), round(pred["lam"][1], 3)],
                       "top5": [{"pick": k, "p": round(v, 4)} for k, v in mtop5],
                       "boomP": round(sum(pred["matrix"].get(k, 0.0)
                                          for k in ("s1sh", "s1sd", "s1sa")), 4)}
        if src:                                     # 层1/层2：市场主干
            is_water = src["boom"] > BOOM_GATE
            entry = {"code": m.get("code"), "match": f"{m['home']} v {m['away']}",
                     "matchId": m.get("matchId"), "league": m.get("league"),
                     "leagueWarn": (m.get("league") or "") in LEAGUE_WARN_V25,
                     "src": src["src"], "lam": src["lam"],
                     "crs_top5": src["top5"], "ttgBoom": src["boom"], "water": is_water}
            if v3w:
                entry["legacy"] = v3w               # 影子对照一周（LEGACY_UNTIL 后移除）
        elif v3w:                                   # 层3：V3W 兜底
            is_water = v3w["boomP"] > BOOM_GATE
            entry = {"code": m.get("code"), "match": f"{m['home']} v {m['away']}",
                     "matchId": m.get("matchId"), "league": m.get("league"),
                     "leagueWarn": (m.get("league") or "") in LEAGUE_WARN_V25,
                     "src": "v3w_fallback", "lam": v3w["lam"],
                     "had": {k: round(v, 4) for k, v in (pred.get("had") or {}).items()},
                     "crs_top5": v3w["top5"], "boomP": v3w["boomP"], "water": is_water,
                     "flags": pred.get("flags", [])}
        else:
            matches.append({"code": m.get("code"), "match": f"{m['home']} v {m['away']}",
                            "skip": "no_data"})
            continue
        matches.append(entry)
        # v35 证据链 hook（fail-open）：S4/S5/S7 按层记
        try:
            import evidence
            _key = evidence.norm_key(match_id=m.get("matchId"), code=m.get("code"),
                                     home=m["home"], away=m["away"])
            evidence.record(args.day, _key, evidence.STEP_LAMBDA,
                            {"src": entry["src"], "lam": entry["lam"],
                             "beta": 0.05 if entry["src"] == "v3w_fallback" else None})
            evidence.record(args.day, _key, evidence.STEP_DEVIG,
                            {"method": entry["src"], "boomGate": BOOM_GATE,
                             "water": entry["water"]})
            evidence.record(args.day, _key, evidence.STEP_MATRIX,
                            {"top5": entry["crs_top5"], "src": entry["src"]})
            if hid and aid:
                evidence.record(args.day, _key, evidence.STEP_PROFILE,
                                {"hid": hid, "aid": aid,
                                 "hstRows": len(ctx["hst"].get(pred.get("league") or "", []))
                                 if pred else 0})
        except Exception:                 # noqa: BLE001 —— fail-open
            pass
        if not entry["water"]:
            cands.append(entry)

    picks = sorted(cands, key=lambda c: -c["crs_top5"][0]["p"])[:TOP_N_PICKS]
    doc = {"date": args.day, "model": "market-3layer (CRS devig → lambda solver → V3W fallback)",
           "params": {"boom": BOOM_V2, "had_hot": HAD_HOT_V2,
                      "boomGate": BOOM_GATE, "rho": mkl.RHO_ZERO, "legacyUntil": LEGACY_UNTIL,
                      "boomGateNote": "layer1=ttg 6+7 · layer2/3=matrix other bucket"},
           "generatedAt": datetime.now().isoformat(timespec="seconds"),
           "matches": matches,
           "picks": [{"code": p["code"], "match": p["match"], "league": p.get("league"),
                      "leagueWarn": p.get("leagueWarn", False), "src": p.get("src"),
                      "pick": p["crs_top5"][0]["pick"], "p": p["crs_top5"][0]["p"]} for p in picks]}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[prod] {len(matches)} 场 · 选场 {len(picks)} → {out_path}")
    # P4 终审 I3 观察（legacy 周 10-18 必查）：top1 p 降序跨三层混排——层2/3 矩阵概率更集中、
    # 大热门 top1 可 0.15~0.22 高于层1 市场校准概率(~0.10~0.15)封顶——选场系统性偏降级源，
    # 与 U2"市场主干最优"判决精神相悖；每日看 picks src 构成，层2/3 占比高须换归一化排序。
    src_cnt = {}
    for p in picks:
        src_cnt[p["src"]] = src_cnt.get(p["src"], 0) + 1
    print(f"[prod] picks src 构成: {' '.join(f'{k}={v}' for k, v in sorted(src_cnt.items()))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
