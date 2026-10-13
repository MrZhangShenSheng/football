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
    # frozenParams+W1/W2/W3 三窗=check_prereg 指纹闸过闸要件（I-2·main 三命令启动校验）
    doc = {"windows": {"WX": {"from": "2025-03-01", "to": "2025-03-31", "role": "synthetic"},
                       **{w: {"from": "2025-01-01", "to": "2025-12-31", "role": "synthetic"}
                          for w in ("W1", "W2", "W3")}},
           "frozenParams": {"beta": 0.05, "lag_days": 2, "decision_day_offset": -1,
                            "min_pool_items": 20, "k_max": 4, "boot_B": 2000, "seed": 20261013}}
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


# ---- Task 7：CLI run/run-all/leak-audit + 归档 + HTML 报告 ----
# 合成窗口径：A0 top1 recovery 可控（5 场 3 中 @1.4 → 0.84 落红线带内）；WX 非真窗不受红线约束。

def _fake_hist_scores(tmp_path, scores, top_odds=1.4):
    """n 场合成窗：crs['2:1'] 全场最低赔（top1 恒押 2:1），scores 控制逐场真实比分。"""
    matches = []
    for i, score in enumerate(scores):
        crs = {"2:1": float(top_odds), "1:1": 9.0}
        crs.update({f"{a}:{b}": 10.0 + a + b for a in range(5) for b in range(5)
                    if f"{a}:{b}" not in crs})
        crs["other_h"] = 40.0
        matches.append({"date": f"2025-03-{i + 1:02d}", "code": f"六{i + 1:02d}", "league": "英超",
                        "home": "阿森纳", "away": "切尔西", "score": score,
                        "had": {"h": 1.5, "d": 4.0, "a": 5.0}, "ttg": {str(n): 3.0 for n in range(8)},
                        "crs": crs, "crsOpen": {**crs, "2:1": float(top_odds) + 0.5}})
    p = tmp_path / "crs_hist_fake.json"
    p.write_text(_json.dumps({"matches": matches}), encoding="utf-8")
    return p

def test_a0_redline_check_band():
    from backtest_crs_batch import a0_redline_check
    mk = lambda rec, w="W1": {"window": w, "rows": 3,
                              "arms": {"A0": {"k1": {"recovery": rec}}}}
    assert a0_redline_check(mk(0.84)) is None          # 带内
    assert a0_redline_check(mk(0.80)) is None and a0_redline_check(mk(0.88)) is None  # 闭区间
    assert "0.79" in a0_redline_check(mk(0.79))        # 下界外 → 管道 bug 中断
    assert "0.89" in a0_redline_check(mk(0.89))        # 上界外
    assert a0_redline_check(mk(0.84, w="WX")) is None  # 非冻结真窗不校（区间=真窗全库口径校准）

def test_cmd_run_archives_window_json(tmp_path, monkeypatch, capsys):
    from backtest_crs_batch import main
    _fake_hist_scores(tmp_path, ["2:1", "2:1", "2:1", "0:0", "0:0"])   # 3/5 中 @1.4 → 0.84
    out_dir = tmp_path / "summaries"
    monkeypatch.setattr("backtest_crs_batch.HIST_DIR", tmp_path)
    monkeypatch.setattr("backtest_crs_batch.PREREG_PATH", _fake_prereg(tmp_path))
    monkeypatch.setattr("backtest_crs_batch.OUT_DIR", out_dir)
    assert main(["run", "WX"]) == 0
    saved = _json.loads((out_dir / "WX.json").read_text(encoding="utf-8"))
    assert saved["rows"] == 5
    assert saved["arms"]["A0"]["k1"]["recovery"] == 0.84
    assert saved["elapsedSec"] >= 0 and saved["generatedAt"]
    out = capsys.readouterr().out
    assert "rows=5" in out and "unmappedRate" in out     # dropped/unmapped 率打印（brief Step2 ③）

