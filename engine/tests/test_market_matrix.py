"""market_matrix 五池去水+DC参数拟合测试（P2·brief两测试 verbatim + 真实存档验收）。开发者 sszhang"""
import json
from pathlib import Path

import numpy as np
import pytest

from market_matrix import devig_pool, fit_lambdas, FIT_ERR_GATE, LAMBDA_BOUNDS

ARCHIVE = Path(__file__).parents[2] / "engine" / "cache" / "score_odds" / "2026-09-06.json"


def test_devig_pool_normalizes():
    odds = {'1:0': 4.0, '1:1': 4.0}
    p = devig_pool(odds, alpha=1.0)
    assert abs(sum(p.values()) - 1.0) < 1e-9 and abs(p['1:0'] - 0.5) < 1e-9


def test_fit_recovers_lambdas_roundtrip():
    # 用已知λ生成的一致赔率反推, 应近似还原
    from dc_predict import score_matrix
    lh, la, rho = 1.6, 1.1, -0.05
    mat = score_matrix(lh, la, rho)
    crs = {}
    for h in range(6):
        for a in range(6):
            if mat[h, a] > 1e-4:
                crs[f"{h}:{a}"] = float(1.0 / mat[h, a])
    had = {'h': 1.0/float(np.tril(mat, -1).sum()),
           'd': 1.0/float(np.trace(mat)),
           'a': 1.0/float(np.triu(mat, 1).sum())}
    ttg = {}
    for t in range(8):
        q = sum(mat[h, a] for h in range(7) for a in range(7) if h + a == t)
        if q > 1e-4: ttg[f"s{t}"] = 1.0 / q
    fit = fit_lambdas(crs, had, ttg, alpha=1.0)
    assert abs(fit['lh'] - lh) < 0.15 and abs(fit['la'] - la) < 0.15
    assert fit['fit_err'] < 0.01


def _archive_matches():
    """真实存档中有 crs/had/ttg 三池的比赛清单（缺档或缺池→测试 skip）。"""
    if not ARCHIVE.exists():
        pytest.skip("score_odds 存档不存在")
    blob = json.loads(ARCHIVE.read_text(encoding="utf-8"))
    rows = []
    for day in blob.get("matchDays", []):
        for m in day.get("matches", []):
            crs, had, ttg = m.get("crs"), m.get("had"), m.get("ttg")
            if all(isinstance(x, dict) and x for x in (crs, had, ttg)):
                rows.append((m["matchNumStr"], f"{m['home']} vs {m['away']}", crs, had, ttg))
    if len(rows) < 5:
        pytest.skip(f"存档三池齐全场次不足 5 场（实际 {len(rows)}）")
    return rows[:5]


def test_real_archive_fit_quality():
    """真实存档硬验收：抽 5 场 fit_lambdas 全过 fit_err<FIT_ERR_GATE 且 λ 在界内。"""
    for numStr, label, crs, had, ttg in _archive_matches():
        fit = fit_lambdas(crs, had, ttg, alpha=1.0)
        assert LAMBDA_BOUNDS[0] < fit['lh'] < LAMBDA_BOUNDS[1], f"{numStr} {label} lh={fit['lh']}"
        assert LAMBDA_BOUNDS[0] < fit['la'] < LAMBDA_BOUNDS[1], f"{numStr} {label} la={fit['la']}"
        assert fit['fit_err'] < FIT_ERR_GATE, f"{numStr} {label} fit_err={fit['fit_err']}"
