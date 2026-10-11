# engine/scripts/tests/test_strength_chain_prod.py
# -*- coding: utf-8 -*-
"""v35-P4 D2 三层降级选择测试。开发者 sszhang"""
import json

import strength_chain_prod as scp

CRS31 = {f"{h}:{a}": 20.0 for h in range(6) for a in range(6)}
CRS31.update({"other_h": 70.0, "other_d": 500.0, "other_a": 400.0})
CRS31["1:1"], CRS31["1:0"] = 6.2, 5.3
TTG8 = {str(i): 10.0 + i * 5 for i in range(8)}
HAD3 = {"h": 2.0, "d": 3.2, "a": 3.5}


def test_layer1_market_crs():
    r = scp.pick_source({"crs": CRS31, "ttg": TTG8, "had": HAD3})
    # 最低赔格 1:0@5.3 去水后仍居首（brief 原断言 s01s01 系笔误·与 test_market_lambda 同常量同结论）
    assert r["src"] == "market_crs" and r["top5"][0]["pick"] == "s01s00"
    # pick_source 内部键=boom（层1=ttg_boom·层2=矩阵other桶）；entry 对外 schema 键=ttgBoom
    assert r["lam"] is None and 0.0 <= r["boom"] < 1.0


def test_layer2_market_lambda_when_crs_missing():
    r = scp.pick_source({"crs": {}, "ttg": TTG8, "had": HAD3})
    assert r["src"] == "market_lambda" and r["lam"] and r["lam"][0] + r["lam"][1] > 1.0


def test_layer3_none_when_odds_empty():
    assert scp.pick_source({}) is None          # 返回 None 由上层走 V3W 兜底


def test_load_day_odds_normalizes_archive_keys(tmp_path):
    """存档→引擎口径归一：crs 中文 other 三键→other_*·ttg s 前缀剥除·businessDate 过滤·跨日文件合并。

    实证存档键形（2026-09~10 score_odds）：胜其他/平其他/负其他 + s0..s7（2026-10-11 Task4 勘察）。
    """
    day = "2026-10-07"
    blk_hit = {"businessDate": day, "matches": [
        {"matchNumStr": "周三001", "home": "甲队", "away": "乙队",
         "crs": {"1:1": 6.2, "1:0": 5.3, **{f"{h}:{a}": 20.0 for h in range(6) for a in range(6)
                                            if (h, a) != (1, 1) and (h, a) != (1, 0)},
                 "胜其他": 70.0, "平其他": 500.0, "负其他": 400.0},
         "ttg": {f"s{i}": 10.0 + i * 5 for i in range(8)},
         "had": HAD3}]}
    doc = {"matchDays": [
        {"businessDate": "2026-10-08", "matches": [{"matchNumStr": "周四001", "crs": {}, "ttg": {}, "had": {}}]},
        blk_hit]}
    (tmp_path / f"{day}.json").write_text(json.dumps(doc), encoding="utf-8")
    out = scp.load_day_odds(day, odds_dir=tmp_path)
    assert list(out) == ["周三001"]                    # 非当日块过滤·键=matchNumStr
    odds = out["周三001"]
    assert "other_h" in odds["crs"] and "胜其他" not in odds["crs"]
    assert "0" in odds["ttg"] and "s0" not in odds["ttg"]
    r = scp.pick_source(odds)                          # 端到端：归一后层1 可算
    assert r and r["src"] == "market_crs"