def test_cmd_run_redline_aborts_on_adjudication_window(tmp_path, monkeypatch, capsys):
    """I-1 分窗语义：裁决窗（role=adjudication）出带 rc=1 中断不续跑（prereg redlines[1]·不放宽）。"""
    from backtest_crs_batch import main
    bad = {"window": "W3", "role": "adjudication", "rows": 3, "unmapped": {}, "unmappedRate": {},
           "arms": {"A0": {"k1": {"n": 3, "stake": 6.0, "payout": 3.0, "recovery": 0.5, "dropped": 0}}}}
    monkeypatch.setattr("backtest_crs_batch.run_window", lambda w, arms, **kw: bad)
    monkeypatch.setattr("backtest_crs_batch.PREREG_PATH", _fake_prereg(tmp_path))
    out_dir = tmp_path / "summaries"
    monkeypatch.setattr("backtest_crs_batch.OUT_DIR", out_dir)
    assert main(["run", "W3"]) == 1                     # 裁决窗出界即中断
    err = capsys.readouterr().err
    assert "REDLINE" in err and "REDLINE-WARN" not in err
    assert (out_dir / "W3.json").exists()               # 中断前 JSON 已落盘（排查凭证）

def test_cmd_run_redline_warns_on_exploratory_window(tmp_path, monkeypatch, capsys):
    """I-1 分窗语义：exploratory 窗（W1/W2）出带降级 warn+JSON redline 留痕不中断
    ——HEAD 与 2026-10-13 归档事实对齐（W2 出带 0.8913 时三窗+HTML 跑完归档）。"""
    from backtest_crs_batch import main
    bad = {"window": "W2", "role": "reference-param-lookback", "rows": 2056,
           "unmapped": {}, "unmappedRate": {},
           "arms": {"A0": {"k1": {"n": 3, "stake": 6.0, "payout": 5.35,
                                  "recovery": 0.8913, "dropped": 0}}}}   # 归档事实同值出带
    monkeypatch.setattr("backtest_crs_batch.run_window", lambda w, arms, **kw: bad)
    monkeypatch.setattr("backtest_crs_batch.PREREG_PATH", _fake_prereg(tmp_path))
    out_dir = tmp_path / "summaries"
    monkeypatch.setattr("backtest_crs_batch.OUT_DIR", out_dir)
    assert main(["run", "W2"]) == 0                     # exploratory 窗出带不中断
    err = capsys.readouterr().err
    assert "REDLINE-WARN" in err and "warn 不中断" in err
    saved = _json.loads((out_dir / "W2.json").read_text(encoding="utf-8"))
    assert saved["redline"]["status"] == "warn-exploratory"           # JSON 留痕
    assert saved["redline"]["value"] == 0.8913
    assert saved["redline"]["band"] == [0.80, 0.88]

def test_check_prereg_fingerprint_gates_main(tmp_path, monkeypatch, capsys):
    """I-2：frozenParams 与模块常量漂移/三窗缺失 → SystemExit(2) 拒绝出报告（跑前指纹闸）。"""
    from backtest_crs_batch import main
    monkeypatch.setattr("backtest_crs_batch.PREREG_PATH", _fake_prereg(tmp_path))
    monkeypatch.setattr("backtest_crs_batch.run_window", lambda w, arms, **kw: {"window": w})
    monkeypatch.setattr("backtest_crs_batch.BETA", 0.99)             # 指纹漂移：β 0.05→0.99
    with pytest.raises(SystemExit) as e:
        main(["run", "W1"])
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert "PREREG-FINGERPRINT" in err and "beta" in err
    monkeypatch.setattr("backtest_crs_batch.BETA", 0.05)             # 恢复 β，改打三窗缺失
    doc = _json.loads(_fake_prereg(tmp_path).read_text(encoding="utf-8"))
    del doc["windows"]["W3"]
    p2 = tmp_path / "prereg-no-w3.json"; p2.write_text(_json.dumps(doc), encoding="utf-8")
    monkeypatch.setattr("backtest_crs_batch.PREREG_PATH", p2)
    with pytest.raises(SystemExit) as e2:
        main(["leak-audit", "W1"])
    assert e2.value.code == 2 and "windows.W3 缺失" in capsys.readouterr().err

