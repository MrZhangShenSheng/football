# engine/tests/test_backtest_crs_batch.py
import sys, pathlib
import pytest
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


# ---- Task 6：窗口编排 + 配对 bootstrap CI + prereg 判据联动 ----
# 合成窗工具：假 prereg（WX 窗）+ 假 hist（3 场·crs/crsOpen 各 26 键过 MIN_POOL_ITEMS=20 门槛；
# brief 原示例 17 键会被装载器丢弃——T4/T5 同款偏离）。ttg 键=真实口径裸数字。
import json as _json

def _fake_prereg(tmp_path, *, with_criterion=False):
    doc = {"windows": {"WX": {"from": "2025-03-01", "to": "2025-03-31", "role": "synthetic"}}}
    if with_criterion:
        doc["primaryCriterion"] = {"window": "WX", "arm": "A3_v3w_model", "baseline": "A1_open_devig",
                                   "metric": "top1 回收率逐场配对差", "ci": "bootstrap95 B=2000",
                                   "pass": "CI 下限 > 0"}
    p = tmp_path / "batch-backtest-prereg.json"
    p.write_text(_json.dumps(doc), encoding="utf-8")
    return p

def _fake_hist(tmp_path, top_odds):
    """3 场合成窗，每场 score=2:1、crs['2:1']=top_odds[i]（最低赔→A0/devig top1 必中）。"""
    matches = []
    for i, top in enumerate(top_odds):
        crs = {"2:1": float(top), "1:1": 9.0}
        crs.update({f"{a}:{b}": 10.0 + a + b for a in range(5) for b in range(5)
                    if f"{a}:{b}" not in crs})
        crs["other_h"] = 40.0
        matches.append({"date": f"2025-03-0{i + 1}", "code": f"六00{i + 1}", "league": "英超",
                        "home": "阿森纳", "away": "切尔西", "score": "2:1",
                        "had": {"h": 1.5, "d": 4.0, "a": 5.0}, "ttg": {str(n): 3.0 for n in range(8)},
                        "crs": crs, "crsOpen": {**crs, "2:1": float(top) + 0.5}})
    p = tmp_path / "crs_hist_fake.json"
    p.write_text(_json.dumps({"matches": matches}), encoding="utf-8")
    return p

def test_run_window_end_to_end(tmp_path, monkeypatch):
    from backtest_crs_batch import run_window, arm_a0
    _fake_hist(tmp_path, [7.0, 7.0, 7.0])
    monkeypatch.setattr("backtest_crs_batch.HIST_DIR", tmp_path)
    monkeypatch.setattr("backtest_crs_batch.PREREG_PATH", _fake_prereg(tmp_path))
    out = run_window("WX", {"A0": lambda r, c, m: arm_a0(r)}, k_max=1)
    a0 = out["arms"]["A0"]["k1"]
    assert a0["n"] == 3 and a0["stake"] == 6.0          # 3 场 × 2 元 × k=1
    assert a0["recovery"] == 7.0                        # 每场 top1=2:1 命中：2×7 / 2
    assert 0.0 <= a0["recovery"] <= 10.0                # brief Step1 数值域
    assert out["unmappedRate"]["A0"] == 0.0
    assert "criteria" not in out                        # WX 无 prereg 对 → 不产判据（W1/W2 exploratory 同理）

def test_load_window_range_reads_frozen_prereg():
    from backtest_crs_batch import load_window_range
    assert load_window_range("W3") == {"from": "2024-10-01", "to": "2025-09-30", "role": "adjudication"}
    with pytest.raises(ValueError):
        load_window_range("NOPE")

def test_prereg_pairs_maps_to_arm_fn_keys():
    from backtest_crs_batch import prereg_pairs, ARM_KEY
    assert [(p["arm"], p["baseline"], p["role"]) for p in prereg_pairs("W3")] == \
        [("A3", "A1", "primary"), ("A2", "A1", "secondary"), ("A4", "A1", "secondary")]
    assert prereg_pairs("W1") == [] and prereg_pairs("W2") == []   # exploratory 窗无判据对
    assert set(ARM_KEY.values()) == {"A0", "A1", "A2", "A3", "A4"}

def test_run_window_paired_ci_only_pairs_rows_where_both_arms_ran(tmp_path, monkeypatch):
    """A3 覆盖率≈50%：中段缺场时按较短截断的 zip 会错配（diffMean 算成 −1），
    行号配对必须只取两臂都非空的场（diffMean=−14）。开发者 sszhang"""
    from backtest_crs_batch import run_window
    _fake_hist(tmp_path, [8.0, 7.0, 6.0])               # A1 恒押 2:1 → 逐场派彩 16/14/12
    monkeypatch.setattr("backtest_crs_batch.HIST_DIR", tmp_path)
    monkeypatch.setattr("backtest_crs_batch.PREREG_PATH", _fake_prereg(tmp_path, with_criterion=True))
    arms = {"A1": lambda r, c, m: ["2:1"],
            "A3": lambda r, c, m: [] if r["date"] == "2025-03-02" else ["1:1"]}
    out = run_window("WX", arms, k_max=1)
    assert out["unmappedRate"]["A3"] == round(1 / 3, 4)
    cri = out["criteria"][0]
    assert (cri["arm"], cri["baseline"], cri["k"]) == ("A3", "A1", 1)
    assert (cri["nArm"], cri["nBaseline"], cri["pairedN"]) == (2, 3, 2)  # 报告层区分 n_arm 与 pairedN
    assert cri["diffMean"] == -14.0                     # row0: 0−16、row2: 0−12（截断错配=−1）
    assert cri["ci95"] == [-16.0, -12.0]                # 定种 bootstrap 两点分布分位（25%−16/50%−14/25%−12）
    assert cri["verdict"] == "FAIL" and cri["exploratory"] is True    # WX 非 W3 → exploratory

def test_run_leak_audit_ab_and_random_baseline(tmp_path, monkeypatch):
    from backtest_crs_batch import run_leak_audit
    _fake_hist(tmp_path, [8.0, 7.0, 6.0])
    monkeypatch.setattr("backtest_crs_batch.HIST_DIR", tmp_path)
    monkeypatch.setattr("backtest_crs_batch.PREREG_PATH", _fake_prereg(tmp_path))
    out = run_leak_audit("WX", hist_dir=tmp_path,
                         arm_b=lambda r, c, m: ["2:1"],
                         arm_a=lambda r, c, m: [] if r["date"] == "2025-03-02" else ["1:1"])
    assert out["armB"] == {"n": 3, "recovery": 7.0}     # (16+14+12)/(2×3)
    assert out["armA"] == {"n": 2, "recovery": 0.0}
    assert out["pairedN"] == 2 and out["diffMean"] == 14.0
    assert out["observedHit"] == 1.0                    # B 臂 top1 恒中
    assert out["randomShuffle"]["hit"] == 1.0 and out["randomShuffle"]["n_perm"] == 200

def test_arm_a3_offset_variant_unmapped_empty():
    from backtest_crs_batch import _arm_a3_offset, DECISION_DAY_OFFSET
    row = {"home": "不存在的队名XYZ", "away": "同样不存在ABC", "date": "2025-03-01"}
    assert _arm_a3_offset(DECISION_DAY_OFFSET - 2)(row, {}, {}) == []

def test_arm_fns_keys_match_arm_key_values():
    from backtest_crs_batch import ARM_FNS, ARM_KEY
    assert set(ARM_FNS) == set(ARM_KEY.values())        # T7 消费同名键防错位
    assert ARM_FNS["A3"].__name__ == "arm_a3" and ARM_FNS["A4"].__name__ == "arm_a4"
