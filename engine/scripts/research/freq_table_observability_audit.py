# engine/scripts/research/freq_table_observability_audit.py
"""六表条件变量赛前可观测性普查（t5 判废后的同类排查）。

t5 被 blocked_from_calibration 的根因不是样本量，是条件变量赛后才有。
该缺陷与 t5 的统计显著性无关，因此需对六表逐一盘点——转正门
（diff_ci_lo>0 且 n_on>=150）完全没检查这一项，任何一张表哪天补足样本
都会直接拿到 calibration_ready，而其条件变量可能同样赛后不可得。

判据（定性·逐表盘点，无阈值）：
对每张表的 on 条件，列出计算所需输入，标注赛前是否可得。
- 全部赛前可得 → observable（转正门有效）
- 任一赛后才有 → blocked（须加 verdictCap，不受样本量影响）

开发者 sszhang
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FREQ = ROOT / "data" / "04-summaries" / "score-attribution" / "freq-tables.json"

# 逐表盘点：表id → (on条件依赖的输入, 赛前可得?, 说明)
AUDIT = {
    "t1_derby_vs_rest": [
        ("_derbies.json 对阵表", True, "静态表·赛前可查"),
        ("league/team_h/team_a", True, "赛前已知"),
    ],
    "t2_redcard_vs_none": [
        ("fd HR/AR 红牌张数", False, "比赛中产生·赛后才有"),
    ],
    "t3_press_counter_family": [
        ("classify_script 的 result.hg/ag", False, "赛后才有"),
        ("result.ht_hg/ht_ag 半场比分", False, "赛后才有"),
    ],
    "t4_fifa_virus": [
        ("_formats.json FIFA 窗口", True, "静态日历·赛前可查"),
        ("kickoff 开赛日", True, "赛前已知"),
    ],
    "t5_xg_divergence_p75": [
        ("实际进球 hg/ag", False, "赛后才有"),
        ("比赛 xG（fd HxG/AxG）", False, "赛后射门质量算出·非赛前预测 xG"),
    ],
    "t6_england_referee_red": [
        ("league 归属", True, "赛前已知"),
        ("fd HR/AR 红牌", False, "赛后才有"),
        ("referee 字段", None, "源未接入·100% missing"),
    ],
}


def main() -> None:
    tables = json.loads(FREQ.read_text(encoding="utf-8"))["tables"]
    print(f"{'表':<28} {'n_on':>6} {'diff_ci_lo':>11} {'可观测性':<12} 阻塞输入")
    print("-" * 96)
    blocked_ids = []
    for tid, inputs in AUDIT.items():
        t = tables.get(tid, {})
        bad = [n for n, pre, _ in inputs if pre is False]
        unknown = [n for n, pre, _ in inputs if pre is None]
        status = "blocked" if bad else ("no_source" if unknown else "observable")
        if bad:
            blocked_ids.append(tid)
        n_on = t.get("n_on")
        cil = t.get("diff_ci_lo")
        print(f"{tid:<28} {str(n_on):>6} {str(cil):>11} {status:<12}"
              f" {('、'.join(bad) or '—')}")
    print()
    for tid, inputs in AUDIT.items():
        print(f"[{tid}]")
        for name, pre, note in inputs:
            mark = {True: "赛前可得", False: "赛后才有", None: "源未接入"}[pre]
            print(f"   [{mark}] {name} —— {note}")
    print(f"\n判定：{len(blocked_ids)}/6 表条件变量赛后才有 → "
          f"{'、'.join(blocked_ids)}")
    obs = [t for t in AUDIT if not any(p is False for _, p, _ in AUDIT[t])]
    print(f"      {len(obs)}/6 表赛前可观测（转正门有效）→ {'、'.join(obs)}")
    print("\n含义：转正门只检查统计显著性与样本量，未检查条件变量可观测性。"
          "\n      blocked 表即便补足 n_on>=150 且 diff_ci_lo>0，亦不能进 v3.6 校准通道"
          "\n      （生成器预测时取不到条件变量）。建议把可观测性加进 prereg gate。")


if __name__ == "__main__":
    main()
