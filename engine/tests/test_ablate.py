# -*- coding: utf-8 -*-
"""ablate 指标升级测试：RPS/logloss 概率质量 + bootstrap CI（P0 2026-08-29）。"""
import math

import pytest

from ablate import group_metrics, boot_ci_diff


class TestGroupMetrics:
    """组级指标：n/命中率/RPS/logloss（有 p_final+result 的子集算概率指标）。"""

    def test_full(self):
        recs = [
            {"directionHit": True, "p_final": [0.7, 0.2, 0.1], "result": "2-1"},   # 主胜对
            {"directionHit": False, "p_final": [0.6, 0.25, 0.15], "result": "0-1"},  # 错但自信
        ]
        m = group_metrics(recs)
        assert m["n"] == 2
        assert m["hit"] == 0.5
        assert m["n_prob"] == 2
        # 第一场 RPS 小（对且自信），第二场 logloss 大（自信但错）
        assert 0 < m["rps"] < 0.3
        assert m["logloss"] > 1.0

    def test_overconfident_wrong_worse(self):
        # 同样错一场：自信错（0.9）比犹豫错（0.4）logloss 差很多
        m1 = group_metrics([{"directionHit": False, "p_final": [0.9, 0.05, 0.05], "result": "0-1"}])
        m2 = group_metrics([{"directionHit": False, "p_final": [0.4, 0.3, 0.3], "result": "0-1"}])
        assert m1["logloss"] > m2["logloss"]
        assert m1["rps"] > m2["rps"]

    def test_missing_prob_skipped(self):
        m = group_metrics([{"directionHit": True, "result": "2-1"}])   # 无 p_final
        assert m["n"] == 1
        assert m["n_prob"] == 0
        assert m["rps"] is None and m["logloss"] is None

    def test_bad_result_skipped(self):
        m = group_metrics([{"directionHit": None, "p_final": [0.5, 0.3, 0.2], "result": "弃赛"}])
        assert m["n_prob"] == 0

    def test_unnormalized_prob_skipped(self):
        # p_final 是标量（老 schema 个别场）而非三向数组 → 跳过概率指标
        m = group_metrics([{"directionHit": True, "p_final": 0.65, "result": "2-1"}])
        assert m["n_prob"] == 0

    def test_empty(self):
        m = group_metrics([])
        assert m["n"] == 0 and m["hit"] is None


class TestBootCi:
    """均值差 bootstrap 95% CI：固定种子可复现、方向正确。"""

    def test_deterministic(self):
        a = [0.1, 0.2, 0.3, 0.15, 0.25, 0.12, 0.18, 0.22]
        b = [0.3, 0.4, 0.35, 0.45, 0.32, 0.38, 0.42, 0.36]
        assert boot_ci_diff(a, b) == boot_ci_diff(a, b)   # 同种子同结果

    def test_significant_gap_excludes_zero(self):
        # a 组明显小于 b 组 → 差 (a-b) CI 上界 < 0
        a = [0.10] * 12
        b = [0.40] * 12
        lo, hi = boot_ci_diff(a, b)
        assert hi < 0

    def test_identical_groups_span_zero(self):
        v = [0.1, 0.2, 0.3, 0.4, 0.25, 0.35, 0.15, 0.45, 0.2, 0.3]
        lo, hi = boot_ci_diff(v, v)
        assert lo <= 0 <= hi

    def test_empty_returns_none(self):
        assert boot_ci_diff([], [0.1, 0.2]) is None
        assert boot_ci_diff([0.1], []) is None
