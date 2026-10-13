# engine/tests/test_llm_eval.py
import sys, json, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from llm_eval import (market_devig_on_keys, actual_key, evaluate,
                      TAIL_ODDS_FLOOR, LOGLLOSS_CLIP)


def _full_pool():
    pool = {f"{i}:{j}": (8.0 if (i + j) <= 3 else 30.0) for i in range(6) for j in range(6)}
    pool.update({"other_h": 60.0, "other_d": 60.0, "other_a": 60.0})
    return pool


def test_actual_key_grid_and_other():
    assert actual_key("2:1") == "2:1"
    assert actual_key("0:3") == "0:3"
    assert actual_key("6:0") == "other_h"
    assert actual_key("2:6") == "other_a"


def test_market_devig_keys():
    keys = ["1:1", "1:0", "0:1"]
    p = market_devig_on_keys(_full_pool(), keys)
    assert set(p) == set(keys) and abs(sum(p.values()) - 1.0) < 1e-6


def test_evaluate_verdict_machinery(tmp_path, monkeypatch):
    # 构造 2 个销售日的 preds + 赛果 fixture，验证 PASS/INSUFFICIENT 逻辑跑通
    import llm_eval as le
    pool = _full_pool()
    day_rows = []
    for i in range(35):                       # n≥30 观察门槛
        code = f"二{i:03d}"
        p = {k: (0.05 if k != "1:1" else 0.2) for k in pool}
        day_rows.append({"code": code, "cardHash": "x" * 16,
                         "p_crs": p, "pRawSum": 1.0, "rawSumWarn": False,
                         "contextStatus": "ok", "keyJudgments": [], "confidence": "medium",
                         "_marketPool": pool, "_actual": "1:1", "_closeProxy": "lineupLast"})
    monkeypatch.setattr(le, "_load_day_rows", lambda days: day_rows)
    out = evaluate(["2026-10-14"])
    assert out["primary"]["n"] == 35
    assert out["primary"]["verdict"] in ("PASS", "FAIL", "UNDETECTED", "INSUFFICIENT")
    assert "tailChannel" in out and "descriptive" in out


def test_verdict_gates_locked():
    """review fix：n<formal 一律 INSUFFICIENT 不判定；tail PASS 锁 n≥150（边界四例）。"""
    import llm_eval as le
    orig = le._boot_ci_diff
    le._boot_ci_diff = lambda d, seed=0, n_boot=0: [1.0, 2.0]   # CI 下限恒>0（诱 PASS）
    try:
        rows99 = [0.01] * 99
        r99 = le.evaluate.__wrapped__(["99场"]) if hasattr(le.evaluate, "__wrapped__") else None
        # 直接构造 verdict 语义（经 evaluate 内嵌函数不可达，用等价逻辑验证闸门函数行为）
        # —— 通过 evaluate 的 monkeypatch 装载器复现四边界
        def rows_of(n):
            return [{"code": f"c{i}", "cardHash": "x" * 16,
                     "p_crs": {"1:1": 0.5}, "pRawSum": 1.0, "rawSumWarn": False,
                     "contextStatus": "ok", "keyJudgments": [], "confidence": "medium",
                     "_marketPool": {"1:1": 3.0, "1:0": 3.0, "0:1": 3.0, "0:0": 3.0,
                                     **{f"{i}:{j}": 30.0 for i in range(6) for j in range(6)
                                        if not (i <= 1 and j <= 1)},
                                     "胜其他": 60.0, "平其他": 60.0, "负其他": 60.0},
                     "_actual": "1:1", "_closeProxy": "lineupLast", "_score": "1:1"}
                    for i in range(n)]
        le._load_day_rows = lambda days: rows_of(99)
        out99 = le.evaluate(["x"])
        assert out99["primary"]["verdict"] == "INSUFFICIENT"      # n=99 <100 不判定
        assert "diffMean" in out99["primary"]                     # 观察数字照出
        le._load_day_rows = lambda days: rows_of(100)
        out100 = le.evaluate(["x"])
        assert out100["primary"]["verdict"] == "PASS"             # n=100 CI 下限>0 → PASS
        le._load_day_rows = lambda days: rows_of(149)
        out149 = le.evaluate(["x"])
        assert out149["tailChannel"]["verdict"] == "INSUFFICIENT"  # tail n=149 <150 锁
        le._load_day_rows = lambda days: rows_of(150)
        out150 = le.evaluate(["x"])
        assert out150["tailChannel"]["verdict"] == "PASS"          # n=150 开闸
    finally:
        le._boot_ci_diff = orig
