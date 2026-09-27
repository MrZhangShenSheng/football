"""⑨ 轮内相关性重放验证（三梯队三期 · docs/2026-09-27 概率-modernization-design.html）。

问题：串关全中概率=连乘（关间独立假设），但同轮比分类有共同冲击（天气/裁判/
轮换潮）——错误相关 → 连乘系统性高估全中率。本脚本用已结算语料测量高估幅度，
决定 ⑨ 建模深度（copula 或仅校准系数）。

口径：按轮（date）取入串方向腿（inPlan 非空且已结算），n≥2 有效：
  P_连乘 = Π p_final[选向]（关间独立假设下的全中率）
  actual = 该轮入串腿是否全部 directionHit
汇总：实际全中轮次数 vs ΣP_连乘（期望全中轮次数）——比值 <1 = 连乘高估 = 正相关。

用法：python replay_correlation.py
开发者 sszhang
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "data" / "04-summaries" / "intra-round-correlation-report.json"

DIR_KEY = {"主": 0, "平": 1, "客": 2}


def main() -> None:
    corpus = json.loads((ROOT / "data" / "04-summaries" / "corpus.json").read_text(encoding="utf-8"))
    rounds: dict[str, list] = {}
    for r in corpus.get("records", []):
        if r.get("directionHit") is not None and r.get("p_final") and r.get("date"):  # inPlan未透传→全腿口径(样本大·同样测轮内相关)
            pick = str(r.get("pick") or "")
            idx = next((i for k, i in DIR_KEY.items() if k in pick), None)
            if idx is None:
                continue
            rounds.setdefault(r["date"], []).append(
                {"code": r.get("code"), "p": float(r["p_final"][idx]), "hit": bool(r["directionHit"])})
    detail = []
    n_rounds = n_all_hit = 0
    sum_p_joint = 0.0
    for date, legs in sorted(rounds.items()):
        if len(legs) < 2:
            continue
        p_joint = 1.0
        for l in legs:
            p_joint *= l["p"]
        all_hit = all(l["hit"] for l in legs)
        n_rounds += 1
        sum_p_joint += p_joint
        n_all_hit += 1 if all_hit else 0
        detail.append({"date": date, "nLegs": len(legs), "pJoint": round(p_joint, 4),
                       "hits": sum(1 for l in legs if l["hit"]), "allHit": all_hit})
    ratio = (n_all_hit / sum_p_joint) if sum_p_joint else None
    verdict = ("连乘高估全中率（实际/期望 <0.8）——正相关性确认，值得 copula/共同因子建模"
               if ratio is not None and ratio < 0.8
               else ("连乘轻微高估——校准系数即可（三期不做重模型）"
                     if ratio is not None and ratio < 1.0 else "无高估证据——维持连乘"))
    print(f"[corr] 有效轮次 {n_rounds} | 实际全中 {n_all_hit} | Σ连乘期望 {sum_p_joint:.2f}")
    print(f"[corr] 实际/期望 = {ratio and round(ratio, 3)} → {verdict}")
    REPORT.write_text(json.dumps({"at": "2026-09-27", "nRounds": n_rounds,
                                  "nAllHit": n_all_hit, "sumPJoint": round(sum_p_joint, 3),
                                  "actualOverExpected": round(ratio, 3) if ratio else None,
                                  "verdict": verdict, "detail": detail[-20:]},
                                 ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"[corr] → {REPORT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
