# -*- coding: utf-8 -*-
"""单元测试：核心数学函数的已知性质（纯计算，零网络依赖）。"""
import math

import numpy as np
import pytest

from dc_fit import dc_tau
from dc_predict import devig, fuse, logit, sigmoid, score_matrix
from backtest import rps


class TestDcTau:
    """Dixon-Coles 低比分修正因子。"""

    def test_rho_zero_degrades_to_independent(self):
        """rho=0 时退化为独立泊松：所有 τ=1。"""
        for x, y in [(0, 0), (0, 1), (1, 0), (1, 1), (2, 0), (0, 2), (3, 3)]:
            assert dc_tau(x, y, lh=1.5, la=1.2, rho=0.0) == 1.0

    def test_negative_rho_ups_low_draws(self):
        """rho<0（文献常态）：0-0 与 1-1 概率被上调（τ>1）。"""
        lh, la, rho = 1.5, 1.2, -0.1
        assert dc_tau(0, 0, lh, la, rho) == pytest.approx(1 - lh * la * rho)
        assert dc_tau(0, 0, lh, la, rho) > 1
        assert dc_tau(1, 1, lh, la, rho) == pytest.approx(1 - rho)
        assert dc_tau(1, 1, lh, la, rho) > 1

    def test_negative_rho_downs_1_0_and_0_1(self):
        """rho<0：1-0 与 0-1 被下调（τ<1），与平局上调守恒（DC 1997 原式）。"""
        lh, la, rho = 1.5, 1.2, -0.1
        assert dc_tau(1, 0, lh, la, rho) == pytest.approx(1 + la * rho)
        assert dc_tau(1, 0, lh, la, rho) < 1
        assert dc_tau(0, 1, lh, la, rho) == pytest.approx(1 + lh * rho)
        assert dc_tau(0, 1, lh, la, rho) < 1

    def test_high_scores_untouched(self):
        """x,y ≥2 的格子不做修正。"""
        for x, y in [(2, 0), (0, 2), (2, 1), (1, 2), (2, 2)]:
            assert dc_tau(x, y, lh=2.0, la=2.0, rho=-0.13) == 1.0


class TestDevig:
    """赔率去水归一化。"""

    def test_equal_odds_equal_probs(self):
        """等价赔率 [2,4,4] → [0.5,0.25,0.25]。"""
        p = devig([2.0, 4.0, 4.0])
        assert p == pytest.approx([0.5, 0.25, 0.25])

    def test_normalized(self):
        p = devig([2.05, 3.4, 3.9])
        assert sum(p) == pytest.approx(1.0)

    def test_favorite_gets_higher_prob(self):
        p = devig([1.5, 4.0, 6.0])
        assert p[0] > p[1] > p[2]


class TestLogitSigmoid:
    def test_inverse_pair(self):
        for p in (0.1, 0.3, 0.5, 0.7, 0.9):
            assert sigmoid(logit(p)) == pytest.approx(p)

    def test_extreme_safe(self):
        """越界概率不崩溃（被夹到安全区间）。"""
        assert math.isfinite(logit(0.0)) and math.isfinite(logit(1.0))


class TestFuse:
    """log-odds 融合。"""

    def test_zero_a_equals_market(self):
        """a=0：模型无权重，融合=市场。"""
        p_dc, p_mkt = [0.5, 0.3, 0.2], [0.4, 0.35, 0.25]
        assert fuse(p_dc, p_mkt, a=0.0, b=1.0) == pytest.approx(p_mkt, abs=1e-9)

    def test_zero_b_equals_dc(self):
        p_dc, p_mkt = [0.5, 0.3, 0.2], [0.4, 0.35, 0.25]
        assert fuse(p_dc, p_mkt, a=1.0, b=0.0) == pytest.approx(p_dc, abs=1e-9)

    def test_normalized(self):
        p = fuse([0.5, 0.3, 0.2], [0.4, 0.35, 0.25], a=0.4, b=1.0)
        assert sum(p) == pytest.approx(1.0)
        assert all(v > 0 for v in p)

    def test_agreement_preserved(self):
        """模型与市场一致时融合不变。"""
        p = [0.45, 0.30, 0.25]
        assert fuse(p, p, a=0.4, b=1.0) == pytest.approx(p, abs=1e-9)


class TestScoreMatrix:
    def test_sums_to_one(self):
        m = score_matrix(1.4, 1.1, -0.1)
        assert m.sum() == pytest.approx(1.0)

    def test_independent_poisson_when_rho_zero(self):
        """rho=0 时 p[1,1] 接近独立泊松乘积（手算对照，容差容纳 7x7 截断归一化）。"""
        lh, la = 1.3, 0.9
        m = score_matrix(lh, la, 0.0)
        p11 = math.exp(-lh) * lh / 1 * math.exp(-la) * la / 1
        assert m[1, 1] == pytest.approx(p11, rel=1e-3)

    def test_draw_heavier_with_negative_rho(self):
        """rho<0 抬高 0-0/1-1（相对 rho=0）。"""
        m0 = score_matrix(1.3, 0.9, 0.0)
        m1 = score_matrix(1.3, 0.9, -0.15)
        assert m1[0, 0] > m0[0, 0]
        assert m1[1, 1] > m0[1, 1]

    def test_equal_lambdas_zero_zero_most_likely(self):
        m = score_matrix(1.0, 1.0, -0.1)
        assert m[0, 0] == m.max()


class TestRps:
    def test_certain_prediction_zero(self):
        """确定性预测（one-hot 对结果）RPS=0。"""
        assert rps([1.0, 0.0, 0.0], 0) == pytest.approx(0.0, abs=1e-9)

    def test_confident_small_penalty(self):
        """高置信但非确定：RPS 小正数。"""
        assert 0 < rps([0.9, 0.05, 0.05], 0) < 0.01

    def test_uniform_known_value(self):
        """均匀分布对主胜：RPS=5/18（手算对照）。"""
        assert rps([1 / 3, 1 / 3, 1 / 3], 0) == pytest.approx(5 / 18)

    def test_ordered_penalty(self):
        """三向有序：错到相邻类比错到对角扣分少。"""
        p = [0.5, 0.3, 0.2]
        assert rps(p, 1) < rps(p, 2)  # 平局(相邻) vs 客胜(远端)
