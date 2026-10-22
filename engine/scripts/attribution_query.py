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

# attribution_query.py 追加
import glob as _glob
from collections import Counter

HIST_DIR = ROOT / "data" / "04-summaries" / "score-attribution" / "hist"
FWD_BILL = ROOT / "data" / "04-summaries" / "score-attribution"

DIM_DICT = {   # dim 命令字典（设计 §二维度表口径）
    "E1": "红牌改写：张数×时点→局面重构项（fd 无分钟时点·张数近似）",
    "E2": "进球时序结构：半场→全场差值（早球开放/锁死分岔·压上反杀族）",
    "E4": "射正背离：两队射正→进球转化 vs 0.30 基准的差",
    "E5": "把握率背离：实际进球−xG（赛后还原维度·勿作事前信号）",
    "A1": "实力差：Elo 差→期望净胜映射（÷400）",
    "L0": "赛制情境：阶段/晋级形势/赛制类型→离散度系数",
}

def _resolve_fd_name(name: str) -> str | None:
    """中文或 fd 英文名 → fd 英文名（账本 match 串口径）。开发者 sszhang"""
    import strength_loaders as sl
    al = sl.load_aliases()
    # 直接是 fd 名？
    for tid, srcs in al.items():
        if srcs.get("fd") == name:
            return name
    # 中文 → id → fd
    tid = sl.zh_to_id().get(name)
    if tid:
        return al.get(tid, {}).get("fd")
    return None

def _load_hist_bills() -> list[dict]:
    """hist 全账本（一次性载入·模块级缓存）。开发者 sszhang"""
    global _HIST_CACHE
    if _HIST_CACHE is None:
        bills = []
        for p in sorted(HIST_DIR.glob("*-attribution.json")):
            bills.extend(b for b in json.loads(p.read_text(encoding="utf-8"))
                         if "baseline" in b)
        _HIST_CACHE = bills
    return _HIST_CACHE

_HIST_CACHE: list[dict] | None = None

def _cmd_formats(args):
    if not args:
        return {"ok": False, "error": "用法: formats <联赛中文名>"}
    doc = json.loads((ROOT / "data" / "00-leagues" / "_formats.json").read_text(encoding="utf-8"))
    entry = doc.get(args[0])
    if not entry:
        return {"ok": False, "error": f"档案无 {args[0]}（有: 意甲 英超 西甲 德甲 法甲 荷甲 葡超 美职 欧冠）"}
    return {"ok": True, "data": entry}

def _cmd_derby(args):
    if len(args) != 2:
        return {"ok": False, "error": "用法: derby <队A> <队B>"}
    tid_a = _zh_id(args[0]); tid_b = _zh_id(args[1])
    # 直接读表（dim_derby 需要联赛中文名——这里按全部联赛组扫）
    table = json.loads((ROOT / "data" / "00-leagues" / "_derbies.json").read_text(encoding="utf-8"))
    hit = None
    for lg, pairs in table.items():
        for a, b in pairs:
            if {tid_a, tid_b} == {a, b}:
                hit = (lg, a, b); break
    if hit:
        return {"ok": True, "data": {"isDerby": True, "league": hit[0],
                "tailScale": 1.4, "note": "先验系数·待频次表校准"}}
    return {"ok": True, "data": {"isDerby": False}}

def _zh_id(name: str) -> str | None:
    import strength_loaders as sl
    return sl.zh_to_id().get(name)

def _cmd_freq(args):
    if not args:
        return {"ok": False, "error": "用法: freq <t1..t6>"}
    doc = json.loads((ROOT / "data" / "04-summaries" / "score-attribution" / "freq-tables.json").read_text(encoding="utf-8"))
    tables = doc.get("tables") or {}
    key = args[0] if args[0] in tables else next((k for k in tables if k.startswith(args[0])), None)
    if not key:
        return {"ok": False, "error": f"无表 {args[0]}（有: {' '.join(tables)}）"}
    t = dict(tables[key])
    t["usage"] = ("可作量化依据" if t.get("verdict") == "calibration_ready"
                  else "未过转正门·只可作叙事线索（禁作 direction/goalBand 量化依据）")
    return {"ok": True, "data": t}

def _cmd_script(args):
    if not args:
        return {"ok": False, "error": "用法: script <队名> [N场·默认10]"}
    fd_name = _resolve_fd_name(args[0])
    if not fd_name:
        return {"ok": False, "error": f"队名 {args[0]} 解析不到 fd 名（别名表无 fd 键或中文不识别）"}
    n = int(args[1]) if len(args) > 1 else 10
    rows = [b for b in _load_hist_bills()
            if fd_name in (b.get("match") or "") and b.get("script", {}).get("type")]
    rows.sort(key=lambda b: b["date"], reverse=True)
    rows = rows[:n]
    dist = Counter(b["script"]["type"] for b in rows)
    return {"ok": True, "data": {
        "team": fd_name, "n": len(rows),
        "window": f"{rows[-1]['date']}~{rows[0]['date']}" if rows else None,
        "dist": dict(dist),
        "note": "剧本指纹（crush碾压/leaky互漏/iron_grid铁桶/open_play对攻等8类）·shape.type 选择参考"}}

def _cmd_bill(args):
    if len(args) != 2:
        return {"ok": False, "error": "用法: bill <日期> <场次码>"}
    err = _leak_check("bill", args[0])
    if err:
        return {"ok": False, "error": err}
    for p in sorted(FWD_BILL.glob("*~*-attribution.json")):
        bills = json.loads(p.read_text(encoding="utf-8"))
        for b in bills:
            if b.get("date") == args[0] and b.get("code") == args[1]:
                return {"ok": True, "data": b}
    return {"ok": False, "error": f"账本无 {args[0]} {args[1]}（已回填场次才有账单）"}

def _cmd_h2h_dist(args):
    if len(args) != 2:
        return {"ok": False, "error": "用法: h2h-dist <队A> <队B>"}
    fa, fb = _resolve_fd_name(args[0]), _resolve_fd_name(args[1])
    if not fa or not fb:
        return {"ok": False, "error": "队名解析失败（至少一队无 fd 名）"}
    dist = Counter()
    for b in _load_hist_bills():
        m = b.get("match") or ""
        if fa in m and fb in m and b.get("result"):
            dist[f"{b['result']['hg']}:{b['result']['ag']}"] += 1
    return {"ok": True, "data": {"teams": f"{fa} vs {fb}", "n": sum(dist.values()),
            "dist": dict(dist.most_common(10)),
            "note": "fd 四季窗内交手·主客按账本主队口径（读取时注意方向）"}}

def _cmd_dim(args):
    if not args or args[0] not in DIM_DICT:
        return {"ok": False, "error": f"用法: dim <{'/'.join(DIM_DICT)}>"}
    return {"ok": True, "data": {"dim": args[0], "desc": DIM_DICT[args[0]]}}

for _name, _fn in (("formats", _cmd_formats), ("derby", _cmd_derby),
                   ("freq", _cmd_freq), ("script", _cmd_script),
                   ("h2h_dist", _cmd_h2h_dist), ("bill", _cmd_bill),
                   ("dim", _cmd_dim)):
    HANDLERS[_name] = _fn

def main() -> None:
    """CLI：python engine/scripts/attribution_query.py <命令> [参数]。开发者 sszhang"""
    r = run_query(sys.argv[1:])
    print(json.dumps(r, ensure_ascii=False, indent=1))
    sys.exit(0 if r.get("ok") else 1)

if __name__ == "__main__":
    main()