def _fake_audit(win, *, rec_b=0.84, rec_a=0.835):
    return {"window": win, "rows": 5, "k": 1, "decisionDayOffsetB": -1, "decisionDayOffsetA": -3,
            "armB": {"n": 5, "recovery": rec_b}, "armA": {"n": 5, "recovery": rec_a},
            "pairedN": 5, "diffMean": 0.0, "ci95": [0.0, 0.0], "observedHit": 0.4,
            "randomShuffle": {"hit": 0.4, "n_perm": 200}, "redline": "prereg redlines[0]"}

def test_cmd_leak_audit_writes_and_verdict(tmp_path, monkeypatch, capsys):
    from backtest_crs_batch import main
    out_dir = tmp_path / "summaries"
    monkeypatch.setattr("backtest_crs_batch.PREREG_PATH", _fake_prereg(tmp_path))
    monkeypatch.setattr("backtest_crs_batch.OUT_DIR", out_dir)
    monkeypatch.setattr("backtest_crs_batch.run_leak_audit",
                        lambda w, **kw: _fake_audit(w))                  # |Δ|=0.005 < 2pp → PASS
    assert main(["leak-audit", "WX"]) == 0
    saved = _json.loads((out_dir / "WX-leakaudit.json").read_text(encoding="utf-8"))
    assert saved["recoveryDelta"] == 0.005 and saved["verdict"] == "PASS"
    out = capsys.readouterr().out
    assert "recovery=0.84" in out and "recovery=0.835" in out           # A/B 回收率对照打印
    monkeypatch.setattr("backtest_crs_batch.run_leak_audit",
                        lambda w, **kw: _fake_audit(w, rec_a=0.78))     # |Δ|=6pp → FAIL
    assert main(["leak-audit", "WX"]) == 1
    assert "FAIL" in capsys.readouterr().out

def _fake_run_window_factory():
    def fake_run_window(win, arms, **kw):
        rep = {"window": win, "role": "synthetic", "rows": 3, "unmapped": {}, "unmappedRate": {},
               "arms": {a: {f"k{k}": {"n": 3, "stake": 6.0, "payout": 5.04,
                                      "recovery": 0.84 if k == 1 else 0.95, "dropped": 0}
                            for k in range(1, 5)} for a in ("A0", "A1", "A2", "A3", "A4")}}
        if win == "W3":
            rep["criteria"] = [
                {"arm": "A3", "baseline": "A1", "role": "primary", "metric": "top1 回收率逐场配对差",
                 "k": 1, "pairedN": 3, "nArm": 3, "nBaseline": 3, "diffMean": 0.5,
                 "ci95": [0.1, 0.9], "verdict": "PASS", "exploratory": False},
                {"arm": "A2", "baseline": "A1", "role": "secondary", "metric": "同上",
                 "k": 1, "pairedN": 3, "nArm": 3, "nBaseline": 3, "diffMean": -0.2,
                 "ci95": [-0.5, 0.1], "verdict": "FAIL", "exploratory": False},
                {"arm": "A4", "baseline": "A1", "role": "secondary", "metric": "同上·预期≈0",
                 "k": 1, "pairedN": 3, "nArm": 3, "nBaseline": 3, "diffMean": 0.0,
                 "ci95": [0.0, 0.0], "verdict": "FAIL", "exploratory": False}]   # 归档事实：恒等但机械 FAIL
        return rep
    return fake_run_window

