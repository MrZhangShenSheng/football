#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修正系数消融（闭环 P2-D / I2）：chain 触发 vs 未触发 命中率/RPS 对比 → 人审 diff 建议。

原则：
- 不自动改 SKILL.md——系数是业务规则，输出修订建议供人审（设计文档 §三 I2）
- chain 兼容两种格式：结构化数组 ["R1","保级平局"] / 自由文本 "R1×0.80;战意:高动力"
- 门槛：触发样本 n ≥ 50（corpus readiness.ablateReady）才输出正式结论；未达标输出观察数据
- 判定：触发场命中率低于未触发场 >10pp 且 n≥50 → 建议"降级/删除"；反向或无差 → "维持/标✅已验证"

用法：
  python ablate.py
"""
import json
import re
from pathlib import Path

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
}
DEGRADE_GAP = 0.10  # 触发场命中率低于未触发 10pp → 建议降级


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
        ht = sum(1 for r in trig if r.get("directionHit")) / len(trig)
        hb = (sum(1 for r in base if r.get("directionHit")) / len(base)) if base else None
        entry = {"coeff": coeff, "n_trigger": len(trig), "n_base": len(base),
                 "hit_trigger": round(ht, 3), "hit_base": round(hb, 3) if hb is not None else None}
        if not n_ready:
            entry["status"] = f"观察中（语料门槛 n≥{ABLATE_MIN_N} 未达，当前结论仅参考）"
        elif hb is None:
            entry["status"] = "无未触发对照组，无法对比"
        elif hb - ht > DEGRADE_GAP:
            entry["status"] = f"⚠️ 建议降级/删除：触发场 {ht:.0%} 显著低于对照 {hb:.0%}（差 {(hb - ht):.0%}）"
            entry["diff"] = f"SKILL.md 修正系数『{coeff}』标注 ⚠️证据薄弱 或删除（负增益 -{(hb - ht):.0%}）"
        elif ht >= hb:
            entry["status"] = f"✅ 建议标已验证：触发场 {ht:.0%} ≥ 对照 {hb:.0%}"
            entry["diff"] = f"SKILL.md 修正系数『{coeff}』标注 ✅已验证（正增益 +{(ht - hb):.0%}）"
        else:
            entry["status"] = f"维持：触发场 {ht:.0%} vs 对照 {hb:.0%}（差 {abs(ht - hb):.0%}，未达降级线）"
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
