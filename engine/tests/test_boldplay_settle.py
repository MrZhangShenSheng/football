"""boldplay settle 逐 leg 判定测试（phase2-plan 任务 6 · TDD 先行）。开发者 sszhang"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from boldplay import settle  # noqa: E402

TICKET = {"totalCost": 18, "tiers": {
    "base": {"cost": 4, "legs": [[{"matchNumStr": "001", "play": "had", "pick": "主胜", "odds": 1.8},
                                  {"matchNumStr": "002", "play": "had", "pick": "平", "odds": 3.4}],
                                 [{"matchNumStr": "003", "play": "had", "pick": "客胜", "odds": 2.1},
                                  {"matchNumStr": "001", "play": "had", "pick": "主胜", "odds": 1.8}]]},
    "upset": {"cost": 8, "multiplier": 4,
              "legs": [{"matchNumStr": "001", "play": "crs", "pick": "2:0", "odds": 9.0},
                       {"matchNumStr": "002", "play": "crs", "pick": "1:1", "odds": 5.8},
                       {"matchNumStr": "003", "play": "crs", "pick": "2:1", "odds": 8.0},
                       {"matchNumStr": "004", "play": "crs", "pick": "3:0", "odds": 20.0}]}}}


def test_settle_partial_leg_hits():
    res = settle(TICKET, {"001": "2:0", "002": "0:0", "003": "2:1", "004": "1:0"})
    assert res["legHits"]["upset"] == [[True, False, True, False]]  # 3/4 部分命中=数据点（{tier:[[注×腿]]} 二维口径）
    assert res["upsetHit"] is False and res["payout"] == 0.0


def test_settle_full_hit_payout():
    res = settle(TICKET, {"001": "2:0", "002": "1:1", "003": "2:1", "004": "3:0"})
    assert res["upsetHit"] is True
    raw = 2 * 9.0 * 5.8 * 8.0 * 20.0 * 4
    assert abs(res["payout"] - raw) < 1e-6                          # 全中按合赔×2×倍数
    assert res["densityRecovered"] > 0


def test_settle_missing_result_marks_none():
    res = settle(TICKET, {"001": "2:0"})
    assert res["legHits"]["upset"] == [[True, None, None, None]]
    assert res["upsetHit"] is False


def test_settle_real_schema_score_key():
    """真实出票 JSON（2026-08-24-boldplay.json）：upset 腿 CRS 选项存 score 键（非 pick）。"""
    real = {"totalCost": 18, "tiers": {
        "upset": {"cost": 8, "multiplier": 4,
                  "legs": [{"matchNumStr": "周二005", "play": "crs", "score": "1:0", "odds": 11.0},
                           {"matchNumStr": "周二006", "play": "crs", "score": "1:0", "odds": 13.0}]}}}
    res = settle(real, {"周二005": "1:0", "周二006": "1:0"})
    assert res["legHits"]["upset"] == [[True, True]] and res["upsetHit"] is True
    assert abs(res["payout"] - 2 * 11.0 * 13.0 * 4) < 1e-6


def test_settle_had_direction_from_score():
    had = {"totalCost": 4, "tiers": {
        "base": {"cost": 4, "legs": [[{"matchNumStr": "001", "play": "had", "pick": "客胜", "odds": 2.1}]]}}}
    assert settle(had, {"001": "0:2"})["legHits"]["base"] == [[True]]
    assert settle(had, {"001": "1:1"})["legHits"]["base"] == [[False]]


def test_settle_ttg_leg():
    """A-MIX TTG 腿：总进球判定（含 7+ 档）。"""
    tk = {"totalCost": 8, "tiers": {"upset": {"cost": 8, "multiplier": 2, "legs": [
        {"matchNumStr": "001", "play": "ttg", "pick": "2球", "odds": 4.25},
        {"matchNumStr": "002", "play": "ttg", "pick": "7+球", "odds": 16.0}]}}}
    assert settle(tk, {"001": "2:0", "002": "4:3"})["legHits"]["upset"] == [[True, True]]
    assert settle(tk, {"001": "1:0", "002": "5:1"})["legHits"]["upset"] == [[False, False]]  # 1球≠2球;6球<7
    assert settle(tk, {"001": "2:0", "002": "2:1"})["legHits"]["upset"] == [[True, False]]


def test_settle_hafu_leg_needs_half():
    """A-MIX HAFU 腿：02-results 无半场比分 → None 待人工（诚实口径，不自动判）。"""
    tk = {"totalCost": 8, "tiers": {"upset": {"cost": 8, "multiplier": 2, "legs": [
        {"matchNumStr": "001", "play": "hafu", "pick": "dd", "odds": 4.75}]}}}
    res = settle(tk, {"001": "1:1"})
    assert res["legHits"]["upset"] == [[None]] and res["upsetHit"] is False and res["payout"] == 0.0
