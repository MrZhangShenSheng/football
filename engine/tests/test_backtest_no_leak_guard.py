# -*- coding: utf-8 -*-
"""铁律 14 机器强制：白名单回测脚本不得回退到自拼 merged 排序。

2026-09-30 事故：肇事行 `merged.sort(key=lambda r: (r[1], 0 if r[0] == "L" else 1))`
让同日联赛库赛果排在待预测场之前 → 回收率虚增 19.7pp。文档纪律约束不住代码，
这里对"结论可引用"的脚本做机器校验：必须用 common.strict_merged，不得自己拼排序。
开发者 sszhang
"""
import re
from pathlib import Path

RESEARCH = Path(__file__).resolve().parents[1] / "scripts" / "research"

# 结论可引用的白名单（research/README.md 同步维护）——这些必须干净
CLEAN = ["clean_eval.py", "leak_mechanism.py", "leak_audit.py",
         "v4b_full_backtest.py", "score_family_fullseason.py",
         "ticket_dynamic_k_backtest.py"]

LEAKY_SORT = re.compile(r'merged\.sort\(\s*key\s*=\s*lambda[^)]*r\[0\]\s*==\s*"L"')


def test_whitelisted_backtests_use_strict_merged():
    """白名单脚本必须 import strict_merged。"""
    for name in CLEAN:
        p = RESEARCH / name
        assert p.exists(), f"{name} 不存在——白名单与实际脚本脱节，请同步 README"
        src = p.read_text(encoding="utf-8")
        assert "strict_merged" in src, (
            f"{name} 未用 common.strict_merged——铁律 14 第 1 条：回测时间线只准用 "
            f"strict_merged，禁止自拼 merged 再 sort")


def test_whitelisted_backtests_have_no_leaky_sort():
    """白名单脚本不得含肇事排序行（leak_mechanism.py 例外：它故意复刻泄漏做对照）。"""
    for name in CLEAN:
        src = (RESEARCH / name).read_text(encoding="utf-8")
        hits = LEAKY_SORT.findall(src)
        if name == "leak_mechanism.py":
            assert hits, "leak_mechanism.py 应保留泄漏复刻用于 A/B 对照"
            continue
        assert not hits, (
            f"{name} 含肇事排序行 merged.sort(key=... r[0] == \"L\")——"
            f"这会让同日联赛库赛果先于待预测场入统计（回测自泄漏，回收率虚增约 20pp）")


def test_production_scripts_never_feed_actual_scores_to_pending_matches():
    """生产脚本待预测场不得带真实赛果——parlay_gate 必须传 None。"""
    lines = (RESEARCH.parents[0] / "parlay_gate.py").read_text(encoding="utf-8").splitlines()
    starts = [i for i, ln in enumerate(lines) if 'merged += [("B"' in ln]
    assert starts, "parlay_gate.py 的 B 行构造已变形，请人工复核是否仍不喂赛果"
    block = "\n".join(lines[starts[0]:starts[0] + 3])
    assert "None, None" in block, (
        "parlay_gate.py 待预测场(B 行)必须以 None 占位赛果——生产链预测未开赛场，"
        "带上真实赛果即等于自泄漏")
