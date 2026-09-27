"""⑦ 平局亲和度连续分（三梯队二期 · docs/2026-09-27-probability-modernization-design.html）。

设计：平局轨二值门槛（p平≥28%+极差<12pp）→ 连续分。大哥历史拍板"平局信号不在
p平 本身、在'没有方向'的程度（极差是灵魂）"——本脚本用已结算语料 AUC 验证该
直觉并给出最优合成式：

  affinity_v1 = p_draw(DC)                    纯平局概率（单因子基线）
  affinity_v2 = p_draw × (1 − spread)         极差折减（"没有方向"加成）
  affinity_v3 = p_draw × (1 − spread)²        极差平方折减（强胶着权重）

AUC（rank 法自实现）对"实际开平"事件：v2/v3 > v1 = 极差信息有数学增量。
验收（设计文档）：推平腿 affinity 显著高于非平局腿（AUC≥0.6）。

用法：python draw_affinity.py
开发者 sszhang
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "data" / "04-summaries" / "draw-affinity-report.json"


def auc(scores: list[float], labels: list[int]) -> float:
    """rank AUC：正例得分高于负例的概率（自实现，无 sklearn）。"""
    pairs = sorted(zip(scores, labels), key=lambda x: x[0])
    # rank 处理并列（平均秩）
    ranks: list[float] = []
    i = 0
    while i < len(pairs):
        j = i
        while j + 1 < len(pairs) and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        r = (i + j) / 2 + 1  # 平均秩 1-based
        for k in range(i, j + 1):
            ranks.append(r)
        i = j + 1
    n_pos = sum(labels)
    n_neg = len(labels) - n_pos
    if not n_pos or not n_neg:
        return float("nan")
    rank_sum_pos = sum(r for r, (_, l) in zip(ranks, pairs) if l == 1)
    return (rank_sum_pos - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def is_draw(result) -> bool:
    try:
        h, a = str(result).split("-")
        return int(h) == int(a)
    except (ValueError, AttributeError):
        return False


def main() -> None:
    corpus = json.loads((ROOT / "data" / "04-summaries" / "corpus.json").read_text(encoding="utf-8"))
    rows = []
    for r in corpus.get("records", []):
        pdc = r.get("p_dc")
        if not pdc or r.get("result") is None or not isinstance(pdc, (list, tuple)):
            continue
        try:
            pdc = [float(x) for x in pdc]
        except (TypeError, ValueError):
            continue
        spread = max(pdc) - min(pdc)
        rows.append({
            "pick": str(r.get("pick") or ""), "isDraw": 1 if is_draw(r["result"]) else 0,
            "v1": pdc[1], "v2": pdc[1] * (1 - spread), "v3": pdc[1] * (1 - spread) ** 2,
        })
    labels = [x["isDraw"] for x in rows]
    n_draw = sum(labels)
    aucs = {v: round(auc([x[v] for x in rows], labels), 4) for v in ("v1", "v2", "v3")}

    # 推平腿 vs 非推平腿的亲和度对比（设计验收：显著高）
    pick_draw = [x for x in rows if "平" in x["pick"]]
    pick_other = [x for x in rows if "平" not in x["pick"]]
    best = max(aucs, key=aucs.get)
    seg = {
        "nPickDraw": len(pick_draw), "nPickOther": len(pick_other),
        "affinityPickDrawMean": round(sum(x[best] for x in pick_draw) / len(pick_draw), 4) if pick_draw else None,
        "affinityPickOtherMean": round(sum(x[best] for x in pick_other) / len(pick_other), 4) if pick_other else None,
    }
    verdict = ("极差有数学增量（v2/v3 AUC 显著高于纯 p_draw）——平局轨连续分采纳合成式"
               if aucs["v2"] > aucs["v1"] + 0.02 or aucs["v3"] > aucs["v1"] + 0.02
               else "极差无显著增量——维持 p_draw 单因子（大哥'极差是灵魂'直觉在本样本不成立或样本不足）")
    print(f"[affinity] n={len(rows)} 实际开平 {n_draw} | AUC: v1纯p平={aucs['v1']} v2极差折减={aucs['v2']} v3平方={aucs['v3']}")
    print(f"[affinity] 推平腿({seg['nPickDraw']})亲和均值 {seg['affinityPickDrawMean']} vs 其他({seg['nPickOther']}) {seg['affinityPickOtherMean']}")
    print(f"[affinity] 裁决: {verdict}")
    REPORT.write_text(json.dumps({"at": "2026-09-27", "n": len(rows), "nDraw": n_draw,
                                  "auc": aucs, "segments": seg, "verdict": verdict},
                                 ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"[affinity] → {REPORT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
