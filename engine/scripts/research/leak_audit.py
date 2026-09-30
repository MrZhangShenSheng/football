# -*- coding: utf-8 -*-
"""回测自泄漏审计：对 hist_odds 盲测类回测脚本跑 A=原样 / B=联赛赛果延后 2 天入统计，并排打印结论行。

背景（2026-09-30）：research 下回测共用 merged=联赛库+体彩盲测、按(日期, 联赛先)排序的滚动统计，
体彩场约六成同一场也在联赛库且日期早 0~1 天 → 被预测场自身赛果先入统计。B 组只让预测日前
≥2 天的联赛赛果入统计，其余代码一字不改；A/B 差距即泄漏贡献。
用法：python engine/scripts/research/leak_audit.py <回测脚本相对路径> [...]
开发者 sszhang"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TMP = ROOT / "engine" / "scripts" / "scratch"   # 补丁副本放 gitignore 目录
ORIG = 'merged = [("L", d, h, a, hg, ag, None) for d, h, a, hg, ag in tl]'
STRICT = ('merged = [("L", (__import__("datetime").date.fromisoformat(d)'
          ' + __import__("datetime").timedelta(days=2)).isoformat(), h, a, hg, ag, None)'
          ' for d, h, a, hg, ag in tl]')
KEYS = ("全段", "窗口", "ROI", "收益率", "命中率", "中奖", "切分", "合计", "总计")
ENV = dict(os.environ, PYTHONIOENCODING="utf-8",
           PYTHONPATH=os.pathsep.join([str(ROOT / "engine/scripts/research"),
                                       str(ROOT / "engine/scripts"), str(ROOT)]))


def run(src, tag, stem):
    TMP.mkdir(parents=True, exist_ok=True)
    p = TMP / f"_leak_{stem}_{tag}.py"
    p.write_text(src, encoding="utf-8")
    r = subprocess.run([sys.executable, str(p)], cwd=str(ROOT), env=ENV, capture_output=True)
    out = r.stdout.decode("utf-8", errors="replace")
    if r.returncode != 0:
        return f"  [运行失败] {r.stderr.decode('utf-8', errors='replace')[-600:]}"
    lines = [l.rstrip() for l in out.splitlines()
             if any(k in l for k in KEYS) and not l.strip().startswith("20")]
    return "\n".join(lines[:30]) or out[-1500:]


if __name__ == "__main__":
    for rel in sys.argv[1:]:
        code = (ROOT / rel).read_text(encoding="utf-8")
        n = code.count(ORIG)
        print(f"\n######## {rel}（merged 构造行命中 {n} 处）########")
        if n == 0:
            print("  无旧构造行：已改用 common.strict_merged，或为循环内 append 写法（需人工核对排序）")
            continue
        stem = Path(rel).stem
        print("—— A 原样 ——")
        print(run(code, "A", stem))
        print("—— B 严格（联赛赛果延后2天入统计）——")
        print(run(code.replace(ORIG, STRICT), "B", stem))
