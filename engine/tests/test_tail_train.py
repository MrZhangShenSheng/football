# engine/tests/test_tail_train.py
import sys, pathlib, json
import numpy as np
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from tail_train import (time_folds, fit_scaler, apply_scaler, event_outcomes,
                        market_event_probs, train_heads, predict_event_probs,
                        E2_CELLS, E3_CELLS, CS_NO_INFO_TG)


def test_time_folds_eleven_expanding():
    folds = time_folds()
    assert len(folds) == 11
    assert folds[0] == {"testFrom": "2025-01-01", "testTo": "2025-02-28"}
    assert folds[-1]["testFrom"] == "2026-09-01" and folds[-1]["testTo"] == "2026-09-28"


def test_scaler_nan_safe():
    X = np.array([[1.0, np.nan], [3.0, 5.0], [np.nan, 7.0]])
    mu, sd = fit_scaler(X)
    assert np.isclose(mu[0], 2.0) and np.isclose(mu[1], 6.0)
    Z = apply_scaler(X, mu, sd)
    assert not np.isnan(Z).any()
    assert np.isclose(Z[0, 1], 0.0)                      # nan→均值→scale 后 0


def test_event_outcomes_grid():
    assert event_outcomes("2:2") == (0, 0, 0)   # tg=4 非肥尾（2:3 tg=5 属 E1·计划笔误修正）
    assert event_outcomes("0:5") == (1, 1, 1)            # tg≥5 且 ∈E2 且 ∈E3
    assert event_outcomes("4:1") == (1, 1, 0)
    assert event_outcomes("6:0") == (1, 0, 0)            # 6:0 不在 0..5 网格→E2=0（口径冻结）


def test_market_event_probs_ttg_and_crs():
    row = {"ttg": {"5": 6.0, "6": 12.0, "7": 30.0, **{str(i): 4.0 for i in range(5)}},
           "crs": {f"{i}:{j}": 12.0 for i in range(6) for j in range(6)},
           "crsOpen": {f"{i}:{j}": 12.0 for i in range(6) for j in range(6)}}
    e1, e2, e3 = market_event_probs(row)
    assert 0.0 < e1 < 0.5
    assert 0.0 < e2 < 0.4 and 0.0 < e3 < 0.2
    assert e2 > e3                                       # E2 格集是 E3 超集
    assert market_event_probs({"ttg": {}, "crs": {}, "crsOpen": {}}) is None


def test_train_predict_heads_synthetic():
    rng = np.random.default_rng(7)
    N = 400
    z = rng.normal(size=N)                                # 潜变量：大球倾向
    y_tg = np.where(z + rng.normal(scale=0.7, size=N) > 0.9, 5, 2)
    X = np.column_stack([z + rng.normal(scale=0.3, size=N), rng.normal(size=N)])
    # 方向/零封标签由 tg 推（结构自洽）
    y_dir = np.array([{5: 2, 2: 0}[t] for t in y_tg])
    y_cs = np.array([0 if (t == 5 and d == 2) else 2 for t, d in zip(y_tg, y_dir)])
    tg1h = np.eye(8)[y_tg]
    heads = train_heads(X, y_tg, np.hstack([X, tg1h]), y_dir,
                        np.hstack([X, tg1h, np.eye(3)[y_dir]]), y_cs)
    P = predict_event_probs(heads, X[:20])
    assert P.shape == (20, 3)
    assert ((P > 0) & (P < 1)).all()
    # z 大 → E1 概率高（模型学到了信号）
    Xhi = np.column_stack([np.full(10, 2.0), np.zeros(10)])
    Xlo = np.column_stack([np.full(10, -2.0), np.zeros(10)])
    assert predict_event_probs(heads, Xhi)[:, 0].mean() > predict_event_probs(heads, Xlo)[:, 0].mean()
