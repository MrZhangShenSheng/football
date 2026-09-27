"""⑩ drift 特征提取框架（三梯队三期 · docs/2026-09-27-probability-modernization-design.html）。

问题：开售→停售的赔率漂移（市场动量）有增量预测信息（学术共识，低流动性联赛
最强）——odds_drift.py 目前只做"解读工具"，本脚本把它升级为"特征提取器"：
每场从 05-trends 快照链提取首末快照的 HAD 三向概率 delta，落盘为特征文件。

用途：特征先积累（两月+），n≥500 后进融合公式（drift 作为 log-odds 修正项）。
特征结构 {code: {atStart, atEnd, pStart[3], pEnd[3], dpPp[3], nSnap}}。

用法：python drift_feature.py
开发者 sszhang
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "engine" / "cache" / "drift_features.json"


def devig(triple: list[float]) -> list[float]:
    inv = [1.0 / x for x in triple]
    s = sum(inv)
    return [x / s for x in inv]


def main() -> None:
    feats: dict = {}
    for f in sorted((ROOT / "data" / "05-trends").glob("*-odds.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for snap in d.get("snapshots", []):
            at = snap.get("at", "")
            for m in snap.get("matches", []):
                code = m.get("code")
                had = m.get("had") or {}
                try:
                    triple = (float(had["h"]), float(had["d"]), float(had["a"]))
                except (KeyError, TypeError, ValueError):
                    continue
                e = feats.setdefault(code, {"atStart": at, "atEnd": at,
                                            "pStart": devig(list(triple)),
                                            "pEnd": devig(list(triple)), "nSnap": 0})
                e["atEnd"] = at          # 时间序遍历=末次覆盖
                e["pEnd"] = devig(list(triple))
                e["nSnap"] += 1
    for code, e in feats.items():
        e["dpPp"] = [round((b - a) * 100, 2) for a, b in zip(e["pStart"], e["pEnd"])]
        e["pStart"] = [round(x, 4) for x in e["pStart"]]
        e["pEnd"] = [round(x, 4) for x in e["pEnd"]]
    OUT.write_text(json.dumps({"extractedAt": "2026-09-27", "nMatches": len(feats),
                               "features": feats,
                               "note": "首末快照HAD三向概率delta·积累至两月+n≥500后进融合(drift log-odds修正)——③self_clv同源时序"},
                              ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    moved = sum(1 for e in feats.values() if max(abs(x) for x in e["dpPp"]) >= 2.0)
    print(f"[drift] 场次 {len(feats)} · 显著漂移(|Δp|≥2pp) {moved} → {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
