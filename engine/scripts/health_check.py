#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""health_check.py — 项目健康巡检器（2026-10-12 主公拍板·批一）。

设计定位（docs 审计三族：静默失败/人肉拼接/纪律自觉——本件收口「静默失败」）：
  - 消费 data_ledger.py（五源质量账本）的产物，不重扫五源；
  - 只补 data_ledger 没有的缺口探测器：fd 断粮症状 / data_check 可运行性 /
    corrupt 堆积 / 久挂票 / S8 缺理由 / 命中字段 None 占比 / 账本新鲜度；
  - 红黄绿分级：红=阻断级（工具崩/账本坏），黄=注意，绿=健康；
  - fail-open 铁律：单探测器异常标 ERR 不炸整体——巡检器自己绝不能成为新断点。
挂载：预测与回填流程末尾各一发（CLAUDE.md 协议表）；产出滚动覆盖
data/04-summaries/health-check.json（不带日期防堆积，历史靠 git）。
开发者 sszhang
"""
import glob
import json
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RED, YELLOW, GREEN, ERR = "red", "yellow", "green", "err"
FD_STALE_DAYS_YELLOW, PENDING_STALE_DAYS, NONE_PCT_YELLOW = 7, 7, 10


def _days(d0: date) -> int:
    return (date.today() - d0).days


def _today() -> date:
    return date.today()


def check_ledger_freshness() -> tuple[str, str]:
    """data_ledger 账本新鲜度（>2 天=update 链路可能没跑）。"""
    p = ROOT / "data" / "04-summaries" / "data-quality-ledger.json"
    doc = json.loads(p.read_text(encoding="utf-8"))
    age = _days(datetime.fromisoformat(doc["generatedAt"]).date())
    lvl = GREEN if age <= 2 else YELLOW
    return lvl, f"generatedAt 距今 {age} 天" + ("" if age <= 2 else "（update 链路疑似未跑）")


def check_fd_symptom() -> tuple[str, str]:
    """fd 断粮症状：最近 14 天已回填场的 pinSource==none 占比（>50%=概率锚缺失期）。
    直测 02-results 而非嗅探 fd CSV——症状口径比源口径更贴业务（backfill 输出同款）。"""
    n = pc_none = 0
    d0 = _today() - timedelta(days=14)
    for f in glob.glob(str(ROOT / "data" / "02-results" / "*.json")):
        try:
            stem = date.fromisoformat(Path(f).stem)
        except ValueError:
            continue
        if not (d0 <= stem <= _today()):
            continue
        for m in json.loads(Path(f).read_text(encoding="utf-8")).get("matches", []):
            if not m.get("result"):
                continue
            n += 1
            if m.get("pinSource") == "none":
                pc_none += 1
    if not n:
        return GREEN, "近 14 天无回填样本"
    pct = pc_none / n * 100
    lvl = YELLOW if pct > 50 else GREEN
    return lvl, f"近 14 天回填 {n} 场·无 pinClose {pc_none}（{pct:.0f}%）" + (
        "——fd 断粮期已知挂账" if lvl == YELLOW else "")


def check_data_check_probe() -> tuple[str, str]:
    """data_check.py 可运行性探针（喂体彩在售清单，退出码非 0=协议第 2 步断）。"""
    sm = ROOT / "engine" / "cache" / "sporttery_matches.json"
    if not sm.exists():
        return YELLOW, "无 sporttery_matches.json（update 未跑？）"
    r = subprocess.run([sys.executable, str(Path(__file__).parent / "data_check.py"), str(sm)],
                       capture_output=True, cwd=ROOT, timeout=120)
    if r.returncode != 0:
        return RED, f"data_check 崩溃 rc={r.returncode}: {r.stderr.decode('utf-8', 'ignore')[-120:]}"
    return GREEN, "data_check 可运行"


def check_corrupt_piles() -> tuple[str, str]:
    """.corrupt 转存堆积（cache/shadow/data 三域递归；>0 黄 >5 红）。"""
    hits = []
    for dom in ("engine/cache", "engine/shadow", "data"):
        hits += glob.glob(str(ROOT / dom / "**" / "*.corrupt-*"), recursive=True)
    lvl = RED if len(hits) > 5 else (YELLOW if hits else GREEN)
    return lvl, f"{len(hits)} 个 corrupt 转存" + (f" 例:{Path(hits[0]).name}" if hits else "")


def check_stale_pending_tickets() -> tuple[str, str]:
    """久挂影子票（pending>7 天且无 pendingNote——挂账原因入账本的票不算）。"""
    tickets = json.loads((ROOT / "engine" / "shadow" / "paper_tickets.json").read_text(encoding="utf-8"))
    stale = [t for t in tickets
             if t.get("result") == "pending"
             and str(t.get("date") or "9999") < (_today() - timedelta(days=PENDING_STALE_DAYS)).isoformat()
             and not t.get("pendingNote")]
    lvl = YELLOW if stale else GREEN
    return lvl, f"{len(stale)} 张久挂无note" + (f": {[t['id'] for t in stale[:4]]}" if stale else "")


def check_s8_reason() -> tuple[str, str]:
    """最近 7 天证据链 S8 假设层缺理由场次（排除结论无头案）。"""
    sys.path.insert(0, str(Path(__file__).parent))
    import evidence
    n_bad = 0
    examples = []
    for back in range(7):
        day = (_today() - timedelta(days=back)).isoformat()
        try:
            for key, miss in evidence.check(day).items():
                if any(m == "S8_hypothesis缺理由" or m.endswith("缺理由") for m in miss):
                    n_bad += 1
                    if len(examples) < 3:
                        examples.append(f"{day}/{key}")
        except Exception:                     # noqa: BLE001 —— 单日脏档不炸
            continue
    lvl = YELLOW if n_bad else GREEN
    return lvl, f"{n_bad} 场 S8 缺理由" + (f" 例:{examples}" if examples else "")


def check_none_ratio() -> tuple[str, str]:
    """已回填非排除场的命中字段全 None 占比（pick 格式漂移警讯·memory 实证）。"""
    files = sorted((ROOT / "data" / "02-results").glob("*.json"),
                   key=lambda p: p.stem, reverse=True)[:7]
    n = bad = 0
    for p in files:
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except Exception:                     # noqa: BLE001
            continue
        for m in doc.get("matches", []):
            # pick 为空=早期 schema 历史记录（无预测语义），不计分母——首跑实证 10-05
            # 文件 7 场无 pick 记录混入致假黄 18%（真漂移仅 10-07 一例 TTG 旧格式）
            if not m.get("result") or not m.get("pick") \
                    or str(m.get("pick") or "").startswith("排除"):
                continue
            n += 1
            if m.get("directionHit") is None and m.get("scoreHit") is None \
                    and m.get("optionHit") is None:
                bad += 1
    if not n:
        return GREEN, "近期无非排除回填样本"
    pct = bad / n * 100
    lvl = YELLOW if pct > NONE_PCT_YELLOW else GREEN
    return lvl, f"近 7 文件非排除回填 {n} 场·命中全None {bad}（{pct:.0f}%）"


CHECKS = [
    ("ledgerFreshness", check_ledger_freshness),
    ("fdSymptom", check_fd_symptom),
    ("dataCheckProbe", check_data_check_probe),
    ("corruptPiles", check_corrupt_piles),
    ("stalePending", check_stale_pending_tickets),
    ("s8Reason", check_s8_reason),
    ("noneRatio", check_none_ratio),
]

_LEVEL_ORDER = {RED: 0, ERR: 0, YELLOW: 1, GREEN: 2}


def run() -> dict:
    results = {}
    for name, fn in CHECKS:
        try:
            lvl, msg = fn()
        except Exception as exc:              # noqa: BLE001 —— fail-open：单项炸不炸整体
            lvl, msg = ERR, f"探测器异常: {exc}"
        results[name] = {"level": lvl, "msg": msg}
    overall = min((r["level"] for r in results.values()), key=lambda l: _LEVEL_ORDER[l])
    doc = {"ranAt": datetime.now().isoformat(timespec="seconds"), "overall": overall,
           "results": results}
    # OUT 动态解析（不依赖模块级常量）：ROOT 可注入（测试 tmp fixture），模块级 OUT
    # 钉死真实仓库会在注入时让 relative_to 炸——首测实证
    out = ROOT / "data" / "04-summaries" / "health-check.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    badge = {RED: "🔴", YELLOW: "🟡", GREEN: "🟢", ERR: "⚫"}[overall]
    print(f"[health] {badge} 总体 {overall}")
    for name, r in results.items():
        mark = {RED: "🔴", YELLOW: "🟡", GREEN: "🟢", ERR: "⚫"}[r["level"]]
        print(f"  {mark} {name:16s} {r['msg']}")
    print(f"[health] → {out.relative_to(ROOT)}")
    return doc


if __name__ == "__main__":
    run()
