# engine/scripts/rule_brief.py
"""简报编译器＋回执命令（rules-registry Task 2·设计 §5）。

brief <date>：active_shadow 全量（≤15 条，溢出按 saved 降序）＋候选池简列 →
briefs/{date}.md。幂等保护：当日简报已有回执则拒覆盖。
receipt <date> --rule R001 --action "拦下 周四013 HAFU hh(拟入3串)"：
结构化回执追加（结算钩子据此算拦截账）。
开发者 sszhang
"""
import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import rules_registry as rr  # noqa: E402

BRIEFS_DIR = ROOT / "engine" / "cache" / "rules" / "briefs"
SHADOW_CAP = 15
CANDIDATE_CAP = 10
RECEIPT_MARK = "## 执行回执"


def brief_path(date: str) -> Path:
    return BRIEFS_DIR / f"{date}.md"


def compile_brief(date: str) -> Path:
    doc = rr.load()
    out = brief_path(date)
    if out.exists() and RECEIPT_MARK in out.read_text(encoding="utf-8"):
        raise SystemExit(f"[brief] {out.name} 已含执行回执——拒覆盖（凭证保护）")
    shadow = sorted(doc.get("active_shadow") or [],
                    key=lambda r: (-r["record"]["saved"], -r["record"]["blocked"], r["id"]))
    included, dropped = shadow[:SHADOW_CAP], shadow[SHADOW_CAP:]
    lines = [f"# 规则简报 {date}",
             "",
             f"【影子层·本轮适用 {len(included)} 条】（执行后每条触发须记回执）",
             ""]
    for r in included:
        rec = r["record"]
        badge = "·gate籍" if r["stage"] == "gate" else ""
        lines.append(f"{r['id']} | {r['text']} | 战绩:{rec['saved']}救/{rec['harmed']}误{badge}")
        lines.append(f"     因: {r['why'][:80]}")
    cands = (doc.get("candidates") or [])[:CANDIDATE_CAP]
    if cands:
        lines += ["", f"【候选池提示 {len(cands)} 条·仅提示不强制】", ""]
        lines += [f"{c['id']} | {c['text']}" for c in cands]
    if dropped:
        lines += ["", f"⚠ 影子层溢出未注入 {len(dropped)} 条（按战绩降序截断）: "
                  + " ".join(r["id"] for r in dropped)]
    lines += ["", f"编译于 {datetime.now().isoformat(timespec='seconds')} · "
                  f"rule_brief.py（rule_brief.py receipt 追加回执）"]
    BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


_RECEIPT_RE = re.compile(r"(周[一二三四五六日]\d{3})\s+([A-Za-z+]+)\s+(\S+?)\s*(?:\(|$)")


def parse_action(action: str):
    """'拦下 周四013 HAFU hh(拟入3串)' → (code, market, pick)。非'拦下'动作返回 None。"""
    m = _RECEIPT_RE.search(action)
    if not m:
        return None
    return m.group(1), m.group(2).upper(), m.group(3)


def add_receipt(date: str, rule_id: str, action: str) -> Path:
    doc = rr.load()
    if rule_id not in rr.by_id(doc):
        raise SystemExit(f"[receipt] 规则册无 {rule_id}")
    out = brief_path(date)
    text = out.read_text(encoding="utf-8") if out.exists() else \
        compile_brief(date).read_text(encoding="utf-8")
    stamp = datetime.now().strftime("%H:%M")
    line = f"{rule_id} {stamp} | {action}"
    if RECEIPT_MARK not in text:
        text += f"\n{RECEIPT_MARK} {date}\n{line}\n"
    else:
        text = text.rstrip("\n") + f"\n{line}\n"
    out.write_text(text, encoding="utf-8")
    return out


def read_receipts(date: str) -> list[dict]:
    """回执行 → [{rule, at, action, blocked_code, blocked_market, blocked_pick}]。"""
    p = brief_path(date)
    if not p.exists():
        return []
    rows = []
    in_receipt = False
    for ln in p.read_text(encoding="utf-8").splitlines():
        if ln.startswith(RECEIPT_MARK):
            in_receipt = True
            continue
        if not in_receipt or not ln.strip():
            continue
        m = re.match(r"^(R\d{3})\s+(\S+)\s+\|\s+(.+)$", ln.strip())
        if not m:
            continue
        rule, at, action = m.group(1), m.group(2), m.group(3)
        hit = parse_action(action)
        rows.append({"rule": rule, "at": at, "action": action,
                     "blocked_code": hit[0] if hit else None,
                     "blocked_market": hit[1] if hit else None,
                     "blocked_pick": hit[2] if hit else None})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="规则简报编译器/回执")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("brief")
    b.add_argument("date")
    r = sub.add_parser("receipt")
    r.add_argument("date")
    r.add_argument("--rule", required=True)
    r.add_argument("--action", required=True)
    a = ap.parse_args()
    if a.cmd == "brief":
        out = compile_brief(a.date)
        print(f"[brief] → {out}")
        print(out.read_text(encoding="utf-8"))
    else:
        out = add_receipt(a.date, a.rule, a.action)
        print(f"[receipt] {a.rule} 已记 → {out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
