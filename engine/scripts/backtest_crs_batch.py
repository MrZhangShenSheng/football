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
import strength_chain_eval as se
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
    return {"picks": picks, "hit": hit, "odds": odds, "payout": payout,
            "dropped": dropped, "actual": actual_pick}

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

def _arm_a3_offset(offset: int):
    """A3 臂的 as_of 偏移变体工厂（--leak-audit A 臂=决策日再提前 LEAK_AUDIT_SHIFT_DAYS 天）。
    memo 键控 (team, as_of) 含 as_of → 不同偏移共享 memo 无交叉污染。开发者 sszhang"""
    def fn(row: dict, ctx: dict, memo: dict) -> list[str]:
        hid, aid = _team_ids(row)
        if not hid or not aid:
            return []
        as_of = date.fromisoformat(row["date"]) + timedelta(days=offset)
        try:
            pred = _predict_match(hid, aid, as_of, ctx, memo, BETA)
        except Exception:
            return []
        if not pred:
            return []
        return [matrix_key_to_pick(k) for k, _ in sorted(pred["matrix"].items(), key=lambda kv: -kv[1])]
    return fn

def arm_a3(row: dict, ctx: dict, memo: dict) -> list[str]:
    """强制层3：V3W 全链·as_of=开赛前一日（决策时点防泄漏）。未映射→[]。开发者 sszhang"""
    return _arm_a3_offset(DECISION_DAY_OFFSET)(row, ctx, memo)

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

# ---- Task 6：窗口编排 + 配对 bootstrap CI + prereg 判据联动 ----

PREREG_PATH = ROOT / "engine" / "cache" / "batch-backtest-prereg.json"
BOOT_B, SEED, K_MAX = 2000, 20261013, 4       # prereg frozenParams.boot_B/seed/k_max 同值
CRITERIA_K = 1                                # prereg metric「top1 回收率逐场配对差」→ 判据固定 top1
LEAK_AUDIT_SHIFT_DAYS = 2                     # 红线：A/B as_of=决策日−1 再提前 2 天（=赛日−3）
ARM_KEY = {"A0_close_null": "A0", "A1_open_devig": "A1", "A2_lambda_reverse": "A2",
           "A3_v3w_model": "A3", "A4_prod_fallback": "A4"}   # prereg 臂名 → 臂函数键（T7 ARM_FNS 同名键）

def _load_prereg() -> dict:
    return json.loads(PREREG_PATH.read_text(encoding="utf-8"))

def load_window_range(name: str) -> dict:
    """prereg windows 段 → {from, to, role}；未知窗=名打错，立即炸不让静默跑错窗。"""
    win = (_load_prereg().get("windows") or {}).get(name)
    if not win:
        raise ValueError(f"prereg windows 无此窗口: {name}")
    return {"from": win["from"], "to": win["to"], "role": win.get("role", "")}

def prereg_pairs(win_name: str) -> list[dict]:
    """主判据+secondary 中窗匹配的对；prereg 臂名经 ARM_KEY 映臂函数键。
    W1/W2 无 prereg 对→[]（exploratory 由 run_window 的 exploratory 标记处理）。"""
    doc = _load_prereg()
    items = [("primary", doc.get("primaryCriterion") or {})] + \
            [("secondary", s) for s in doc.get("secondary") or []]
    return [{"arm": ARM_KEY[it["arm"]], "baseline": ARM_KEY[it["baseline"]],
             "role": role, "metric": it.get("metric", "")}
            for role, it in items if it.get("window") == win_name and it.get("arm") and it.get("baseline")]

ARM_FNS = {"A0": lambda row, ctx, memo: arm_a0(row),      # 签名统一 (row, ctx, memo)->list
           "A1": lambda row, ctx, memo: arm_a1(row),
           "A2": lambda row, ctx, memo: arm_a2(row),
           "A3": arm_a3, "A4": arm_a4}

