# engine/scripts/attribution_query.py
"""LLM 数据通道查询总线 v3.7：注册表路由 + 七命令。设计 docs/plans/2026-10-22-llm-data-channel-design.html。
防泄漏：leakRisk=postmatch 的命令拒未完场日期参数。开发者 sszhang"""
import json
import sys
from datetime import date
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
ROOT = SCRIPTS_DIR.parents[1]
REGISTRY_PATH = ROOT / "data" / "00-leagues" / "_data_registry.json"

def load_registry() -> dict:
    """注册表加载（active 条目=可路由）。开发者 sszhang"""
    reg = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    return {k: v for k, v in reg.items() if v.get("status") == "active"}

def _today() -> str:
    return date.today().isoformat()

def _leak_check(cmd: str, date_arg: str | None) -> str | None:
    """postmatch 命令的日期闸：date ≥ today 拒（未来/今日未完场禁查）。开发者 sszhang"""
    reg = load_registry()
    if reg.get(cmd, {}).get("leakRisk") != "postmatch" or not date_arg:
        return None
    if date_arg[:10] >= _today():
        return f"拒查：{date_arg} 未完场（postmatch 闸）"
    return None

def run_query(argv: list[str]) -> dict:
    """总线入口：argv[0]=命令名，注册表路由到实现。命令实现在 Task 2 挂入。开发者 sszhang"""
    if not argv:
        return {"ok": False, "error": "空查询"}
    cmd = argv[0].replace("-", "_").replace("h2h_dist", "h2h_dist")
    reg = load_registry()
    # 命令名归一：h2h-dist → h2h_dist
    canon = {"h2h-dist": "h2h_dist"}
    cmd = canon.get(argv[0], argv[0])
    if cmd not in reg:
        return {"ok": False, "error": f"未知命令 {argv[0]}（可查: {' '.join(sorted(reg))}）"}
    impl = HANDLERS.get(cmd)
    if impl is None:
        return {"ok": False, "error": f"命令 {cmd} 未实现"}
    return impl(argv[1:])

HANDLERS: dict[str, object] = {}   # Task 2 逐命令挂入

def main() -> None:
    """CLI：python engine/scripts/attribution_query.py <命令> [参数]。开发者 sszhang"""
    r = run_query(sys.argv[1:])
    print(json.dumps(r, ensure_ascii=False, indent=1))
    sys.exit(0 if r.get("ok") else 1)

if __name__ == "__main__":
    main()
