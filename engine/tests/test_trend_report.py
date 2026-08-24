# -*- coding: utf-8 -*-
"""trend_report 测试：轮次聚合 / 累计 log loss / 校准分桶 / in_plan 归一化 / 方案层统计。"""
from trend_report import (
    build_series, build_calibration, build_plans, plan_summary,
    normalize_in_plan, normalize_grade, pick_type, outcome_idx, logloss,
)


def _rec(**kw):
    base = {"date": "2026-08-22", "code": "周六001", "round": "2026-08-22",
            "league": "日职", "stars": 3, "grade": 3, "pick": "主胜", "odds": 1.8,
            "p_final": [0.6, 0.25, 0.15], "result": None, "directionHit": None}
    base.update(kw)
    return base


def test_outcome_idx():
    assert outcome_idx({"result": "2-1"}) == 0
    assert outcome_idx({"result": "1-1"}) == 1
    assert outcome_idx({"result": "0-3"}) == 2
    assert outcome_idx({"result": None}) is None
    assert outcome_idx({"result": "延期"}) is None


def test_series_round_aggregation():
    """跨比赛日同轮次聚合为一个点（round 是轮次 key，非 date）。"""
    recs = [
        _rec(code="周六001", result="2-1", directionHit=True),
        _rec(code="周六002", result="0-1", directionHit=False),
        _rec(code="周日001", result="1-1", directionHit=False),  # 同 round 不同比赛日
    ]
    s = build_series(recs)
    assert len(s["rounds"]) == 1  # 3 场 → 1 轮
    assert s["rounds"][0]["n"] == 3
    assert s["rounds"][0]["cum_n"] == 3
    assert s["rounds"][0]["cum_hit_rate"] == round(1 / 3, 4)


def test_cum_logloss_with_market():
    """累计 log loss：模型线与市场线独立累计。"""
    recs = [
        _rec(p_final=[0.6, 0.25, 0.15], result="2-1"),   # 主胜命中
        _rec(p_final=[0.3, 0.3, 0.4], result="1-1", directionHit=False),  # 平局，模型押客
    ]
    s = build_series(recs)
    row = s["rounds"][0]
    expected = (logloss([0.6, 0.25, 0.15], 0) + logloss([0.3, 0.3, 0.4], 1)) / 2
    assert abs(row["cum_logloss"] - round(expected, 4)) < 1e-9
    assert row["cum_logloss_mkt"] == row["cum_logloss"]  # 无独立市场概率时同值


def test_calibration_bins():
    recs = [
        _rec(p_final=[0.8, 0.1, 0.1], result="2-1", directionHit=True),   # 80% 桶 命中
        _rec(p_final=[0.75, 0.15, 0.1], result="0-1", directionHit=False),  # 75% 桶 未中
        _rec(p_final=[0.45, 0.3, 0.25], result="1-1", directionHit=False),  # 40-55% 桶 未中
    ]
    cal = build_calibration(recs)
    bins = {c["bin"]: c for c in cal}
    assert bins["70%~101%"]["n"] == 2
    assert bins["70%~101%"]["obs"] == 0.5
    assert bins["40%~55%"]["n"] == 1
    assert bins["0%~40%"]["n"] == 0


def test_in_plan_normalization():
    assert normalize_in_plan("False") == "未入串"
    assert normalize_in_plan(False) == "未入串"
    assert normalize_in_plan(None) == "未入串"
    assert normalize_in_plan("A") == "入串A"
    assert normalize_in_plan("C") == "入串C"


def test_pick_type():
    assert pick_type("主胜") == "方向"
    assert pick_type("2-0") == "比分"
    assert pick_type(None) == "方向"


def test_normalize_grade_mixed_schema():
    assert normalize_grade(4) == "D级"
    assert normalize_grade("3") == "C级"
    assert normalize_grade("A") == "D级"
    assert normalize_grade("B") == "C级"
    assert normalize_grade("S") == "?"
    assert normalize_grade(None) == "?"


def test_plan_layer_all_hit_and_break():
    """方案层：全中 / 断关 / 待回填三态。"""
    recs = [
        _rec(code="周六001", result="2-1", directionHit=True),
        _rec(code="周六002", result="1-0", directionHit=True),
        _rec(code="周六003", result="0-2", directionHit=False),
        _rec(code="周六004", result=None),
    ]
    plans = {
        "2026-08-22:A": ["周六001", "周六002"],        # 全中
        "2026-08-22:B": ["周六001", "周六003"],        # 断 1 关
        "2026-08-22:C": ["周六002", "周六004"],        # 待回填
    }
    rows = build_plans(plans, recs)
    by_name = {p["plan"]: p for p in rows}
    assert by_name["A"]["status"] == "全中"
    assert by_name["B"]["status"] == "断1关"
    assert by_name["B"]["breaks"] == ["周六003"]
    assert by_name["C"]["status"] == "待回填"
    # 汇总：已结算 2 方案，全中 1
    s = build_series(recs)
    summary = plan_summary(rows, s)
    assert summary["n_settled"] == 2
    assert summary["full_hits"] == 1
    assert summary["actual_rate"] == 0.5


def test_plan_old_dict_format_skipped():
    """旧格式（dict 含 picks/odds）跳过不统计。"""
    plans = {"2026-08-20:A": {"type": "胜平负3串1", "picks": ["#3主胜@1.86"]}}
    rows = build_plans(plans, [])
    assert rows == []


def test_assertions_trigger_and_skip():
    """A1校准/A2星级断言：样本足够触发、不足静默。"""
    from trend_report import build_assertions, build_calibration, build_buckets, build_series
    # 构造 20 条：70%+ 桶系统性高估（预测 0.85 实际全 miss）+ 四星命中率 40%
    recs = []
    for i in range(20):
        recs.append(_rec(code=f"S{i:02d}", stars=4, p_final=[0.85, 0.08, 0.07],
                         result="0-2", directionHit=False))
    s = build_series(recs)
    cal = build_calibration(s["filled"])
    buckets = build_buckets(s["filled"])
    asserts = build_assertions(s, cal, buckets)
    by_name = {a["name"]: a for a in asserts}
    a1 = by_name.get("A1校准·70%~101%")
    assert a1 and a1["triggered"], "校准断言应触发"
    assert "高估" in a1["conclusion"]
    a2 = by_name.get("A2星级·四星")
    assert a2 and a2["triggered"], "四星 40% vs 预期65% 偏离25pp 应触发"
    # 样本不足：3 条 → 静默
    recs_small = [_rec(code=f"T{i}", stars=4, p_final=[0.85, 0.08, 0.07], result="0-2", directionHit=False) for i in range(3)]
    s2 = build_series(recs_small)
    a2s = build_assertions(s2, build_calibration(s2["filled"]), build_buckets(s2["filled"]))
    assert all(not a["triggered"] for a in a2s), "n<15 应全部静默"
