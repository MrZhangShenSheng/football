import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import json
import glob

import pytest

import freq_band as fb
from dc_predict import score_matrix, tau_is_positive

CACHE = Path(__file__).resolve().parents[1] / "cache"


def test_rho_bounds_widened():
    import dc_fit
    import inspect
    src = inspect.getsource(dc_fit.fit)
    assert "(-0.3, 0.15)" in src, "rho 域未放宽至 (-0.3, 0.15)"


def test_fitted_rho_within_new_bounds():
    """所有已拟合 rho 须在新域内。"""
    for p in glob.glob(str(CACHE / "*_dc.json")):
        rho = json.load(open(p, encoding="utf-8")).get("rho")
        if rho is None:
            continue
        assert -0.3 - 1e-9 <= rho <= 0.15 + 1e-9, f"{Path(p).name} rho={rho} 越界"


def test_no_negative_tau_over_lambda_domain():
    """已拟合 rho 在 LAMBDA_CLAMP 全域内要么 tau 恒正、要么走 rho=0 退化——
    两者都由 score_matrix 内部保证，此测试锁定退化路径不产出负概率。"""
    lo, hi = fb.LAMBDA_CLAMP
    for p in glob.glob(str(CACHE / "*_dc.json")):
        rho = json.load(open(p, encoding="utf-8")).get("rho")
        if rho is None:
            continue
        for i in range(int(lo * 10), int(hi * 10) + 1, 2):
            for j in range(int(lo * 10), int(hi * 10) + 1, 2):
                m = score_matrix(i / 10, j / 10, rho)
                assert (m > 0).all() and abs(m.sum() - 1.0) < 1e-9, \
                    f"{Path(p).name} rho={rho} lh={i/10} la={j/10} 出负概率"


def test_score_matrix_degrades_to_poisson_not_negative():
    """越有效域（ρ=0.15, lh·la>6.67）须退化为纯泊松而非负概率/夹 1e-12。
    纯泊松 0:0 格 = e^{-(lh+la)} / 截断和——7×7 对 λ=4,4 只捕捉 79% 质量，归一
    会放大每格（此前断言漏了截断和，误判退化失败——测试 bug，实现一直正确）。"""
    import math
    trunc = sum(4.0 ** k / math.factorial(k) for k in range(7)) * math.exp(-4.0)
    expected = math.exp(-8.0) / (trunc * trunc)
    m = score_matrix(4.0, 4.0, 0.15)
    assert m[0, 0] == pytest.approx(expected, rel=1e-9)
    assert (m > 0).all()


def test_score_matrix_still_normalises():
    p = score_matrix(1.4, 1.1, -0.24)
    assert abs(p.sum() - 1.0) < 1e-9
    assert (p > 0).all()
