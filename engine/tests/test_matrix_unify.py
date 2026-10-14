import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import numpy as np
import pytest

import dc_predict as dp
import goal_engine as ge


CASES = [(1.2, 1.0, -0.24), (1.8, 1.5, -0.07), (2.0, 1.6, 0.0),
         (4.0, 4.0, 0.15),      # 越 rho 有效域 → 两侧都应退化纯泊松
         (2.8, 4.4, 0.0841)]    # 实测曾负 tau 的组合（belgium rho）


@pytest.mark.parametrize("lh,la,rho", CASES)
def test_two_modules_same_distribution(lh, la, rho):
    """同参数下 dc_predict 与 goal_engine 须产出一致分布（10-14 T5 统一口径）。

    此前两模块各自 max(...,1e-12)/无守卫，同参数可产出不同矩阵；
    统一为「越 rho 有效域退化纯泊松」后须逐格相等。"""
    a = dp.score_matrix(lh, la, rho)
    b = ge.score_matrix(lh, la, rho)
    np.testing.assert_allclose(a, b, atol=1e-12)


def test_no_cell_floored_at_1e_minus_12():
    """旧掩盖式会把负 tau 格夹成 1e-12，与相邻格相差数量级断层。统一后
    退化矩阵 6:6 格须为纯泊松值 pm(6)²/截断和²。"""
    import math
    m = ge.score_matrix(4.0, 4.0, 0.15)
    trunc = sum(4.0 ** k / math.factorial(k) for k in range(7)) * math.exp(-4.0)
    pm6 = math.exp(-4.0) * 4.0 ** 6 / math.factorial(6)
    assert m[6, 6] == pytest.approx(pm6 * pm6 / (trunc * trunc), rel=1e-9)
