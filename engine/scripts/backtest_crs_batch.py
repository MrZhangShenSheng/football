#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""比分端三层降级链分批回测（W1/W2/W3）·判据预注册 engine/cache/batch-backtest-prereg.json。

设计冻结：docs/superpowers/plans/2026-10-13-crs-batch-backtest.md §设计冻结。
防泄漏三道闸：team_state_on(dc_rolling) lag=2；决策价=crsOpen+as_of=开赛前一日；结算只读不回写。
开发者 sszhang"""
import json, sys, time
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
EXPECT_ZERO_MARK = "预期≈0"                    # metric 含此标记的判据=A4 复刻确认（恒等预期）·仅 HTML 呈现层分叉（I-3·JSON 机械 verdict 保留原值）
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

# ---- Task 7：CLI 命令 + 报告归档 + HTML 报告 ----

OUT_DIR = ROOT / "data" / "04-summaries" / "crs-batch-backtest"
OUT_DIR_NAME = "crs-batch-backtest-report.html"
WINDOW_ORDER = ("W1", "W2", "W3")
A0_RECOVERY_BAND = (0.80, 0.88)   # prereg redlines[1]：A0 top1 窗内带（全库口径 84% 容差·出界=管道 bug 中断不放宽）
ROLE_ADJUDICATION = "adjudication"  # prereg 裁决窗角色（W3）——红线出带才中断；exploratory 窗（W1/W2）出带降级 warn 不中断（I-1 分窗语义·与 2026-10-13 归档事实对齐：W2 出带 0.8913 时三窗+HTML 跑完归档）
LEAK_AUDIT_MAX_DELTA = 0.02       # brief Step 3：|Δrecovery|<2pp 过
ARM_ORDER = ("A0", "A1", "A2", "A3", "A4")
ARM_LABELS = {"A0": "A0 封盘null天花板", "A1": "A1 开盘CRS去水",
              "A2": "A2 λ反解(封盘ttg+had)", "A3": "A3 V3W模型(as_of=前一日)", "A4": "A4 生产降级复刻"}
LIMITATIONS = (
    ("A2 封盘反解", "A2 与 A0 同信息源（封盘 ttg+had 反解 λ·ρ=0），测的是矩阵结构增益而非新信息；"
                 "39 键矩阵中 8 键体彩无价，dropped 与挂牌臂结构性不对等。"),
    ("决策时点=前一日", "A3 as_of=开赛前一日（DECISION_DAY_OFFSET=-1，prereg 冻结），早于体彩实际可购/临场时点——"
                   "模型臂决策可用信息少于实票临场口径，对 A3 略偏保守。"),
    ("W1/W2 参数回望", "V3W 链参数（HST 权重/β=0.05）标定基于含 W1/W2 期的全库，W1/W2 回收率存在样本内乐观偏差；"
                   "仅 W3（2024-10~2025-09 历史窗）相对干净——prereg exploratoryOnly 已冻结此口径。"),
    ("A4 层2/3 降级路径 0 触发", "装载器 crsOpen≥20 门槛=层1 门槛，配对过滤剔除了生产会降级的场"
                            "——层2/3 独立价值本轮不可观测。"),
    ("口径二（模拟实票）未实现", "主判据已裁决后属 moot（prereg exploratoryOnly 已列「口径二模拟实票」）。"),
)
SOURCES = ("engine/cache/hist_odds/crs_hist_2023-09-01_2025-09-30.json、crs_hist_2025-10-01_2026-09-28.json"
           "（crs/crsOpen ≥20 键配对过滤）·判据 engine/cache/batch-backtest-prereg.json（2026-10-13 冻结）"
           "·A3/A4 链 data/02-results/league/*_matches.json（34 库 ctx·team_state_on lag=2 防泄漏）")

def a0_redline_check(report: dict) -> str | None:
    """prereg redlines[1]：A0 top1 回收率窗内偏离 0.80~0.88 = 管道 bug 中断。
    只校 prereg 冻结真窗（区间=真窗全库口径 84% 校准，合成/他窗不适用）。开发者 sszhang"""
    if report.get("window") not in WINDOW_ORDER:
        return None
    rec = (report.get("arms", {}).get("A0", {}) or {}).get("k1", {}).get("recovery")
    lo, hi = A0_RECOVERY_BAND
    if rec is None:
        return f"A0 top1 recovery 缺失（rows={report.get('rows')}·stake=0 管道异常）"
    if not (lo <= rec <= hi):
        return f"A0 top1 recovery={rec} 出界 [{lo}, {hi}]（prereg redlines[1]·管道 bug 中断·不放宽区间）"
    return None

def _needs_leak_audit(report: dict) -> bool:
    """prereg redlines[0]：任何臂窗口任何 k 回收率>1.0 → 必跑泄漏审计。"""
    for ks in (report.get("arms") or {}).values():
        for v in ks.values():
            if (v or {}).get("recovery") is not None and v["recovery"] > 1.0:
                return True
    return False

def _write_json(name: str, doc: dict) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p = OUT_DIR / name
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return p

def _print_window_summary(report: dict) -> None:
    a0 = (report.get("arms", {}).get("A0", {}) or {}).get("k1", {}) or {}
    print(f"[{report.get('window')}] rows={report.get('rows')} role={report.get('role')} "
          f"elapsed={report.get('elapsedSec')}s A0.top1.recovery={a0.get('recovery')} "
          f"(红线带 {A0_RECOVERY_BAND[0]}~{A0_RECOVERY_BAND[1]})")
    for arm in ARM_ORDER:
        rate = (report.get("unmappedRate") or {}).get(arm)
        dropped = ((report.get("arms", {}).get(arm, {}) or {}).get("k1", {}) or {}).get("dropped")
        print(f"  {arm}: unmappedRate={rate} k1.dropped={dropped}")
    for c in report.get("criteria", []):
        print(f"  [判据][{c['role']}] {c['arm']} vs {c['baseline']} k={c['k']}: "
              f"diffMean={c['diffMean']} ci95={c['ci95']} pairedN={c['pairedN']} "
              f"(nArm={c['nArm']}/nBaseline={c['nBaseline']}) verdict={c['verdict']} "
              f"exploratory={c['exploratory']}")

def _run_and_archive(win_name: str) -> tuple[int, dict]:
    """跑窗→注入耗时/生成时刻→归档 {win}.json→A0 红线分窗自检（I-1）：
    裁决窗（role=adjudication·W3）出带 rc=1 中断；exploratory 窗（W1/W2）出带降级
    warn 不中断——JSON 补 redline{status:warn-exploratory} 留痕重写归档
    （与 2026-10-13 归档事实对齐：W2 出带 0.8913 时三窗+HTML 跑完）。
    JSON 先落盘留排查凭证（中断路径同）。开发者 sszhang"""
    t0 = time.perf_counter()
    report = run_window(win_name, ARM_FNS)
    report["elapsedSec"] = round(time.perf_counter() - t0, 1)
    report["generatedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _write_json(f"{win_name}.json", report)
    _print_window_summary(report)
    viol = a0_redline_check(report)
    if viol:
        if report.get("role") == ROLE_ADJUDICATION:
            print(f"[REDLINE][{win_name}] {viol}", file=sys.stderr)
            return 1, report
        rec = (report.get("arms", {}).get("A0", {}) or {}).get("k1", {}).get("recovery")
        report["redline"] = {"status": "warn-exploratory", "value": rec,
                             "band": list(A0_RECOVERY_BAND), "message": viol}
        _write_json(f"{win_name}.json", report)
        print(f"[REDLINE-WARN][{win_name}] {viol}（exploratory 窗·降级 warn 不中断"
              f"·排查定性大赛期特征·带不放宽·W3 独立自检带内）", file=sys.stderr)
    return 0, report

def run_leak_audit_archived(win_name: str) -> tuple[int, dict]:
    """泄漏审计跑+归档 {win}-leakaudit.json+A/B 回收率对照打印；|Δ|<2pp PASS（brief Step 3）。"""
    t0 = time.perf_counter()
    rep = run_leak_audit(win_name)
    rep["elapsedSec"] = round(time.perf_counter() - t0, 1)
    rep["generatedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
    rb = (rep.get("armB") or {}).get("recovery")
    ra = (rep.get("armA") or {}).get("recovery")
    if rb is not None and ra is not None:
        rep["recoveryDelta"] = round(rb - ra, 4)
        rep["verdict"] = "PASS" if abs(rep["recoveryDelta"]) < LEAK_AUDIT_MAX_DELTA else "FAIL"
    else:
        rep["verdict"] = "NO-PAIR"
    _write_json(f"{win_name}-leakaudit.json", rep)
    print(f"[leak-audit][{win_name}] B(as_of D{rep.get('decisionDayOffsetB')}) recovery={rb} vs "
          f"A(D{rep.get('decisionDayOffsetA')}) recovery={ra} Δ={rep.get('recoveryDelta')} "
          f"verdict={rep['verdict']} pairedN={rep.get('pairedN')}")
    return (0 if rep["verdict"] == "PASS" else 1), rep

def _esc(x) -> str:
    return str(x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

_CSS = ("body{font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;max-width:1100px;"
        "margin:24px auto;padding:0 18px;color:#1f2430;background:#fbfbfd}"
        "h1{font-size:22px}h2{font-size:17px;color:#2b5f9e;border-bottom:2px solid #2b5f9e;"
        "padding-bottom:4px;margin-top:30px}h3{font-size:15px;margin-bottom:6px}"
        "table{border-collapse:collapse;margin:10px 0;font-size:13px}"
        "th,td{border:1px solid #d5d9e0;padding:4px 10px;text-align:right}"
        "th{background:#eef2f7}th:first-child,td:first-child{text-align:left}"
        ".sub{display:block;color:#8a92a0;font-size:11px}"
        ".verdict-PASS{color:#0a7d32;font-weight:700}.verdict-FAIL{color:#c0392b;font-weight:700}"
        ".verdict-NO-PAIR{color:#777;font-weight:700}.verdict-IDENT{color:#2b5f9e;font-weight:700}.hi{background:#fdeaea}"
        ".badge-exp{background:#fff2cc;color:#7a5c00;padding:1px 7px;border-radius:3px;font-size:11px}"
        ".badge-adj{background:#dcefe0;color:#1c5e2e;padding:1px 7px;border-radius:3px;font-size:11px}"
        ".foot,.sub-line{color:#667;font-size:12px}p{line-height:1.6}")

def build_html_report(reports: dict, audits: dict, meta: dict) -> str:
    """三窗裁决 HTML：主判据置顶→泄漏审计→窗级明细→限制三条→数据来源与耗时。
    仅用户报告用 HTML+内嵌 SVG（仓库铁律 9）。开发者 sszhang"""
    h = ['<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">',
         "<title>比分端三层降级链分批回测 W1/W2/W3</title>",
         f"<style>{_CSS}</style></head><body>",
         '<h1><svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true" '
         'style="vertical-align:-4px;color:#2b5f9e"><path d="M4 20h16M7 20v-6M12 20V8M17 20v-10" '
         'stroke="currentColor" stroke-width="2.2" fill="none" stroke-linecap="round"/></svg> '
         "比分端三层降级链分批回测·三窗裁决报告</h1>",
         f"<p class='sub-line'>生成 {_esc(meta.get('finishedAt'))}·判据预注册 "
         f"engine/cache/batch-backtest-prereg.json（2026-10-13 冻结·铁律 14② 禁止跑后调口径捞结果）</p>"]
    # —— 主判据置顶（W3 预注册裁决）——
    h.append("<h2>主判据（W3·预注册裁决）</h2>")
    crits = (reports.get("W3", {}) or {}).get("criteria") or []
    if crits:
        h.append("<table><tr><th>角色</th><th>臂</th><th>基线</th><th>k</th><th>pairedN</th>"
                 "<th>nArm</th><th>nBaseline</th><th>diffMean</th><th>CI95</th><th>verdict</th><th>性质</th></tr>")
        for c in crits:
            ci_txt = f"[{c['ci95'][0]}, {c['ci95'][1]}]" if c.get("ci95") else "—"
            dm_txt = c.get("diffMean") if c.get("diffMean") is not None else "—"
            is_ident = EXPECT_ZERO_MARK in (c.get("metric") or "") and c.get("diffMean") == 0
            verdict_html = ("<span class='verdict-IDENT'>一致（如预期）</span>" if is_ident
                            else f"<span class='verdict-{_esc(c['verdict'])}'>{_esc(c['verdict'])}</span>")
            h.append(f"<tr><td>{_esc(c['role'])}</td><td>{_esc(ARM_LABELS.get(c['arm'], c['arm']))}</td>"
                     f"<td>{_esc(ARM_LABELS.get(c['baseline'], c['baseline']))}</td><td>{c['k']}</td>"
                     f"<td>{c['pairedN']}</td><td>{c['nArm']}</td><td>{c['nBaseline']}</td>"
                     f"<td>{dm_txt}</td><td>{ci_txt}</td>"
                     f"<td>{verdict_html}</td>"
                     f"<td>{'exploratory' if c['exploratory'] else '冻结裁决'}</td></tr>")
        h.append("</table>")
        h.append("<p>口径：top1 回收率逐场配对差·bootstrap95 B=2000·seed=20261013·"
                 "PASS=CI 下限&gt;0；配对=两臂都非空的场（行号交集）；"
                 "diffMean=逐场派彩差（元/场，÷2=回收率差）。</p>")
    else:
        h.append("<p>W3 判据未产出（窗口未跑或红线中断）。</p>")
    # —— 泄漏审计 ——
    h.append("<h2>泄漏审计 leak-audit（A3 决策日 A/B）</h2>")
    if audits:
        h.append("<table><tr><th>窗</th><th>B as_of=赛日前一日 recovery</th><th>A as_of=赛日前三日 recovery</th>"
                 "<th>Δ 判定</th><th>pairedN</th><th>observedHit</th><th>randomShuffle</th></tr>")
        for w, a in audits.items():
            h.append(f"<tr><td>{_esc(w)}</td><td>{(a.get('armB') or {}).get('recovery')}</td>"
                     f"<td>{(a.get('armA') or {}).get('recovery')}</td>"
                     f"<td>{'—' if a.get('recoveryDelta') is None else a['recoveryDelta']} "
                     f"<span class='verdict-{_esc(a.get('verdict'))}'>{_esc(a.get('verdict'))}</span></td>"
                     f"<td>{a.get('pairedN')}</td><td>{a.get('observedHit')}</td>"
                     f"<td>hit={(a.get('randomShuffle') or {}).get('hit')}/"
                     f"perm={(a.get('randomShuffle') or {}).get('n_perm')}</td></tr>")
        h.append("</table>")
        h.append("<p>口径：|Δrecovery|&lt;2pp 过；FAIL=泄漏警讯→该窗模型臂结果不进结论（prereg redlines[0]）。</p>")
    else:
        h.append("<p>未触发（无臂回收率&gt;1.0 且 W1 未跑）。</p>")
    # —— 窗级明细 ——
    h.append("<h2>窗级明细（臂 × k 回收率矩阵）</h2>")
    for w in WINDOW_ORDER:
        rep = reports.get(w)
        if not rep:
            h.append(f"<h3>{w}（未产出）</h3>")
            continue
        badge = ("<span class='badge-exp'>exploratory·参数回望</span>" if w in ("W1", "W2")
                 else "<span class='badge-adj'>裁决窗</span>")
        a0 = (rep.get("arms", {}).get("A0", {}) or {}).get("k1", {}) or {}
        viol = a0_redline_check(rep)
        a0_txt = f"recovery={a0.get('recovery')}"
        if viol:
            if rep.get("role") == ROLE_ADJUDICATION:
                a0_txt += " <span class='verdict-FAIL'>红线出界·中断</span>"
            else:
                a0_txt += (" <span class='verdict-FAIL'>红线出带（exploratory 窗·排查定性大赛期特征"
                           "·带不放宽·W3 独立自检带内）</span>")
        h.append(f"<h3>{w} {badge}</h3>")
        h.append(f"<p>role={_esc(rep.get('role'))}·rows={rep.get('rows')}·耗时={rep.get('elapsedSec')}s"
                 f"·A0 top1 {a0_txt}（红线带 {A0_RECOVERY_BAND[0]}~{A0_RECOVERY_BAND[1]}）</p>")
        rates = rep.get("unmappedRate") or {}
        h.append("<p class='sub-line'>" + "·".join(
            f"{a} unmapped={rates.get(a)}" for a in ARM_ORDER if rates.get(a) is not None) + "</p>")
        is_adj = rep.get("role") == ROLE_ADJUDICATION
        ths = [f"<th>k{k}" + (" <span class='badge-exp'>exp</span>" if (is_adj and k >= 2) else "")
               + "</th>" for k in range(1, K_MAX + 1)]
        h.append("<table><tr><th>臂</th>" + "".join(ths) + "</tr>")
        for arm in ARM_ORDER:
            cells = []
            for k in range(1, K_MAX + 1):
                v = ((rep.get("arms", {}).get(arm, {}) or {}).get(f"k{k}", {}) or {})
                rec = v.get("recovery")
                hi = " class='hi'" if rec is not None and rec > 1.0 else ""
                cells.append(f"<td{hi}>{'' if rec is None else rec}"
                             f"<span class='sub'>n={v.get('n', 0)}·drop={v.get('dropped', 0)}</span></td>")
            h.append(f"<tr><td>{_esc(ARM_LABELS[arm])}</td>" + "".join(cells) + "</tr>")
        h.append("</table>")
        h.append("<p class='foot'>cell=回收率（派彩/本金·2 元/注·按封盘价结算）；n=有排序场数·drop=无价废注；"
                 "红底=回收率&gt;1.0（触发 leak-audit 红线）"
                 + ("；k2-4 列=exploratory（prereg exploratoryOnly「top2-4」·不进裁决）。" if is_adj else "")
                 + "</p>")
    # —— 限制三条 ——
    h.append("<h2>限制</h2><ol>")
    for t, d in LIMITATIONS:
        h.append(f"<li><b>{_esc(t)}</b>：{_esc(d)}</li>")
    h.append("</ol>")
    # —— 数据来源与耗时 ——
    h.append("<h2>数据来源与耗时</h2>")
    h.append(f"<p>{_esc(SOURCES)}</p>")
    h.append(f"<p>started={_esc(meta.get('startedAt'))}·finished={_esc(meta.get('finishedAt'))}"
             f"·总耗时={_esc(meta.get('totalElapsedSec'))}s</p><ul>")
    for w in WINDOW_ORDER:
        la = (meta.get("leakAudits") or {}).get(w)
        h.append(f"<li>{w}: run={_esc((meta.get('windows') or {}).get(w))}s"
                 + (f"·leak-audit={_esc(la)}s" if la is not None else "") + "</li>")
    h.append("</ul></body></html>")
    return "\n".join(h)

PREREG_FINGERPRINT = ("beta", "min_pool_items", "k_max", "boot_B", "seed", "decision_day_offset")

def check_prereg() -> None:
    """指纹校验（计划承诺·I-2）：run/run-all/leak-audit 启动时断言 prereg frozenParams
    与模块常量逐值相等、windows 三窗存在——不等即 SystemExit(2) 打印差异
    （防跑的代码与冻结判据口径漂移·跑前闸非跑后审计）。开发者 sszhang"""
    doc = _load_prereg()
    frozen = doc.get("frozenParams") or {}
    consts = {"beta": BETA, "min_pool_items": MIN_POOL_ITEMS, "k_max": K_MAX,
              "boot_B": BOOT_B, "seed": SEED, "decision_day_offset": DECISION_DAY_OFFSET}
    diffs = []
    for k, v in consts.items():
        if k not in frozen:
            diffs.append(f"frozenParams.{k} 缺失")
        elif frozen[k] != v:
            diffs.append(f"frozenParams.{k}={frozen[k]!r} ≠ 模块常量 {v!r}")
    diffs += [f"windows.{w} 缺失" for w in WINDOW_ORDER if w not in (doc.get("windows") or {})]
    if diffs:
        print("[PREREG-FINGERPRINT] 判据指纹不一致，拒绝出报告（铁律 14②·跑前闸）：\n  "
              + "\n  ".join(diffs), file=sys.stderr)
        raise SystemExit(2)

def cmd_run(win_name: str) -> int:
    rc, _ = _run_and_archive(win_name)
    return rc

def cmd_leak_audit(win_name: str) -> int:
    rc, _ = run_leak_audit_archived(win_name)
    return rc

def cmd_run_all() -> int:
    """三窗齐跑→W1 必审计+红线触发窗审计→HTML 报告→主判据 verdict 打印（brief Step 4）。
    A0 红线分窗（I-1）：裁决窗（W3）出带 rc=1 中断不续跑；exploratory 窗（W1/W2）出带
    warn 留痕续跑（2026-10-13 归档事实=W2 出带 0.8913 续跑完 W3+HTML）；
    leak-audit FAIL 不中断（不过不进结论·报告标注）。开发者 sszhang"""
    t0_wall, t0 = time.time(), time.perf_counter()
    reports, audits, timings, audit_timings = {}, {}, {}, {}
    for w in WINDOW_ORDER:
        rc, rep = _run_and_archive(w)
        if rc:
            return rc
        reports[w] = rep
        timings[w] = rep.get("elapsedSec")
    for w in WINDOW_ORDER:
        if w == "W1" or _needs_leak_audit(reports[w]):   # W1 必跑（brief Step 3）；其余 redlines[0] 触发
            _, audit = run_leak_audit_archived(w)
            audits[w] = audit
            audit_timings[w] = audit.get("elapsedSec")
    meta = {"startedAt": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t0_wall)),
            "finishedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
            "totalElapsedSec": round(time.perf_counter() - t0, 1),
            "windows": timings, "leakAudits": audit_timings}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / OUT_DIR_NAME).write_text(build_html_report(reports, audits, meta), encoding="utf-8")
    print(f"[run-all] HTML 报告 -> {OUT_DIR / OUT_DIR_NAME}")
    for c in reports.get("W3", {}).get("criteria", []):
        tag = "主判据" if c["role"] == "primary" else "次判据"
        print(f"[{tag}] {c['arm']} vs {c['baseline']} k={c['k']}: diffMean={c['diffMean']} "
              f"ci95={c['ci95']} pairedN={c['pairedN']} verdict={c['verdict']}")
    return 0

def main(argv: list[str] | None = None) -> int:
    """CLI：run <窗> | leak-audit <窗> | run-all——JSON/HTML 归档 data/04-summaries/crs-batch-backtest/。
    窗口名守门=load_window_range（prereg 冻结窗，打错即炸不静默）。开发者 sszhang"""
    import argparse
    ap = argparse.ArgumentParser(description="比分端三层降级链分批回测（W1/W2/W3·判据预注册）")
    ap.add_argument("cmd", choices=["run", "run-all", "leak-audit"])
    ap.add_argument("window", nargs="?", help="W1|W2|W3（prereg 冻结窗）")
    args = ap.parse_args(argv)
    check_prereg()                     # 指纹闸：三命令统一跑前校验（I-2）
    if args.cmd == "run-all":
        return cmd_run_all()
    if not args.window:
        ap.error(f"{args.cmd} 需要窗口参数（prereg 冻结窗：{'/'.join(WINDOW_ORDER)}）")
    return cmd_run(args.window) if args.cmd == "run" else cmd_leak_audit(args.window)

if __name__ == "__main__":
    raise SystemExit(main())
