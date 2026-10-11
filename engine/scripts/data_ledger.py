# engine/scripts/data_ledger.py
# -*- coding: utf-8 -*-
"""v35 P2 数据地基账本——五源质量指标自动生成（设计 docs/2026-10-11-v35-foundation-design.html §三）。

产物 data/04-summaries/data-quality-ledger.json·随 run.py update 自动刷新（防过期档案）。
开发者 sszhang
"""
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEDGER_OUT = ROOT / "data" / "04-summaries" / "data-quality-ledger.json"

# 静态事件史（口径/断粮事故——账本须记伤疤不记功绩）
INCIDENTS = [
    {"date": "2026-10-11", "source": "fd", "event": "Psh/Psch 口径混价：2425/2526 静默用赛前价·已修复 g(PPCH,Psch) 重拉 51 文件 100% 对账"},
    {"date": "2026-09-07", "source": "shadow", "event": "scratch 目录清理误删影子账本——凭证禁放 scratch 纪律起点"},
]


def scan_sporttery(d: Path) -> dict:
    files = sorted(d.glob("*.json")) if d.exists() else []
    matches = updated = 0
    for f in files:
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except Exception:                  # noqa: BLE001
            continue
        for blk in doc.get("matchDays") or []:
            for m in blk.get("matches") or []:
                matches += 1
                if m.get("oddsUpdatedAt"):
                    updated += 1
    return {"archives": len(files), "dayMin": files[0].stem if files else None,
            "dayMax": files[-1].stem if files else None,
            "matches": matches, "oddsUpdatedMatches": updated,
            "调价率": round(updated / matches, 4) if matches else None}


def scan_pinclose(d: Path) -> dict:
    files = sorted(d.glob("*.json")) if d.exists() else []
    n = with_pc = 0
    for f in files:
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except Exception:                  # noqa: BLE001
            continue
        for m in doc.get("matches") or []:
            n += 1
            if m.get("pinClose"):
                with_pc += 1
    return {"files": len(files), "matches": n, "withPinClose": with_pc,
            "锚覆盖率": round(with_pc / n, 4) if n else None}


def scan_hst(glob_root: str = None) -> dict:
    """glob 兜底扫 hst_{lg}.json（不锁死目录）。"""
    root = Path(glob_root) if glob_root else ROOT / "engine" / "cache"
    out = {}
    for f in sorted(root.rglob("hst_*.json")):
        try:
            rows = json.loads(f.read_text(encoding="utf-8")).get("rows") or []
        except Exception:                  # noqa: BLE001
            continue
        dates = [r.get("date") or r.get("dt") or r.get("day") for r in rows
                 if isinstance(r, dict)]
        dates = [x for x in dates if x]
        out[f.stem.replace("hst_", "")] = {
            "rows": len(rows),
            "lastDate": max(dates) if dates else None,
            "fileMtime": datetime.fromtimestamp(f.stat().st_mtime).date().isoformat()}
    return out


def scan_lineups(d: Path) -> dict:
    files = sorted(d.glob("*-lineups.json")) if d.exists() else []
    entries = published = inj = 0
    for f in files:
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except Exception:                  # noqa: BLE001
            continue
        for m in (doc.get("matches") or {}).values():
            entries += 1
            if m.get("lineup"):
                published += 1
            i = m.get("injuries") or m.get("injury") or {}
            inj += len(i) if isinstance(i, (list, dict)) else 0
    return {"files": len(files), "matchEntries": entries,
            "lineupPublished": published, "injuryEvents": inj,
            "伤停轨判据进度": f"{entries}/300场·{inj}/30变更"}


def scan_aliases() -> dict:
    f = ROOT / "data" / "01-teams" / "_aliases.json"
    try:
        n = len(json.loads(f.read_text(encoding="utf-8")))
    except Exception:                      # noqa: BLE001
        n = None
    return {"entries": n}


def build() -> dict:
    return {"generatedAt": datetime.now().isoformat(timespec="seconds"),
            "sources": {
                "sporttery": scan_sporttery(ROOT / "engine" / "cache" / "score_odds"),
                "pinnacle锚": scan_pinclose(ROOT / "data" / "02-results"),
                "hst": scan_hst(),
                "lineup伤停轨": scan_lineups(ROOT / "data" / "05-trends"),
                "aliases": scan_aliases()},
            "incidents": INCIDENTS}


def audit(ledger: dict) -> str:
    """抽样对账：账本数字 vs 源文件直算（≥10 条）。"""
    checks = []
    sp = ledger["sources"]["sporttery"]
    live = scan_sporttery(ROOT / "engine" / "cache" / "score_odds")
    checks.append(("sporttery.archives", sp["archives"], live["archives"]))
    checks.append(("sporttery.matches", sp["matches"], live["matches"]))
    checks.append(("sporttery.调价率", sp["调价率"], live["调价率"]))
    pc = ledger["sources"]["pinnacle锚"]
    live_pc = scan_pinclose(ROOT / "data" / "02-results")
    checks += [("pinClose.withPinClose", pc["withPinClose"], live_pc["withPinClose"]),
               ("pinClose.锚覆盖率", pc["锚覆盖率"], live_pc["锚覆盖率"])]
    lu = ledger["sources"]["lineup伤停轨"]
    live_lu = scan_lineups(ROOT / "data" / "05-trends")
    checks += [("lineup.files", lu["files"], live_lu["files"]),
               ("lineup.matchEntries", lu["matchEntries"], live_lu["matchEntries"]),
               ("lineup.lineupPublished", lu["lineupPublished"], live_lu["lineupPublished"]),
               ("lineup.injuryEvents", lu["injuryEvents"], live_lu["injuryEvents"])]
    al = ledger["sources"]["aliases"]
    checks.append(("aliases.entries", al["entries"], scan_aliases()["entries"]))
    hst = ledger["sources"]["hst"]
    live_hst = scan_hst()
    for lg in list(hst)[:2]:
        checks.append((f"hst.{lg}.rows", hst[lg]["rows"], live_hst.get(lg, {}).get("rows")))
    bad = [(k, a, b) for k, a, b in checks if a != b]
    return f"PASS · {len(checks)} 条抽样一致" if not bad else f"FAIL——{bad[:3]}"


def main() -> None:
    if "--audit" in sys.argv:
        print(audit(json.loads(LEDGER_OUT.read_text(encoding="utf-8"))))
        return
    ledger = build()
    LEDGER_OUT.parent.mkdir(parents=True, exist_ok=True)
    LEDGER_OUT.write_text(json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[ledger] 五源账本 → {LEDGER_OUT}")
    print(json.dumps(ledger["sources"], ensure_ascii=False, indent=1)[:800])


if __name__ == "__main__":
    main()
