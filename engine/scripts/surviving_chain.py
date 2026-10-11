# engine/scripts/surviving_chain.py
# -*- coding: utf-8 -*-
"""v35-P4 D6 幸存链注册表——生产组件白名单+PASS 凭证（铁律15 机器闸门）。

无审计不入链：每个生产概率源组件在 surviving_chain.json 挂凭证（unit+CI+审计日期）；
核心组件启动校验，缺/过期/非 keep → 拒跑（SystemExit 2）。
fail-open 只适用仪表（evidence hook）；闸门是准入不是仪表，拒跑是正道。
开发者 sszhang
"""
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "engine" / "cache" / "strength_chain" / "surviving_chain.json"

STATUS_KEEP = "keep"          # 留任主概率源（可作启动闸校验对象）
STATUS_DEMOTE = "demote"      # 降级层/调味（不得作主干）
STATUS_RETIRED = "retired"    # 退场（历史档案）
MAX_AUDIT_AGE_DAYS = 90       # 凭证有效期


def load_registry() -> dict:
    """缺文件/脏文件返回空注册表（由 check_component 给出拒跑原因，不在这里抛）。"""
    try:
        return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception:                      # noqa: BLE001
        return {"schema": 1, "components": {}}


def check_component(name: str, max_age_days: int = MAX_AUDIT_AGE_DAYS) -> tuple:
    """(ok, reason)。ok=True 仅当：条目存在 + status==keep + 审计未过期。"""
    reg = load_registry()
    comp = (reg.get("components") or {}).get(name)
    if not comp:
        return False, f"[surviving_chain] 组件 {name} 不在注册表——无审计不入链（铁律15），先过消融审计"
    if comp.get("status") != STATUS_KEEP:
        return False, f"[surviving_chain] 组件 {name} status={comp.get('status')} 非 keep——不得作主干"
    audited = str(comp.get("auditedAt") or "")
    try:
        age = (date.today() - date.fromisoformat(audited)).days
    except ValueError:
        return False, f"[surviving_chain] 组件 {name} auditedAt 非法: {audited!r}"
    if age > max_age_days:
        return False, f"[surviving_chain] 组件 {name} 审计过期（{audited}·{age}天>{max_age_days}）——重审后才可跑"
    if age < 0:
        return False, f"[surviving_chain] 组件 {name} 审计日期在未来（{audited}）——凭证非法"
    return True, f"PASS · {name} 凭证 {audited}（{age}天）"


def require_component(name: str) -> None:
    """启动闸：不通过直接 SystemExit(2)——防未审计组件混入生产。"""
    ok, why = check_component(name)
    if not ok:
        print(why, file=sys.stderr)
        raise SystemExit(2)
    print(f"[surviving_chain] {why}")
