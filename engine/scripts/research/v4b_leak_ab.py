# -*- coding: utf-8 -*-
r"""+341.4% 的 A/B 归因：同一脚本、同一数据，只切换时间线口径。

大哥 2026-09-30 要求："+341% 既然不对，跑下之前跑这个结果的回测，按照当前正确逻辑去跑，
我想要看正确值。"

做法：把 v4b_full_backtest.py 的回测主体参数化为 lag_days，跑两遍——
  A 旧口径 lag=0（同日 L 先 B 后，但不后移）：被预测场自己的赛果先入统计 = 自泄漏
  B 新口径 lag=2（common.strict_merged 默认）：预测某场时统计里只有其日期前 ≥2 天的赛果
两版除时间线外完全一致（同数据、同模型、同选腿、同注金、同随机性=无），
故差值可直接归因于泄漏，不掺杂其他改动。

已知对照锚点：verify_production_v4b.py 第 192 行硬编码 expected_profit = 341.4，
commit 14173ab 记录"253票回测+341.4%/命中15.4%/回撤11.7%"。

用法：python engine/scripts/research/v4b_leak_ab.py
开发者 sszhang
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TARGET = ROOT / "engine" / "scripts" / "research" / "v4b_full_backtest.py"
OUT = ROOT / "data" / "04-summaries" / "2026-09-30-v4b-leak-ab.json"

PATS = {
    "nBlind": r"盲测样本：(\d+)场",
    "roiPct": r"收益率:\s*([+-]?[\d.]+)%",
    "nHitTickets": r"命中票数:\s*(\d+)",
    "hitRatePct": r"命中率:\s*([\d.]+)%",
    "costYuan": r"总成本:\s*([\d.]+)元",
    "payoutYuan": r"总派彩:\s*([\d.]+)元",
    "maxDrawdownPct": r"最大回撤:\s*([\d.]+)%",
}


def run(lag: int) -> dict:
    """以指定 lag_days 跑一遍回测，解析汇总行。"""
    src = TARGET.read_text(encoding="utf-8")
    # 注入 lag：strict_merged(tl, [...]) → strict_merged(tl, [...], lag_days=N)
    patched = src.replace(
        "from common import strict_merged",
        f"from common import strict_merged as _sm\n"
        f"strict_merged = lambda a, b: _sm(a, b, lag_days={lag})",
        1,
    )
    if patched == src:
        raise SystemExit("未找到 strict_merged 导入行——脚本结构已变，请人工核对")
    tmp = TARGET.with_name(f"_tmp_v4b_lag{lag}.py")
    tmp.write_text(patched, encoding="utf-8")
    try:
        p = subprocess.run([sys.executable, str(tmp.relative_to(ROOT))],
                           cwd=str(ROOT), capture_output=True, timeout=3600)
        txt = (p.stdout or b"").decode("utf-8", "replace")
    finally:
        tmp.unlink(missing_ok=True)
    got = {}
    for k, pat in PATS.items():
        m = re.search(pat, txt)
        got[k] = float(m.group(1)) if m else None
    if got["roiPct"] is None:
        sys.stderr.write(txt[-2000:])
        raise SystemExit(f"lag={lag} 未解析到收益率，见上方输出尾部")
    return got


def main():
    print("=" * 74)
    print("+341.4% 的 A/B 归因：同一脚本同一数据，只切时间线口径")
    print("=" * 74)
    print("锚点：commit 14173ab 记录 253票 +341.4% / 命中15.4% / 回撤11.7%")
    print("      verify_production_v4b.py:192 硬编码 expected_profit = 341.4\n")

    res = {}
    for tag, lag in (("A 旧口径(lag=0·自泄漏)", 0), ("B 新口径(lag=2·strict_merged)", 2)):
        print(f"跑 {tag} ...")
        res[tag] = run(lag)
        r = res[tag]
        print(f"  盲测 {r['nBlind']:.0f}场  收益率 {r['roiPct']:+.1f}%  "
              f"命中 {r['nHitTickets']:.0f}票/{r['hitRatePct']:.1f}%  "
              f"回撤 {r['maxDrawdownPct']:.1f}%")
        print(f"  成本 {r['costYuan']:.0f}元 派彩 {r['payoutYuan']:.1f}元\n")

    a = res["A 旧口径(lag=0·自泄漏)"]
    b = res["B 新口径(lag=2·strict_merged)"]
    print("=" * 74)
    print("归因")
    print("=" * 74)
    gap = a["roiPct"] - b["roiPct"]
    print(f"  旧口径 {a['roiPct']:+.1f}%  →  正确口径 {b['roiPct']:+.1f}%"
          f"   泄漏虚增 {gap:+.1f}pp")
    print(f"  命中率 {a['hitRatePct']:.1f}% → {b['hitRatePct']:.1f}%"
          f"   回撤 {a['maxDrawdownPct']:.1f}% → {b['maxDrawdownPct']:.1f}%")
    print(f"\n  注：口径修正后回撤从 {a['maxDrawdownPct']:.1f}% 升到 "
          f"{b['maxDrawdownPct']:.1f}%——原先"
          f"'回撤异常小'正是铁律 14 警讯清单里的一条，此处得到实证。")
    print("  两版差异仅 lag_days 一个参数，其余完全相同，故差值可直接归因于自泄漏。")

    OUT.write_text(json.dumps({
        "ranAt": "2026-09-30",
        "ask": "大哥要求按当前正确逻辑重跑 +341% 那个回测，看正确值",
        "anchorClaim": {"roiPct": 341.4, "source": "commit 14173ab / "
                        "verify_production_v4b.py:192", "nTickets": 253,
                        "hitRatePct": 15.4, "maxDrawdownPct": 11.7},
        "A_lag0_leaky": a, "B_lag2_strict": b,
        "leakInflationPp": round(gap, 1),
        "verdict": f"正确值 {b['roiPct']:+.1f}%（lag=2 strict_merged 口径）；"
                   f"旧口径 {a['roiPct']:+.1f}% 系自泄漏虚增 {gap:+.1f}pp",
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\n→ {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
