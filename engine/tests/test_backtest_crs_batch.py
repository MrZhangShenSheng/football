# engine/tests/test_backtest_crs_batch.py
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from backtest_crs_batch import load_hist_window, matrix_key_to_pick

def test_matrix_key_to_pick():
    assert matrix_key_to_pick("s01s00") == "1:0"
    assert matrix_key_to_pick("s05s03") == "5:3"
    assert matrix_key_to_pick("other_h") == "other_h"

def test_load_window_filters_and_keeps_pairs(tmp_path, monkeypatch):
    # 造 2 场迷你库：1 场两价齐、1 场缺 crsOpen → 只留前者
    fake = {"range": "x", "count": 2, "matches": [
        {"date": "2025-03-01", "code": "周六001", "league": "英超", "home": "A", "away": "B",
         "score": "2:1", "had": {"h": 1.5, "d": 4.0, "a": 5.0}, "ttg": {"s0": 3.0},
         "crs": {f"{i}:{j}": 8.0 for i in range(5) for j in range(5)},
         "crsOpen": {f"{i}:{j}": 8.5 for i in range(5) for j in range(5)}},
        {"date": "2025-03-02", "code": "周六002", "league": "英超", "home": "C", "away": "D",
         "score": "1:1", "had": {"h": 2.0, "d": 3.0, "a": 3.5}, "ttg": {},
         "crs": {f"{i}:{j}": 8.0 for i in range(5) for j in range(5)}, "crsOpen": {}},
    ]}
    p = tmp_path / "crs_hist_fake.json"; p.write_text(__import__("json").dumps(fake), encoding="utf-8")
    monkeypatch.setattr("backtest_crs_batch.HIST_DIR", tmp_path)
    rows = load_hist_window("2025-03-01", "2025-03-31")
    assert len(rows) == 1 and rows[0]["code"] == "周六001"
