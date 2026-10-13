# engine/tests/test_backtest_crs_batch.py
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from backtest_crs_batch import load_hist_window, matrix_key_to_pick

def test_matrix_key_to_pick():
    assert matrix_key_to_pick("s01s00") == "1:0"
    assert matrix_key_to_pick("s05s03") == "5:3"
    assert matrix_key_to_pick("other_h") == "other_h"
    # 盲区修复（Controller Ruling）：score_matrix 矩阵内部"其他"三键须映回体彩价池键
    assert matrix_key_to_pick("s1sh") == "other_h"
    assert matrix_key_to_pick("s1sd") == "other_d"
    assert matrix_key_to_pick("s1sa") == "other_a"

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

def test_settle_row_hit_and_unpriced():
    from backtest_crs_batch import settle_row
    row = {"score": "2:1", "crs": {"2:1": 9.5, "1:1": 7.0, "1:0": 8.0, "other_h": 40.0}}
    r = settle_row(["2:1", "1:1", "1:0"], row, 2)
    assert r["hit"] == "2:1" and r["odds"] == 9.5 and r["payout"] == 2 * 9.5
    r2 = settle_row(["9:9", "2:1"], row, 2)   # 9:9 无价 → dropped，次选命中
    assert r2["dropped"] == 1 and r2["hit"] == "2:1"

def test_settle_row_unlisted_score_falls_to_other():
    from backtest_crs_batch import settle_row
    row = {"score": "7:0", "crs": {"other_h": 45.0, "1:1": 7.0}}
    r = settle_row(["other_h"], row, 1)
    assert r["hit"] == "other_h" and r["odds"] == 45.0

def test_arm_a0_orders_by_devig_prob():
    from backtest_crs_batch import arm_a0
    # crs_power_devig 有 MIN_CRS_CELLS=20 门槛（3键迷你池实测返 {}→IndexError），
    # 补满 25 键、其余赔率全 >9.0，保持 brief 语义：1:1 赔最低 → 去水概率最高
    pool = {"1:1": 7.0, "1:0": 8.0, "0:0": 9.0}
    pool.update({f"{i}:{j}": 12.0 + i + j for i in range(5) for j in range(5)
                 if f"{i}:{j}" not in pool})
    row = {"crs": pool, "crsOpen": {}, "had": {}, "ttg": {}}
    ranked = arm_a0(row)                     # 1:1 赔最低 → 去水概率最高
    assert ranked[0] == "1:1"

def test_arm_a2_lambda_reverse_orders():
    from backtest_crs_batch import arm_a2
    # ttg 池键=真实 hist_odds 口径裸数字 "0".."7"（brief 原 "s{i}" 前缀键实测被
    # lam_sum_from_ttg 的 TTG_KEYS 过滤→lam_sum=0.0 静默垃圾→top="0:0"）
    row = {"crs": {}, "crsOpen": {}, "ttg": {f"{i}": 3.0 for i in range(8)},
           "had": {"h": 1.4, "d": 4.5, "a": 6.5}}
    ranked = arm_a2(row)                     # 强主 λ→矩阵 top 应为主胜小比分
    assert ranked[0] in ("1:0", "2:0", "2:1", "3:0")

def test_arm_a3_unmapped_returns_empty():
    from backtest_crs_batch import arm_a3
    row = {"home": "不存在的队名XYZ", "away": "同样不存在ABC", "date": "2025-03-01"}
    assert arm_a3(row, {}, {}) == []

def test_arm_a4_prefers_market_when_open_full():
    from backtest_crs_batch import arm_a4
    # crs_power_devig 有 MIN_CRS_CELLS=20 门槛（T4 实测）：brief 原 4×4=16 键会被拦下
    # 走层2/层3——补满 25 键保持「开盘池齐→层1」语义；ttg 键=真实口径裸数字（T4 探针3）
    open_pool = {"1:1": 6.0, "1:0": 8.0}
    open_pool.update({f"{i}:{j}": 12.0 + i + j for i in range(5) for j in range(5)
                      if f"{i}:{j}" not in open_pool})
    row = {"crs": {}, "crsOpen": open_pool,
           "ttg": {f"{i}": 3.0 for i in range(8)}, "had": {"h": 1.5, "d": 4.0, "a": 5.0},
           "home": "X", "away": "Y", "date": "2025-03-01"}
    ranked = arm_a4(row, {}, {})             # 开盘 crs 齐 → 层1，排序=开盘 devig 序
    assert ranked and isinstance(ranked, list)
    assert ranked[0] == "1:1"                # 1:1 开盘赔最低→去水概率最高；且层1 pick 须已映回体彩 pick 键
