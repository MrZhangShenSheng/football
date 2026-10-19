# engine/scripts/llm_judge_eval.py
"""llm_judge_eval.py — 结构化自主判断的阅卷器（v3·llm-autonomy-prereg 三判据）。

判据已跑前冻结（engine/cache/llm-autonomy-prereg.json·铁律 14②），本文件只把判据变成代码：
  E_direction  主判据：走向判断 vs 市场去水 argmax 基线（配对·不站边场弃权不计入分母）
  E_goalBand   副判据：进球区间 vs 市场 TTG 众数区间（同宽度窗最大质量·配对）
  E_keyCells   副判据：关键格命中 vs 市场同数量 top 格（keyCells 空的场诚实弃权不计入）
  校准/gapFlags 分层为 descriptiveOnly（prereg·不设门）
afterKickoff 卷不入任何判据（只计数——开赛后冻结的判断有会话污染风险，prereg 红线）。
裁决（prereg verdictRules）：n<100 INSUFFICIENT / CI 下限>0 PASS / 跨0且点估计>0 UNDETECTED /
跨0且点估计≤0 FALSIFIED。CI 复用 strength_chain_eval._boot_ci_diff（冻结实现·seed=20261016）。
基线一律取数据卡市场层（赛前快照），closeProxy=cardTime 标注（同 v1 prereg 口径）。
输出滚动覆盖 data/04-summaries/llm-autonomy-eval.json（可重算指标,非凭证）。
开发者 sszhang"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import llm_judge  # noqa: E402
from strength_chain_eval import _boot_ci_diff  # noqa: E402  判据数学=冻结实现

RESULTS_ROOT = ROOT / "data" / "02-results"
OUT_PATH = ROOT / "data" / "04-summaries" / "llm-autonomy-eval.json"
SEED = 20261016          # prereg frozenParams.seed
N_BOOT = 2000            # prereg frozenParams.boot_B
N_FORMAL = 100           # prereg primaryCriterion.nGate.formal
DIR_TO_SIDE = {"主胜": "h", "平": "d", "客胜": "a"}
BOOT_EPS = 1e-9          # devig 概率并列判定容差


# ── 基线材料（全部取自冻结卡——赛前快照,防用收盘后信息阅卷）─────────────────
def _pick_key(k: str) -> str | None:
    """卡 crs 键 → pick 键（'h:a'/other_h|d|a）。与 llm_judge._market_keys 同族转换
    （四份拷贝系刻意不共享——判据链各自冻结,不因公共 util 变更而连锁）。开发者 sszhang"""
    if ":" in k:
        return k
    if k.startswith("s") and len(k) == 6 and k[3] == "s":
        return f"{int(k[1:3])}:{int(k[4:6])}"
    return {"s1sh": "other_h", "胜其他": "other_h", "s1sd": "other_d", "平其他": "other_d",
            "s1sa": "other_a", "负其他": "other_a"}.get(k)


def _baselines(card: dict) -> dict:
    """市场基线：方向 argmax（并列全取·基线从宽）/ TTG 去水 bins / CRS 去水概率表。

    crs 去水缺失时（卡层 devig 需 ≥20 有价键,池子开档不全的场会缺）退 1/o 归一朴素
    去水——近似而非伪造,排序用途足够。开发者 sszhang"""
    dv = ((card.get("layers") or {}).get("market") or {}).get("devig") or {}
    had = dv.get("had") or {}
    top_p = max(had.values()) if had else None
    argmax = sorted(k for k, v in had.items() if top_p is not None and v >= top_p - BOOT_EPS) \
        if top_p else []
    ttg_bins = {}
    for k, v in (dv.get("ttg") or {}).items():
        try:
            ttg_bins[int(k[1:])] = float(v)
        except (ValueError, TypeError, IndexError):
            continue
    crs_dv = dv.get("crs")
    if not crs_dv:
        raw = (((card.get("layers") or {}).get("market") or {}).get("crs") or {})
        inv = {}
        for k, v in raw.items():
            pk = _pick_key(k)
            try:
                o = float(v)
            except (TypeError, ValueError):
                continue
            if pk and o > 1.0:
                inv[pk] = 1.0 / o
        s = sum(inv.values())
        crs_dv = {k: v / s for k, v in inv.items()} if s else {}
    crs = sorted(((k, float(v)) for k, v in crs_dv.items()))
    return {"dirArgmax": argmax, "ttgBins": ttg_bins, "crsRanked": crs}


def _market_goal_band(bins: dict, width: int) -> tuple[list[int], float]:
    """同宽度连续窗的市场众数区间（s7=7+ 桶对 ≥7 各 bin 复用其质量）。返回 (窗集合, 质量)。"""
    if not bins:
        return [], 0.0

    def mass(a):
        return sum(bins.get(min(b, 7), 0.0) for b in range(a, a + width + 1))

    cands = {a: round(mass(a), 9) for a in range(0, 8)}
    top = max(cands.values())
    return sorted(a for a, m in cands.items() if m >= top - BOOT_EPS), top


def _outcome(score: str) -> tuple[str, int, int] | None:
    m = re.match(r"^\s*(\d+)\s*[-:]\s*(\d+)\s*$", str(score or ""))
    if not m:
        return None
    h, a = int(m.group(1)), int(m.group(2))
    return ("h" if h > a else "a" if a > h else "d"), h, a


def _actual_cell(h: int, a: int) -> str:
    return f"{h}:{a}"


def _cell_hit(cell: str, h: int, a: int, priced: set[str]) -> bool:
    """关键格命中判定：精确格=比分相等；other 三桶=该方向且比分不在市场有价精确格内
    （体彩『胜其他』语义=该方向所有未列出的比分）。开发者 sszhang"""
    if cell.startswith("other_"):
        side = {"other_h": "h", "other_d": "d", "other_a": "a"}[cell]
        return (_outcome(f"{h}-{a}") or ("?", 0, 0))[0] == side \
            and _actual_cell(h, a) not in priced
    return cell == _actual_cell(h, a)


def score_match(j: dict, card: dict, result: str) -> dict:
    """单场阅卷：三判据配对分。不站边/空 keyCells → 该判据 None（弃权）。开发者 sszhang"""
    oc = _outcome(result)
    if oc is None:
        return {"code": j["code"], "unjudgeable": True}
    side, h, a = oc
    bl = _baselines(card)
    priced = {k for k, _ in bl["crsRanked"]}
    out = {"code": j["code"], "result": f"{h}-{a}", "afterKickoff": bool(j.get("afterKickoff")),
           "confidence": j.get("confidence"), "gapFlags": j.get("gapFlags") or [],
           "abstain": False}

    # E_direction
    call = (j.get("direction") or {}).get("call")
    if call == "不站边":
        out["direction"] = None
        out["abstain"] = True
    else:
        m_hit = 1 if side in bl["dirArgmax"] else 0
        j_hit = 1 if DIR_TO_SIDE.get(call) == side else 0
        out["direction"] = {"model": j_hit, "market": m_hit, "diff": j_hit - m_hit}

    # E_goalBand（市场基线=同宽度窗最大质量,并列窗全取·基线从宽）
    gb = j.get("goalBand") or {}
    if bl["ttgBins"] and isinstance(gb.get("lo"), int) and isinstance(gb.get("hi"), int):
        windows, _ = _market_goal_band(bl["ttgBins"], gb["hi"] - gb["lo"])
        j_hit = 1 if gb["lo"] <= h + a <= gb["hi"] else 0
        m_hit = 1 if any(w <= h + a <= w + (gb["hi"] - gb["lo"]) for w in windows) else 0
        out["goalBand"] = {"model": j_hit, "market": m_hit, "diff": j_hit - m_hit}
    else:
        out["goalBand"] = None

    # E_keyCells（空=诚实弃权；基线=市场去水 top-k,边界并列一并计入·基线从宽）
    cells = j.get("keyCells") or []
    if not cells or not priced:
        out["keyCells"] = None
    else:
        ranked = bl["crsRanked"]
        k = min(len(cells), len(ranked))
        boundary = ranked[k - 1][1]
        base_cells = {c for c, p in ranked if p >= boundary - BOOT_EPS}
        j_hit = 1 if any(_cell_hit(c["cell"], h, a, priced) for c in cells) else 0
        m_hit = 1 if _actual_cell(h, a) in base_cells else 0
        out["keyCells"] = {"model": j_hit, "market": m_hit, "diff": j_hit - m_hit,
                           "n": len(cells)}
    return out


def _verdict(n: int, ci: list[float]) -> str:
    """prereg verdictRules。PASS 额外于 n≥30 才有意义（observe 门）,否则 INSUFFICIENT。"""
    if n < N_FORMAL:
        return "INSUFFICIENT"
    if ci[0] > 0:
        return "PASS"
    return "UNDETECTED" if sum(ci) / 2 > 0 else "FALSIFIED"


def _agg(scores: list[dict], key: str) -> dict:
    rows = [s[key] for s in scores if s.get(key)]
    diffs = [r["diff"] for r in rows]
    n = len(diffs)
    ci = _boot_ci_diff(diffs, seed=SEED, n_boot=N_BOOT)
    mh, mk = sum(r["model"] for r in rows), sum(r["market"] for r in rows)
    return {"n": n, "modelHits": mh, "marketHits": mk,
            "modelRate": round(mh / n, 4) if n else None,
            "marketRate": round(mk / n, 4) if n else None,
            "meanDiff": round(sum(diffs) / n, 6) if n else None,
            "ci": ci, "verdict": _verdict(n, ci)}


def _load_results(sale_date: str) -> dict[str, str]:
    p = RESULTS_ROOT / f"{sale_date}.json"
    if not p.exists():
        return {}
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out = {}
    for m in doc.get("matches") or []:
        r = m.get("result")
        if r:
            out[m.get("code")] = r
    return out


def eval_date(sale_date: str, *, root: Path | None = None,
              cards_root: Path | None = None) -> dict | None:
    """阅一个销售日。缺判断卷/缺赛果→ None（诚实降级,不造分母）。

    最早凭证语义（llm_judge.load_volumes）：修正卷重述全量时卷级 now 会重盖所有戳,
    直接读最新卷会把早判好的场误标「开赛后冻结」——故每 code 取最早出现卷的判断,
    补录不洗白 late、早冻结不被重述抹掉。开发者 sszhang"""
    vols = llm_judge.load_volumes(sale_date, root=root)
    if not vols:
        return None
    earliest: dict[str, tuple[dict, dict]] = {}
    for doc in vols:
        for j in doc.get("preds") or []:
            earliest.setdefault(j.get("code"), (doc, j))
    results = _load_results(sale_date)
    if not results:
        return None
    scores, late, missing = [], 0, []
    for code, (doc0, j) in earliest.items():
        if code not in results:
            missing.append(code)
            continue
        if j.get("afterKickoff"):
            late += 1
            continue
        cards = Path(cards_root) if cards_root else llm_judge.CARDS_ROOT
        card_p = cards / sale_date / f"{code}.json"
        card = json.loads(card_p.read_text(encoding="utf-8")) if card_p.exists() else {}
        scores.append(score_match(j, card, results[code]))
    return {"saleDate": sale_date, "preregHash": vols[0].get("preregHash"),
            "nJudged": len(scores), "afterKickoffExcluded": late,
            "noResult": missing, "scores": scores}


def eval_all(*, root: Path | None = None, cards_root: Path | None = None) -> dict:
    """跨日累计阅卷 + 三判据裁决 + 描述性分层（校准/gapFlags）。开发者 sszhang"""
    base = Path(root) if root else llm_judge.JUDGMENTS_ROOT
    dates = sorted(p.stem for p in base.glob("????-??-??.json")) if base.exists() else []
    per_date, all_scores = {}, []
    for d in dates:
        r = eval_date(d, root=root, cards_root=cards_root)
        if r:
            per_date[d] = {k: r[k] for k in ("nJudged", "afterKickoffExcluded", "noResult")}
            all_scores += r["scores"]
        else:
            per_date[d] = "skipped(无判断卷或无赛果)"

    direction = _agg(all_scores, "direction")
    goal = _agg(all_scores, "goalBand")
    cells = _agg(all_scores, "keyCells")

    # descriptive：置信校准（不站边场不计）+ gapFlags 分层
    cal = {}
    for lv in ("high", "medium", "low"):
        rows = [s["direction"]["diff"] for s in all_scores
                if s.get("direction") and s.get("confidence") == lv]
        cal[lv] = {"n": len(rows), "meanDiff": round(sum(rows) / len(rows), 4) if rows else None}
    mono = (cal["high"]["meanDiff"] is not None and cal["medium"]["meanDiff"] is not None
            and cal["low"]["meanDiff"] is not None
            and cal["high"]["meanDiff"] >= cal["medium"]["meanDiff"] >= cal["low"]["meanDiff"])
    gap = {}
    for s in all_scores:
        if not s.get("direction"):
            continue
        g = len([f for f in s.get("gapFlags", [])])
        gap[g] = gap.setdefault(g, []) + [s["direction"]["diff"]]
    gap_strat = {str(k): {"n": len(v), "meanDiff": round(sum(v) / len(v), 4)}
                 for k, v in sorted(gap.items())}

    abstains = sum(1 for s in all_scores if s.get("abstain"))
    report = {
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "prereg": "engine/cache/llm-autonomy-prereg.json",
        "closeProxy": "cardTime（基线取数据卡市场层·赛前快照）",
        "dates": dates, "perDate": per_date,
        "nMatches": len(all_scores), "abstains": abstains,
        "afterKickoffExcluded": sum(1 for s in all_scores if s.get("afterKickoff")),
        "direction": direction, "goalBand": goal, "keyCells": cells,
        "calibration": {**cal, "monotonic": mono},
        "gapStrat": gap_strat,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
    return report


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="llm 自主判断阅卷器（v3 三判据）")
    ap.add_argument("cmd", choices=["run"])
    ap.add_argument("sale_date", nargs="?", default=None,
                    help="缺省=跨日累计阅卷")
    a = ap.parse_args()
    if a.sale_date:
        r = eval_date(a.sale_date)
        if r is None:
            print(f"[eval] {a.sale_date} 无判断卷或无赛果——诚实降级")
            return 1
        print(json.dumps({k: v for k, v in r.items() if k != "scores"},
                         ensure_ascii=False, indent=1))
        return 0
    rep = eval_all()
    print(f"[eval] {rep['nMatches']} 场（{len(rep['dates'])} 日）"
          f"· 弃权 {rep['abstains']} · 开赛后排除 {rep['afterKickoffExcluded']}")
    for name in ("direction", "goalBand", "keyCells"):
        c = rep[name]
        print(f"  {name:<10} n={c['n']:<4} 模型 {c['modelHits']} vs 市场 {c['marketHits']}"
              f"  meanDiff={c['meanDiff']}  CI={c['ci']}  → {c['verdict']}")
    cal = rep["calibration"]
    print(f"  校准(high≥med≥low): {cal['high']['meanDiff']}/{cal['medium']['meanDiff']}"
          f"/{cal['low']['meanDiff']} monotonic={cal['monotonic']}")
    print(f"  → {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