def test_cmd_run_all_generates_html(tmp_path, monkeypatch, capsys):
    from backtest_crs_batch import main, OUT_DIR_NAME
    doc = {"windows": {w: {"from": "2025-03-01", "to": "2025-03-31", "role": "synthetic"}
                       for w in ("W1", "W2", "W3")},
           "frozenParams": {"beta": 0.05, "lag_days": 2, "decision_day_offset": -1,
                            "min_pool_items": 20, "k_max": 4, "boot_B": 2000, "seed": 20261013},
           "primaryCriterion": {"window": "W3", "arm": "A3_v3w_model", "baseline": "A1_open_devig",
                                "metric": "top1 回收率逐场配对差", "ci": "bootstrap95", "pass": "CI 下限 > 0"},
           "secondary": [{"window": "W3", "arm": "A2_lambda_reverse", "baseline": "A1_open_devig",
                          "metric": "同上"}]}
    p = tmp_path / "prereg.json"; p.write_text(_json.dumps(doc), encoding="utf-8")
    out_dir = tmp_path / "summaries"
    monkeypatch.setattr("backtest_crs_batch.PREREG_PATH", p)
    monkeypatch.setattr("backtest_crs_batch.OUT_DIR", out_dir)
    monkeypatch.setattr("backtest_crs_batch.run_window", _fake_run_window_factory())
    monkeypatch.setattr("backtest_crs_batch.run_leak_audit",
                        lambda w, **kw: _fake_audit(w))                  # W1 必跑（brief Step3）
    assert main(["run-all"]) == 0
    for w in ("W1", "W2", "W3"):
        assert (out_dir / f"{w}.json").exists()
    assert (out_dir / "W1-leakaudit.json").exists()      # W1 必跑
    assert not (out_dir / "W2-leakaudit.json").exists()  # k1=0.84 ≤1.0 不触发（redlines[0]）
    html = (out_dir / OUT_DIR_NAME).read_text(encoding="utf-8")
    assert html.startswith("<!DOCTYPE html>") and '<meta charset="utf-8">' in html
    assert 0 <= html.index("主判据") < html.index("窗级明细")     # 主判据置顶
    assert "封盘反解" in html and "决策时点" in html and "参数回望" in html   # 限制节三条
    assert "数据来源" in html
    out = capsys.readouterr().out
    assert "主判据" in out and "PASS" in out            # verdict 打印（brief Step4）

def test_needs_leak_audit_any_k_over_one():
    from backtest_crs_batch import _needs_leak_audit
    rep = {"arms": {"A3": {"k1": {"recovery": 0.9}, "k3": {"recovery": 1.2}}}}
    assert _needs_leak_audit(rep) is True               # 任何臂任何 k >1.0 → 触发（redlines[0]）
    assert _needs_leak_audit({"arms": {"A0": {"k1": {"recovery": 1.0}}}}) is False   # 恰=1.0 不触发
    assert _needs_leak_audit({}) is False

def test_build_html_report_contract():
    from backtest_crs_batch import build_html_report
    r3 = _fake_run_window_factory()("W3", None)
    r3["role"] = "adjudication"                      # 真窗 role（裁决窗 k2-4 exploratory 标记生效）
    w2 = _fake_run_window_factory()("W2", None)
    w2["role"] = "reference-param-lookback"
    w2["arms"]["A0"]["k1"]["recovery"] = 0.8913      # 归档事实复刻：W2 出带（I-1 呈现）
    reports = {"W3": r3, "W2": w2}
    audits = {"W1": _fake_audit("W1")}
    meta = {"startedAt": "2026-10-13 10:00:00", "finishedAt": "2026-10-13 10:30:00",
            "totalElapsedSec": 1800.0, "windows": {"W3": 900.0}, "leakAudits": {"W1": 300.0}}
    html = build_html_report(reports, audits, meta)
    assert html.startswith("<!DOCTYPE html><html lang=\"zh-CN\">")
    assert "<meta charset=\"utf-8\">" in html
    assert html.index("主判据") < html.index("窗级明细")           # 置顶
    assert "pairedN" in html and "0.1" in html                    # CI 数值入表
    assert "verdict-PASS" in html and "verdict-FAIL" in html      # 徽章分级
    assert "exploratory" in html.lower() or "探索" in html
    for kw in ("封盘反解", "决策时点", "参数回望", "数据来源", "1800"):  # 限制三条+来源+耗时
        assert kw in html
    # I-3：A4 metric 含「预期≈0」且 diffMean=0 → 呈现「一致（如预期）」非 FAIL/PASS 框架
    assert "verdict-IDENT" in html and "一致（如预期）" in html
    # M-1：判据表 diffMean 单位脚注
    assert "元/场" in html
    # I-3/M-3：限制第四条（A4 层2/3）+第五条（口径二 moot）
    assert "0 触发" in html and "moot" in html
    # M-2：裁决窗 k2-4 列头 exploratory 标记
    assert "badge-exp" in html and "top2-4" in html
    # I-1：exploratory 窗出带=处置说明文字（非裸「红线出界」中断语）
    assert "排查定性大赛期特征" in html and "红线出界·中断" not in html
