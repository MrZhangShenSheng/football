# -*- coding: utf-8 -*-
"""集成测试：合成数据验证 Dixon-Coles 拟合能恢复已知的实力结构。

构造 4 队联赛：Strong > MidA ≈ MidB > Weak，主场优势固定。
泊松采样固定 seed 生成两个赛季轮次，fit 后断言：
- 强队 attack 最高、弱队最低
- home_adv 为正且接近真值
- 拟合后预测强队主场对弱队的 λh 明显大于 λa
"""
import math
from datetime import date, timedelta

import numpy as np
import pytest

from dc_fit import fit
from dc_predict import score_matrix

RNG = np.random.default_rng(42)

# 真值（attack 加法刻度，defense 同刻度负向=防守好）
TRUE = {
    "Strong": {"attack": 0.55, "defense": -0.35},
    "MidA": {"attack": 0.10, "defense": -0.05},
    "MidB": {"attack": 0.05, "defense": 0.00},
    "Weak": {"attack": -0.50, "defense": 0.40},
}
TRUE_HOME = 0.30
TEAMS = sorted(TRUE)


def synth_matches(rounds: int = 20) -> list[dict]:
    """双循环赛程：每轮 4 队两两配对（主客轮换），泊松采样比分。"""
    matches = []
    start = date(2025, 8, 1)
    for r in range(rounds):
        d = start + timedelta(days=7 * r)
        order = TEAMS if r % 2 == 0 else TEAMS[::-1]
        for i in range(0, len(order), 2):
            h, a = order[i], order[i + 1]
            lh = math.exp(TRUE[h]["attack"] + TRUE[a]["defense"] + TRUE_HOME)
            la = math.exp(TRUE[a]["attack"] + TRUE[h]["defense"])
            matches.append({
                "date": d, "home": h, "away": a,
                "hg": int(RNG.poisson(lh)), "ag": int(RNG.poisson(la)),
            })
    return matches


@pytest.fixture(scope="module")
def fitted():
    matches = synth_matches()
    teams, attack, defense, home_adv, rho, nll = fit(matches, xi=0.005)
    return {t: (attack[i], defense[i]) for t, i in zip(teams, range(len(teams)))}, home_adv, rho, matches


class TestDcFitSynthetic:

    def test_strength_ordering_recovered(self, fitted):
        """强队 attack 最高、弱队最低。"""
        params, _, _, _ = fitted
        assert params["Strong"][0] == max(v[0] for v in params.values())
        assert params["Weak"][0] == min(v[0] for v in params.values())

    def test_strong_beats_weak_gap(self, fitted):
        """强弱攻击力差距显著为正。"""
        params, _, _, _ = fitted
        assert params["Strong"][0] - params["Weak"][0] > 0.5

    def test_home_advantage_positive(self, fitted):
        _, home_adv, _, _ = fitted
        assert 0.1 < home_adv < 0.6  # 真值 0.30，容差放宽容纳采样噪声

    def test_lambda_direction_strong_home_vs_weak(self, fitted):
        """强队主场对弱队：λh 应显著大于 λa。"""
        params, home_adv, rho, _ = fitted
        ah, dh = params["Strong"]
        aw, dw = params["Weak"]
        lh = math.exp(ah + dw + home_adv)
        la = math.exp(aw + dh)
        assert lh / la > 2.0  # 真值比 exp(0.55+0.40+0.30 + 0.50+0.35) 极大，2.0 是保守下界

    def test_predicted_home_prob_above_half(self, fitted):
        params, home_adv, rho, _ = fitted
        ah, dh = params["Strong"]
        aw, dw = params["Weak"]
        m = score_matrix(math.exp(ah + dw + home_adv), math.exp(aw + dh), rho)
        p_home = float(sum(m[i, j] for i in range(7) for j in range(7) if i > j))
        assert p_home > 0.5

    def test_fit_stable_deterministic(self, fitted):
        """同数据两次拟合结果一致（无随机初始化）。"""
        params1, ha1, rho1, matches = fitted
        teams, attack, defense, home_adv, rho, _ = fit(matches, xi=0.005)
        params2 = {t: (attack[i], defense[i]) for t, i in zip(teams, range(len(teams)))}
        for t in teams:
            assert params1[t][0] == pytest.approx(params2[t][0])
        assert ha1 == pytest.approx(home_adv)
