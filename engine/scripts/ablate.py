#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修正系数消融（闭环 P2-D / I2）：chain 触发 vs 未触发 概率质量对比 → 人审 diff 建议。

原则：
- 不自动改 SKILL.md——系数是业务规则，输出修订建议供人审（设计文档 §三 I2）
- chain 兼容两种格式：结构化数组 ["R1","保级平局"] / 自由文本 "R1×0.80;战意:高动力"
- 门槛：触发样本 n ≥ 50（corpus readiness.ablateReady）才输出正式结论；未达标输出观察数据
- 判定（P0 指标升级 2026-08-29）：主判据=RPS diff 95% bootstrap CI（触发−对照，负=触发组
  概率质量更好；CI 下界>0 显著变差→建议降级，上界<0 显著变好→标已验证，跨 0→维持）；
  方向命中率降级为展示项；概率样本不足时回退旧 10pp 命中率线

用法：
  python ablate.py
"""
import json
import random
import re
from pathlib import Path

from attribute import result_to_idx
from backtest import logloss, rps
from common import log, ROOT
from corpus import ABLATE_MIN_N

CORPUS = ROOT / "data" / "04-summaries" / "corpus.json"
OUT = ROOT / "data" / "04-summaries" / "ablate-report.json"

# 已知系数关键词（chain 文本/数组中匹配）→ 系数名
COEFF_PATTERNS = {
    "开季修正": r"^R[123]$|开季|R1×|R2×|R3×",
    "联赛波动": r"波动|×1\.5|瑞超|挪超|芬超",
    "保级平局保护": r"保级|六分战",
    "首回合平局保护": r"首回合|两回合",
    "战意状态机": r"战意|留力|生死战|高动力|低动力",
    "平局率修正": r"平局率",
    "伤停差值": r"伤停差值|主力缺阵|门将缺阵",
}
DEGRADE_GAP = 0.10  # 触发场命中率低于未触发 10pp → 建议降级（旧线，概率样本不足时回退）
PROB_SUM_TOL = 0.05  # p_final 三向求和容差（防非归一脏数据入概率指标）


def _prob_vals(recs: list[dict]) -> tuple[list[float], list[float]]:
    """收集可算概率指标的 (rps_vals, logloss_vals)。

    只吃归一三向数组 + 可解析结果；标量 p_final（老 schema 个别场）跳过。
    """
    rps_vals, ll_vals = [], []
    for r in recs:
        oi = result_to_idx(r.get("result"))
        p = r.get("p_final")
        if oi is None or not isinstance(p, list) or len(p) != 3:
            continue
        try:
            probs = [float(x) for x in p]
        except (TypeError, ValueError):
            continue
        if abs(sum(probs) - 1.0) > PROB_SUM_TOL:
            continue
        rps_vals.append(rps(probs, oi))
        ll_vals.append(logloss(probs, oi))
    return rps_vals, ll_vals


def group_metrics(recs: list[dict]) -> dict:
    """组级指标：n / 方向命中率（展示）/ RPS+logloss（主指标，有 p_final+result 的子集）。"""
    n = len(recs)
    hit = (sum(1 for r in recs if r.get("directionHit")) / n) if n else None
    rps_vals, ll_vals = _prob_vals(recs)
    return {"n": n, "hit": round(hit, 3) if hit is not None else None,
            "rps": round(sum(rps_vals) / len(rps_vals), 4) if rps_vals else None,
            "logloss": round(sum(ll_vals) / len(ll_vals), 4) if ll_vals else None,
            "n_prob": len(rps_vals)}


def boot_ci_diff(vals_a: list[float], vals_b: list[float],
                 n_boot: int = 1000, seed: int = 42) -> list[float] | None:
    """均值差 (a−b) 的 bootstrap 95% CI。固定种子可复现；任一组空 → None。"""
    if not vals_a or not vals_b:
        return None
    rng = random.Random(seed)
    diffs = []
    for _ in range(n_boot):
        sa = [vals_a[rng.randrange(len(vals_a))] for _ in vals_a]
        sb = [vals_b[rng.randrange(len(vals_b))] for _ in vals_b]
        diffs.append(sum(sa) / len(sa) - sum(sb) / len(sb))
    diffs.sort()
    return [round(diffs[int(0.025 * n_boot)], 4),
            round(diffs[min(int(0.975 * n_boot), n_boot - 1)], 4)]


def parse_chain(rec: dict) -> list[str]:
    """chain → 系数名列表（数组直接用；文本按分隔符拆+模式匹配）。"""
    ch = rec.get("chain")
    if not ch:
        return []
    if isinstance(ch, list):
        parts = [str(x) for x in ch]
    else:
        parts = re.split(r"[;；,，]| and ", str(ch))
    names = []
    for kw, pat in COEFF_PATTERNS.items():
        if any(re.search(pat, p) for p in parts):
            names.append(kw)
    return names


def main() -> None:
    if not CORPUS.exists():
        log("ablate", "缺 corpus.json（先跑 corpus.py）")
        return
    c = json.loads(CORPUS.read_text(encoding="utf-8"))
    filled = [r for r in c.get("records", []) if r.get("result") and "-" in str(r.get("result"))
              and r.get("directionHit") is not None]
    n_ready = c.get("readiness", {}).get("ablateReady", False)

    # 触发/未触发分组（按系数）
    report = []
    for coeff in COEFF_PATTERNS:
        trig = [r for r in filled if coeff in parse_chain(r)]
        base = [r for r in filled if coeff not in parse_chain(r)]
        if len(trig) < 5:
            report.append({"coeff": coeff, "n_trigger": len(trig), "status": "样本不足（<5 触发场），继续观察"})
            continue
        tm, bm = group_metrics(trig), group_metrics(base)
        rt, _ = _prob_vals(trig)
        rb, _ = _prob_vals(base)
        ci = boot_ci_diff(rt, rb)
        entry = {"coeff": coeff, "n_trigger": tm["n"], "n_base": bm["n"],
                 "hit_trigger": tm["hit"], "hit_base": bm["hit"],   # 展示项
                 "rps_trigger": tm["rps"], "rps_base": bm["rps"],
                 "logloss_trigger": tm["logloss"], "logloss_base": bm["logloss"],
                 "n_prob": tm["n_prob"], "rps_diff_ci95": ci}
        if not n_ready:
            entry["status"] = f"观察中（语料门槛 n≥{ABLATE_MIN_N} 未达，当前结论仅参考）"
        elif ci is not None:
            # 主判据：RPS diff 95% CI（触发−对照；RPS 小=好，diff>0=触发组更差）
            if ci[0] > 0:
                entry["status"] = f"⚠️ 建议降级/删除：触发组 RPS 显著更差（diff CI95 [{ci[0]}, {ci[1]}] 全在 0 上方）"
                entry["diff"] = f"SKILL.md 修正系数『{coeff}』降级或删除（RPS 负增益 CI [{ci[0]}, {ci[1]}]）"
            elif ci[1] < 0:
                entry["status"] = f"✅ 建议标已验证：触发组 RPS 显著更好（diff CI95 [{ci[0]}, {ci[1]}] 全在 0 下方）"
                entry["diff"] = f"SKILL.md 修正系数『{coeff}』标注 ✅已验证（RPS 正增益 CI [{ci[0]}, {ci[1]}]）"
            else:
                entry["status"] = f"维持：RPS diff CI95 [{ci[0]}, {ci[1]}] 跨 0（方向性证据不足，命中率 {tm['hit']:.0%} vs {bm['hit']:.0%} 仅展示）"
        elif tm["hit"] is not None and bm["hit"] is not None and bm["hit"] - tm["hit"] > DEGRADE_GAP:
            # 回退：概率样本不足（无 p_final 三向）时沿用旧命中率降级线
            entry["status"] = f"⚠️ 建议降级/删除（回退口径·概率样本不足）：触发场 {tm['hit']:.0%} 低于对照 {bm['hit']:.0%} 超 {DEGRADE_GAP:.0%}"
            entry["diff"] = f"SKILL.md 修正系数『{coeff}』标注 ⚠️证据薄弱 或删除"
        else:
            entry["status"] = f"维持：触发场 {tm['hit']:.0%} vs 对照 {bm['hit']:.0%}（概率样本不足，回退口径未达降级线）"
        report.append(entry)

    OUT.write_text(json.dumps({"generatedAt": c.get("generatedAt"),
                               "n_filled": len(filled), "gateReady": n_ready,
                               "humanReview": True, "report": report},
                              ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log("ablate", f"已回填 {len(filled)} 场 · 门槛{'✅' if n_ready else '未达（结论仅参考）'} → {OUT.relative_to(ROOT)}")
    for e in report:
        log("ablate", f"  {e['coeff']}: {e['status']}")
    log("ablate", "⚠️ 本报告只出建议不自动改 SKILL.md——系数修订需人审（设计文档 I2）")


if __name__ == "__main__":
    main()
