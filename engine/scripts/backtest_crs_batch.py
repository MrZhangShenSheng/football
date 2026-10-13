#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""比分端三层降级链分批回测（W1/W2/W3）·判据预注册 engine/cache/batch-backtest-prereg.json。

设计冻结：docs/superpowers/plans/2026-10-13-crs-batch-backtest.md §设计冻结。
防泄漏三道闸：team_state_on(dc_rolling) lag=2；决策价=crsOpen+as_of=开赛前一日；结算只读不回写。
开发者 sszhang"""
import json, sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from strength_chain_eval import POOL_OTHER, _score_facts, _predict_match
import market_lambda as mkl
import score_matrix as sm
import strength_loaders as sl
from strength_chain_prod import pick_source

ROOT = Path(__file__).resolve().parents[2]
HIST_DIR = ROOT / "engine" / "cache" / "hist_odds"
MIN_POOL_ITEMS = 20

_MATRIX_OTHER_TO_PICK = {v: p for p, v in POOL_OTHER.items()}   # 's1sh'→'other_h'（实测：未挂牌比分落 other 矩阵键）

def matrix_key_to_pick(k: str) -> str:
    """矩阵键 's01s00' → pick 键 '1:0'；other_* 三键直通；s1sh/s1sd/s1sa 矩阵内部键映回 other_*（Controller Ruling 盲区修复）。开发者 sszhang"""
    if k in _MATRIX_OTHER_TO_PICK:
        return _MATRIX_OTHER_TO_PICK[k]
    if k.startswith("other_"):
        return k
    if k.startswith("s") and k[3] == "s":
        return f"{int(k[1:3])}:{int(k[4:6])}"
    return k

def load_hist_window(date_lo: str, date_hi: str, *, hist_dir: Path | None = None) -> list[dict]:
    """两 hist 文件扫窗 → 标准化行。crs/crsOpen 均 ≥MIN_POOL_ITEMS 才收（四臂配对完整性）。"""
    base = hist_dir or HIST_DIR
    out, dropped = [], {"no_close": 0, "no_open": 0}
    for f in sorted(base.glob("crs_hist_*.json")):
        doc = json.loads(f.read_text(encoding="utf-8"))
        for m in doc.get("matches") or []:
            d = str(m.get("date") or "")
            if not (date_lo <= d <= date_hi):
                continue
            crs, crs_open = m.get("crs") or {}, m.get("crsOpen") or {}
            if len(crs) < MIN_POOL_ITEMS:
                dropped["no_close"] += 1; continue
            if len(crs_open) < MIN_POOL_ITEMS:
                dropped["no_open"] += 1; continue
            out.append({"date": d, "code": m.get("code"), "league": m.get("league"),
                        "home": m.get("home"), "away": m.get("away"), "score": m.get("score"),
                        "had": m.get("had") or {}, "ttg": m.get("ttg") or {},
                        "crs": crs, "crsOpen": crs_open})
    out.sort(key=lambda r: r["date"])
    return out

def settle_row(picks_ranked: list[str], row: dict, k: int) -> dict:
    """前 k pick 按封盘价结算（2元/注）。赛果经 _score_facts 落归一键；无价 pick=废注。
    实测口径（2026-10-13 探针）：_score_facts 对未挂牌比分返回 other 矩阵键 s1sh/s1sd/s1sa，
    须反解回体彩 pick 键 other_h/other_d/other_a 才能与 crs 池对齐。开发者 sszhang"""
    crs = row["crs"]
    facts = _score_facts(row["score"], set(crs.keys()))
    actual_pick = _MATRIX_OTHER_TO_PICK.get(facts[0]) or matrix_key_to_pick(facts[0]) if facts else None
    picks, dropped, hit, odds = [], 0, None, 0.0
    for p in picks_ranked:
        if len(picks) >= k:
            break
        if p not in crs:
            dropped += 1; continue          # 无价废注（不入 k 计数）
        picks.append(p)
        if hit is None and p == actual_pick:
            hit, odds = p, float(crs[p])
    payout = 2.0 * odds if hit else 0.0
    return {"picks": picks, "hit": hit, "odds": odds, "payout": payout, "dropped": dropped}

def _devig_ranked(pool: dict) -> list[str]:
    p = mkl.crs_power_devig(pool)
    ranked = [k for k, _ in sorted(p.items(), key=lambda kv: -kv[1])] if p else []
    return [matrix_key_to_pick(k) for k in ranked]   # crs_power_devig 出矩阵键（sXXsYY/s1sx），映回体彩 pick 键（实测探针 2026-10-13）

def arm_a0(row: dict) -> list[str]:
    """null 天花板：封盘 CRS power 去水降序。开发者 sszhang"""
    return _devig_ranked(row["crs"])

def arm_a1(row: dict) -> list[str]:
    """开盘信息臂：开盘 CRS power 去水降序。"""
    return _devig_ranked(row["crsOpen"])

def arm_a2(row: dict) -> list[str]:
    """强制层2：封盘 ttg+had 反解 λ(ρ=0) → 泊松矩阵降序（测反解结构增益，与 A0 同信息源）。"""
    ttg, had = row["ttg"], row["had"]
    if len(ttg) < 6 or not (had.get("h") and had.get("d") and had.get("a")):
        return []
    lam_sum = mkl.lam_sum_from_ttg(ttg)
    p3 = mkl.had_devig(had["h"], had["d"], had["a"])
    lh = mkl.solve_lamh(lam_sum, p3[0])
    mat = sm.dc_matrix(lh, lam_sum - lh, mkl.RHO_ZERO)
    ranked = sorted(mat.items(), key=lambda kv: -kv[1])
    return [matrix_key_to_pick(k) for k, _ in ranked]

# ---- A3/A4 臂（Task 5·brief Step 3）----

BETA = 0.05                      # prereg frozenParams.beta（V3W λ 乘子 β·与 strength_chain_prod main 同值）
DECISION_DAY_OFFSET = -1         # prereg frozenParams.decision_day_offset（as_of=开赛前一日·决策时点防泄漏）
_CTX_CACHE: dict = {}

def build_ctx_cached() -> dict:
    """34 库全量 ctx·进程级缓存（四臂三窗共享·防交叉污染即共享同一事实源）。"""
    if "ctx" not in _CTX_CACHE:
        leagues = sorted({p.stem.removesuffix("_matches")
                          for p in (ROOT / "data" / "02-results" / "league").glob("*_matches.json")})
        _CTX_CACHE["ctx"] = sl.build_ctx(leagues)
    return _CTX_CACHE["ctx"]

def _team_ids(row: dict):
    """hist 行队名=体彩中文名→zh_to_id 主路（strength_chain_prod:146 生产同款）；
    _norm_team 兜底英文显示名行（Task5 探针4 实测：中文经 _norm_team 全 None→A3 恒空）。"""
    aliases = sl.load_aliases()
    zh = sl.zh_to_id(aliases)
    def one(name: str):
        return zh.get(name) or sl._norm_team(name, aliases)
    return (one(row["home"]), one(row["away"]))

def arm_a3(row: dict, ctx: dict, memo: dict) -> list[str]:
    """强制层3：V3W 全链·as_of=开赛前一日（决策时点防泄漏）。未映射→[]。开发者 sszhang"""
    hid, aid = _team_ids(row)
    if not hid or not aid:
        return []
    as_of = date.fromisoformat(row["date"]) + timedelta(days=DECISION_DAY_OFFSET)
    try:
        pred = _predict_match(hid, aid, as_of, ctx, memo, BETA)
    except Exception:
        return []
    if not pred:
        return []
    mat = pred["matrix"]
    return [matrix_key_to_pick(k) for k, _ in sorted(mat.items(), key=lambda kv: -kv[1])]

def arm_a4(row: dict, ctx: dict, memo: dict) -> list[str]:
    """生产降级复刻：开盘价入 pick_source（层1 CRS去水→层2 反解）·None→A3 兜底。开发者 sszhang
    层1 top5 的 pick=crs_power_devig 输出矩阵键（sXXsYY/s1sx·T4 实测）→一律经 matrix_key_to_pick 映回体彩 pick 键。"""
    odds = {"crs": row["crsOpen"], "ttg": row["ttg"], "had": row["had"]}
    src = pick_source(odds)
    if src:
        return [matrix_key_to_pick(x["pick"]) for x in src["top5"]] if src["src"] == "market_crs" else \
               [matrix_key_to_pick(k) for k, _ in sorted(
                   sm.dc_matrix(src["lam"][0], src["lam"][1], mkl.RHO_ZERO).items(),
                   key=lambda kv: -kv[1])]
    return arm_a3(row, ctx, memo)
