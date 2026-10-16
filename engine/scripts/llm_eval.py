# engine/scripts/llm_eval.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""llm_eval.py — LLM 预测对拍结算（spec §4·prereg engine/cache/llm-predict-prereg.json）。

主判据=31 格全分布配对 log-loss（market−llm·CI 下限>0）；右尾通道=E_tail 聚合二项
（赔率≥30 键 ∪ {0:3,0:4}·CI 下限>0 且 n≥150）；descriptive=E1/E2/E3。
对手盘=开赛前最后可得快照（lineup_watch 快照链末帧回放，缺则数据卡价+closeProxy 分层）。
开发者 sszhang"""
import json
import math
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import market_lambda as mkl
from strength_chain_eval import _boot_ci_diff
from tail_train import event_outcomes

PREREG_PATH = ROOT / "engine" / "cache" / "llm-predict-prereg.json"
PREDS_ROOT = ROOT / "engine" / "cache" / "llm_preds"
CARDS_ROOT = ROOT / "engine" / "cache" / "llm_cards"
TRENDS_ROOT = ROOT / "data" / "05-trends"
RESULTS_ROOT = ROOT / "data" / "02-results"
OUT_DIR = ROOT / "data" / "04-summaries" / "llm-predict"

BOOT_B, SEED, LOGLLOSS_CLIP, MIN_POOL_ITEMS = 2000, 20261015, 1e-6, 20
TAIL_ODDS_FLOOR = 30.0
TAIL_FIXED_CELLS = ("0:3", "0:4")
N_GATE = {"observe": 30, "formal": 100, "final": 200}
TAIL_N_FORMAL = 150              # prereg tailChannel.pass=CI 下限>0 且 n≥150（review fix 锁闸）

_RESULT_SCORE_RE = re.compile(r"\d+-\d+")          # backfill 现行 result 口径 "h-a"


def _pick_key(k: str) -> str | None:
    """市场层 crs 键三态归一（与 llm_pred.freeze 同逻辑·勿 import 私有代码）：
    pick 键 "1:1" 直通 / sXXsYY→"1:0" / s1sx+中文 other→other_h|d|a；非法 None。"""
    if ":" in k:
        return k
    if k.startswith("s") and len(k) == 6 and k[3] == "s":
        return f"{int(k[1:3])}:{int(k[4:6])}"
    return ("other_h" if k in ("s1sh", "胜其他") else "other_d" if k in ("s1sd", "平其他")
            else "other_a" if k in ("s1sa", "负其他") else None)


def _pick_pool(raw: dict) -> dict:
    """{任意三态键: 赔率} → {pick 键: float}（有价 >1.0 才收）。"""
    out = {}
    for k, v in (raw or {}).items():
        kk = _pick_key(k)
        if kk and isinstance(v, (int, float)) and float(v) > 1.0:
            out[kk] = float(v)
    return out


def actual_key(score: str, pool: dict | None = None) -> str | None:
    """赛果→计分键（终审 fix：池感知）。真实体彩 31 格不含 4:3/4:4/5:3/5:4/4:5/3:5/5:5——
    未定格键须按方向落 other_*（否则按结果相关性静默丢行≈1~1.5%·恰为右尾高比分域）。
    pool 提供时：格键有价返格键，无价返 other_*；pool=None 旧行为（网格≤5 直返）。开发者 sszhang"""
    h, a = (int(x) for x in score.split(":"))
    grid = f"{h}:{a}"
    if h <= 5 and a <= 5 and (pool is None or (pool.get(grid) and float(pool[grid]) > 1.0)):
        return grid
    return "other_h" if h > a else ("other_d" if h == a else "other_a")


def market_devig_on_keys(pool: dict, keys: list[str]) -> dict:
    """power devig（整池 s 键解）→ pick 键交集 → 条件归一（对拍键集口径·两侧同键集才公平）。
    池<MIN_POOL_ITEMS→{}。"""
    if len([v for v in pool.values() if float(v) > 1.0]) < MIN_POOL_ITEMS:
        return {}
    s = mkl.crs_power_devig(pool)
    if not s:
        return {}
    back = {}
    for k, v in s.items():
        kk = _pick_key(k)
        if kk:
            back[kk] = v
    if len(back) < MIN_POOL_ITEMS:
        return {}
    picked = {k: back[k] for k in keys if k in back}
    z = sum(picked.values())
    return {k: v / z for k, v in picked.items()} if z > 0 else {}


def _ll(p: float, hit: bool) -> float:
    p = min(max(p, LOGLLOSS_CLIP), 1 - LOGLLOSS_CLIP)
    return -math.log(p) if hit else -math.log(1 - p)


def tail_keys_of(pool: dict) -> list[str]:
    """右尾键集=有价键中赔率≥30 ∪ 固定 {0:3,0:4}（有价才收）。"""
    out = {k for k, v in pool.items() if float(v) >= TAIL_ODDS_FLOOR}
    out |= {c for c in TAIL_FIXED_CELLS if pool.get(c) and float(pool[c]) > 1.0}
    return sorted(out)


# ── 对手盘（close 池）──────────────────────────────────────────────────
def _replay_odds(snaps: list) -> dict:
    """lineup_watch 快照链（diff 帧）回放 → 末帧五池完整状态（同 lineup_watch.replay 口径：
    键或整池消失记 None→删除）。"""
    state = {}
    for s in snaps:
        for pool, ch in (s.get("odds") or {}).items():
            if ch is None:
                state.pop(pool, None)
                continue
            cur = state.setdefault(pool, {})
            for k, v in ch.items():
                if v is None:
                    cur.pop(k, None)
                else:
                    cur[k] = v
    return state


def close_pool(kickoff_date: str, code: str, card: dict) -> tuple[dict, str]:
    """对手盘池：lineup_watch {kickoff_date}-lineups.json 该场（record.code 匹配）快照链
    末帧 crs（键转 pick）；缺→数据卡市场层，proxy="cardTime"。开发者 sszhang"""
    lu_f = TRENDS_ROOT / f"{kickoff_date}-lineups.json"
    if lu_f.exists():
        try:
            doc = json.loads(lu_f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            doc = {}
        for rec in (doc.get("matches") or {}).values():
            if isinstance(rec, dict) and rec.get("code") == code:
                crs = _replay_odds(rec.get("snapshots") or []).get("crs") or {}
                pool = _pick_pool(crs)
                if len(pool) >= MIN_POOL_ITEMS:
                    return pool, "lineupLast"
                break
    return _pick_pool((card.get("layers") or {}).get("market", {}).get("crs") or {}), "cardTime"


# ── 装载 ──────────────────────────────────────────────────────────────
def _shift_day(d: str, off: int) -> str:
    day = date.fromisoformat(d) + timedelta(days=off)
    return day.isoformat()


def _load_results(days: list[str]) -> dict[str, str]:
    """体彩编号→终场比分（"h:a"）。限销售日±1 的 02-results 当日文件（开赛日仅当日/次日凌晨
    两态·±1 覆盖）；matches/records 两键兼容；score>finalScore>result 三级兼容（backfill 现行
    result="h-a" 转冒号·方向字母 "H" 等非比分拒绝）。"""
    out = {}
    for d in days:
        for off in (-1, 0, 1):
            base = RESULTS_ROOT / _shift_day(d, off)
            # -rN 拆轮文件同读（2026-10-15 盲区实证：同日第二轮赛果在 -r1，仅拼主文件名则
            # 该轮码全丢→n=0）；主文件列最后，同码后写覆盖（主文件=终审版纪律）
            files = sorted(RESULTS_ROOT.glob(base.name + "-r*.json")) + [Path(str(base) + ".json")]
            for f in files:
                if not f.exists():
                    continue
                try:
                    doc = json.loads(f.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                for m in (doc.get("matches") or doc.get("records") or []):
                    code, sc = m.get("code"), m.get("score") or m.get("finalScore")
                    if not sc:
                        r = m.get("result")
                        sc = r.replace("-", ":") if isinstance(r, str) and _RESULT_SCORE_RE.fullmatch(r) else None
                    if code and sc and ":" in str(sc):
                        out[str(code)] = str(sc)
    return out


def _load_day_rows(days: list[str]) -> list[dict]:
    """生产装载：llm_preds{d}.json + 数据卡市场层 + lineup 末帧 + 体彩赛果 → 逐场行。
    无赛果场跳过（结算期未到）；缺卡跳过（无市场兜底池）。"""
    rows, results = [], _load_results(days)
    for d in days:
        # 修正卷优先（rules-registry R008）：-rN 取最大 N，无则主卷
        pf = None
        revs = sorted(PREDS_ROOT.glob(f"{d}-r*.json"),
                      key=lambda p: int(p.stem.split("-r")[-1]))
        if revs:
            pf = revs[-1]
        else:
            cand = PREDS_ROOT / f"{d}.json"
            pf = cand if cand.exists() else None
        if pf is None:
            continue
        preds = json.loads(pf.read_text(encoding="utf-8")).get("preds") or []
        for pr in preds:
            card_f = CARDS_ROOT / d / f'{pr.get("code")}.json'
            if not card_f.exists():
                continue
            card = json.loads(card_f.read_text(encoding="utf-8"))
            pool, proxy = {}, "cardTime"
            for off in (-1, 0, 1):                     # 开赛日=销售日当日/次日凌晨两态，±1 扫 lineups
                pool, proxy = close_pool(_shift_day(d, off), pr["code"], card)
                if proxy == "lineupLast":
                    break
            sc = results.get(pr["code"])
            if not sc or ":" not in sc:
                continue                                # 赛果未回填，跳过
            try:
                ak = actual_key(sc)
            except ValueError:
                continue                                # 非比分脏值，跳过
            rows.append({**pr, "_marketPool": pool, "_actual": ak,
                         "_closeProxy": proxy, "_score": sc})
    return rows


def evaluate(days: list[str]) -> dict:
    rows = _load_day_rows(days)
    diffs_full, diffs_tail, desc = [], [], {"E1": [], "E2": [], "E3": []}
    proxy_split = {"cardTime": [], "lineupLast": []}
    for r in rows:
        keys = [k for k in r["p_crs"] if k in r["_marketPool"]]
        mkt = market_devig_on_keys(r["_marketPool"], keys)
        if not mkt or r["_actual"] not in mkt:
            continue                                    # 池不足/键缺失→双侧同跳
        a = actual_key(r["_score"], r["_marketPool"])   # 池感知：未定格键落 other_*（终审 fix）
        llm_raw = r["p_crs"]
        z = sum(llm_raw.get(k, 0.0) for k in keys)
        llm = {k: (llm_raw.get(k, 0.0) / z if z > 0 else 1.0 / len(keys)) for k in keys}
        # ↑ LLM 侧同样在计分键集上条件归一（prereg keyset 字面·双侧对称）
        diffs_full.append(-math.log(max(mkt[a], LOGLLOSS_CLIP)) + math.log(max(llm.get(a, 0.0), LOGLLOSS_CLIP)))
        proxy_split.setdefault(r["_closeProxy"], []).append(diffs_full[-1])
        tk = tail_keys_of(r["_marketPool"])
        if tk:
            pl = sum(llm.get(k, 0.0) for k in tk)
            pm = sum(mkt.get(k, 0.0) for k in tk)
            hit = a in tk
            diffs_tail.append(_ll(pm, hit) - _ll(pl, hit))
        sc = r.get("_score") or r.get("_actual")        # 兜底：行缺 _score 时 grid 键可反推
        if sc and ":" in str(sc):
            y = event_outcomes(str(sc))
            for j, e in enumerate(("E1", "E2", "E3")):
                desc[e].append(_ll(sum(mkt.get(k, 0.0) for k in _event_keys(e, r["_marketPool"])), bool(y[j]))
                               - _ll(sum(llm.get(k, 0.0) for k in _event_keys(e, r["_marketPool"])), bool(y[j])))

    def verdict(diffs, gate_formal, gate_final):
        """词表映射：代码 FAIL=prereg FALSIFIED/续判终态（n≥200 仍 UNDETECTED 方向→FAIL
        是 prereg verdictRules 的合理外推·终审 LOW 注明）。闸门顺序（review fix·对齐 prereg 字母）：n<formal 一律 INSUFFICIENT（数字照出
        但不判定——防 observe 窗提前 PASS/FAIL 杀轨）；n≥formal 才四态；tail 侧由
        调用方传 gate_formal=150（tailChannel.pass 双条件 CI 下限>0 且 n≥150）。开发者 sszhang"""
        n = len(diffs)
        if n < gate_formal:
            stats = {"n": n, "verdict": "INSUFFICIENT"}
            if n >= N_GATE["observe"]:
                stats["diffMean"] = round(float(np.mean(diffs)), 4)      # 观察数字照出·不判定
            return stats
        ci = _boot_ci_diff(diffs, seed=SEED, n_boot=BOOT_B)
        mean = float(np.mean(diffs))
        v = ("PASS" if ci[0] > 0 else
             "FAIL" if mean <= 0 else
             "UNDETECTED" if mean > 0 and n < gate_final else "FAIL")
        return {"n": n, "diffMean": round(mean, 4), "ci95": [round(float(x), 4) for x in ci], "verdict": v}

    return {"days": days,
            "primary": verdict(diffs_full, N_GATE["formal"], N_GATE["final"]),
            "tailChannel": {**verdict(diffs_tail, TAIL_N_FORMAL, N_GATE["final"]),
                            "gate": "CI 下限>0 且 n≥150 才开影子专项"},
            "descriptive": {e: ({"n": len(d), "diffMean": round(float(np.mean(d)), 4)}
                                if len(d) >= N_GATE["observe"] else {"n": len(d)})
                            for e, d in desc.items()},
            "closeProxySplit": {k: {"n": len(v), "diffMean": round(float(np.mean(v)), 4)}
                                for k, v in proxy_split.items() if v}}


def _event_keys(event: str, pool: dict) -> list[str]:
    """descriptive E1/E2/E3 的市场键聚合（E1 无市场直配键→用 crs 总进球≥5 格+other 三键
    近似，forward 场 ttg 池在卡里，此处按 spec descriptive 简化为 crs 键聚合口径并标注；
    E2/E3 格集与 tail_train.E2_CELLS/E3_CELLS 同口径）。"""
    if event == "E1":
        return [k for k in pool if ":" in k and int(k[0]) + int(k[2]) >= 5] + \
               [k for k in ("other_h", "other_d", "other_a") if k in pool]
    cells = {"E2": [(3, 0), (4, 0), (4, 1), (5, 0), (5, 1), (5, 2), (0, 3), (0, 4), (1, 4), (0, 5), (1, 5), (2, 5)],
             "E3": [(0, 3), (0, 4), (0, 5)]}
    return [f"{h}:{a}" for h, a in cells[event] if f"{h}:{a}" in pool]


def check_prereg() -> dict:
    if not PREREG_PATH.exists():
        raise SystemExit(f"[prereg] 缺 {PREREG_PATH}（铁律14②）")
    pr = json.loads(PREREG_PATH.read_text(encoding="utf-8"))
    fp = pr["frozenParams"]
    expect = {"boot_B": BOOT_B, "seed": SEED, "logloss_clip": LOGLLOSS_CLIP,
              "min_pool_items": MIN_POOL_ITEMS, "tail_odds_floor": TAIL_ODDS_FLOOR,
              "tail_fixed_cells": list(TAIL_FIXED_CELLS), "tail_n_formal": TAIL_N_FORMAL}
    if pr.get("primaryCriterion", {}).get("nGate") != N_GATE:      # 闸门常量双向锁（终审 fix）
        raise SystemExit(f"[prereg] nGate={pr['primaryCriterion']['nGate']} ≠ 模块 N_GATE {N_GATE}")
    for k, v in expect.items():
        if fp.get(k) != v:
            raise SystemExit(f"[prereg] frozenParams.{k}={fp.get(k)} ≠ 模块常量 {v}")
    return pr


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="LLM 预测对拍结算")
    ap.add_argument("cmd", choices=["run"])
    ap.add_argument("days", nargs="*")
    args = ap.parse_args()
    check_prereg()
    days = args.days or sorted(p.stem for p in PREDS_ROOT.glob("*.json"))
    result = evaluate(days)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "eval.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    print(json.dumps({"primary": result["primary"], "tailChannel": result["tailChannel"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
