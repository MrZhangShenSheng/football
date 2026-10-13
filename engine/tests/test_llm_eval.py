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
