# engine/scripts/rules_registry.py
"""规则册读写与校验（rules-registry 系统·docs/plans/2026-10-16-rules-registry-design.html）。

规则册=唯一"预测生效"源（大哥 2026-10-16 拍板 A）。三段：candidates/active_shadow/retired，
gates 索引指向已转正入代码的规则。生命周期与冻结判据见设计 §3。
开发者 sszhang
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "engine" / "cache" / "rules" / "registry.json"

STAGES = ("candidate", "shadow", "gate", "retired")
FORMS = ("gate", "checklist")
SEGMENTS = ("candidates", "active_shadow", "retired")
_STAGE_TO_SEGMENT = {"candidate": "candidates", "shadow": "active_shadow",
                     "gate": "active_shadow", "retired": "retired"}
RECORD_KEYS = ("rounds", "blocked", "saved", "harmed")


def load(path: Path | None = None) -> dict:
    p = path or REGISTRY_PATH
    return json.loads(p.read_text(encoding="utf-8"))


def save(doc: dict, path: Path | None = None) -> None:
    p = path or REGISTRY_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def iter_rules(doc: dict):
    for seg in SEGMENTS:
        for r in doc.get(seg) or []:
            yield seg, r


def by_id(doc: dict) -> dict:
    return {r["id"]: r for _, r in iter_rules(doc)}


def validate(doc: dict) -> list[str]:
    """结构校验：id 唯一/段-stage 一致/form-stage 组合/gate 必带 trigger/record 齐全。"""
    issues, seen = [], set()
    for seg, r in iter_rules(doc):
        rid = r.get("id")
        if not rid:
            issues.append(f"{seg}: 缺 id")
            continue
        if rid in seen:
            issues.append(f"{rid}: 重复")
        seen.add(rid)
        stage = r.get("stage")
        if stage not in STAGES:
            issues.append(f"{rid}: stage 非法 {stage}")
        elif _STAGE_TO_SEGMENT.get(stage) != seg:
            issues.append(f"{rid}: stage={stage} 不在段 {seg}")
        form = r.get("form")
        if form not in FORMS:
            issues.append(f"{rid}: form 非法 {form}")
        if form == "gate" and stage in ("shadow", "gate") and not r.get("trigger"):
            issues.append(f"{rid}: gate 型 {stage} 段必须带 trigger")
        if not r.get("text"):
            issues.append(f"{rid}: 缺 text")
        rec = r.get("record") or {}
        for k in RECORD_KEYS:
            if k not in rec:
                issues.append(f"{rid}: record 缺 {k}")
    for rid, g in (doc.get("gates") or {}).items():
        if rid not in seen:
            issues.append(f"gates.{rid}: 指向不存在的规则")
        elif not (g.get("module") and g.get("since")):
            issues.append(f"gates.{rid}: 缺 module/since")
    return issues


def record_tick(doc: dict, rule_id: str, *, blocked=0, saved=0, harmed=0, rounds=0) -> bool:
    """结算钩子回写战绩。返回是否写中。"""
    r = by_id(doc).get(rule_id)
    if not r:
        return False
    rec = r["record"]
    rec["rounds"] += rounds
    rec["blocked"] += blocked
    rec["saved"] += saved
    rec["harmed"] += harmed
    return True


if __name__ == "__main__":
    doc = load()
    bad = validate(doc)
    for i in bad:
        print(f"[rules] ✗ {i}", file=sys.stderr)
    n = {seg: len(doc.get(seg) or []) for seg in SEGMENTS}
    print(f"[rules] 规则册 OK：候选 {n['candidates']} / 影子 {n['active_shadow']} / "
          f"退役 {n['retired']} / gates {len(doc.get('gates') or {})}" if not bad else
          f"[rules] {len(bad)} 处问题", file=sys.stderr if bad else sys.stdout)
    sys.exit(1 if bad else 0)