def run_window(win_name: str, arms: dict, *, k_max: int = K_MAX, hist_dir: Path | None = None) -> dict:
    """窗口编排：装载→各臂逐场排序→逐 k 结算→聚合+主/次判据配对 CI。arm 签名 (row, ctx, memo)->list。
    配对只取两臂都非空的场（按行号对齐——A3 覆盖率≈50% 且缺场与命中交错时，
    按较短截断的 zip 会系统性错配）；判据仅 prereg 口径，W3 外标 exploratory。开发者 sszhang"""
    win = load_window_range(win_name)
    rows = load_hist_window(win["from"], win["to"], hist_dir=hist_dir)
    ctx = build_ctx_cached()
    memo: dict = {}
    per = {arm: {k: {"n": 0, "stake": 0.0, "payout": 0.0, "dropped": 0} for k in range(1, k_max + 1)}
           for arm in arms}
    pay_detail = {arm: {k: {} for k in range(1, k_max + 1)} for arm in arms}   # arm -> k -> {行号: 派彩}
    unmapped = {arm: 0 for arm in arms}
    for i, row in enumerate(rows):
        for arm, fn in arms.items():
            ranked = fn(row, ctx, memo)
            if not ranked:
                unmapped[arm] += 1; continue
            for k in range(1, k_max + 1):
                st = settle_row(ranked, row, k)
                per[arm][k]["n"] += 1
                per[arm][k]["stake"] += 2.0 * k
                per[arm][k]["payout"] += st["payout"]
                per[arm][k]["dropped"] += st["dropped"]
                pay_detail[arm][k][i] = st["payout"]
    n_rows = len(rows)
    report = {"window": win_name, "role": win.get("role", ""), "rows": n_rows,
              "arms": {a: {f"k{k}": {**v, "recovery": round(v["payout"] / v["stake"], 4) if v["stake"] else None}
                           for k, v in ks.items()} for a, ks in per.items()},
              "unmapped": unmapped,
              "unmappedRate": {a: round(c / n_rows, 4) if n_rows else None for a, c in unmapped.items()}}
    for pair in prereg_pairs(win_name):
        hi, base, k = pair["arm"], pair["baseline"], CRITERIA_K
        d_hi, d_base = pay_detail[hi][k], pay_detail[base][k]
        common = sorted(set(d_hi) & set(d_base))
        diffs = [d_hi[i] - d_base[i] for i in common]
        ci = se._boot_ci_diff(diffs, seed=SEED, n_boot=BOOT_B) if diffs else []
        report.setdefault("criteria", []).append(
            {**pair, "k": k, "pairedN": len(common), "nArm": len(d_hi), "nBaseline": len(d_base),
             "diffMean": round(sum(diffs) / len(diffs), 4) if diffs else None,
             "ci95": [round(x, 4) for x in ci] if diffs else None,
             "verdict": ("PASS" if ci[0] > 0 else "FAIL") if diffs else "NO-PAIR",
             "exploratory": win_name not in ("W3",)})
    return report

def run_leak_audit(win_name: str, *, k: int = CRITERIA_K, hist_dir: Path | None = None,
                   arm_b=None, arm_a=None) -> dict:
    """泄漏 A/B + 随机基线（prereg redlines[0]：任何臂窗口回收率>1.0 → 先过此审计，不过不进结论）。
    B=A3 决策日 as_of（DECISION_DAY_OFFSET）；A=同链再提前 LEAK_AUDIT_SHIFT_DAYS 天（赛日−3）。
    arm_b/arm_a 可注入合成臂（测试用）；裁决归 T7，本函数只产数字。开发者 sszhang"""
    win = load_window_range(win_name)
    rows = load_hist_window(win["from"], win["to"], hist_dir=hist_dir)
    ctx = build_ctx_cached()
    memo: dict = {}
    arm_b = arm_b or ARM_FNS["A3"]
    arm_a = arm_a or _arm_a3_offset(DECISION_DAY_OFFSET - LEAK_AUDIT_SHIFT_DAYS)
    pay = {"B": {}, "A": {}}
    picks, actuals = [], []
    for i, row in enumerate(rows):
        for tag, fn in (("B", arm_b), ("A", arm_a)):
            ranked = fn(row, ctx, memo)
            if not ranked:
                continue
            st = settle_row(ranked, row, k)
            pay[tag][i] = st["payout"]
            if tag == "B" and st["picks"] and st["actual"]:
                picks.append(st["picks"][0]); actuals.append(st["actual"])
    common = sorted(set(pay["B"]) & set(pay["A"]))
    diffs = [pay["B"][i] - pay["A"][i] for i in common]
    ci = se._boot_ci_diff(diffs, seed=SEED, n_boot=BOOT_B) if diffs else []

    def _rec(d: dict) -> dict:
        stake = 2.0 * k * len(d)
        return {"n": len(d), "recovery": round(sum(d.values()) / stake, 4) if stake else None}
    return {"window": win_name, "rows": len(rows), "k": k,
            "decisionDayOffsetB": DECISION_DAY_OFFSET,
            "decisionDayOffsetA": DECISION_DAY_OFFSET - LEAK_AUDIT_SHIFT_DAYS,
            "armB": _rec(pay["B"]), "armA": _rec(pay["A"]),
            "pairedN": len(common),
            "diffMean": round(sum(diffs) / len(diffs), 4) if diffs else None,
            "ci95": [round(x, 4) for x in ci] if diffs else None,
            "observedHit": round(sum(1 for p, a in zip(picks, actuals) if p == a) / len(picks), 4) if picks else None,
            "randomShuffle": se._random_shuffle_baseline(picks, actuals, seed=SEED) if picks else {},
            "redline": "任何臂窗口回收率>1.0 → 先过此审计+随机基线，不过不进结论（prereg redlines[0]）"}

def main(argv: list[str]) -> int:
    """CLI：run <W1|W2|W3> [--leak-audit]——报告打 stdout（T7 重定向归档）。开发者 sszhang"""
    if len(argv) < 2 or argv[0] != "run":
        print("用法: python backtest_crs_batch.py run W1|W2|W3 [--leak-audit]", file=sys.stderr)
        return 2
    report = run_window(argv[1], ARM_FNS)
    if "--leak-audit" in argv[2:]:
        report["leakAudit"] = run_leak_audit(argv[1])
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
