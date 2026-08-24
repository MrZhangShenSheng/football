"""Bold Play settle 逐 leg 判定测试。开发者 sszhang"""
from boldplay import settle, _direction

TICKET = {"totalCost": 18, "tiers": {
    "base": {"cost": 4, "legs": [[{"matchNumStr": "001", "play": "had", "pick": "主胜", "odds": 1.8},
                                   {"matchNumStr": "002", "play": "had", "pick": "平", "odds": 3.4}],
                                  [{"matchNumStr": "003", "play": "had", "pick": "客胜", "odds": 2.1},
                                   {"matchNumStr": "001", "play": "had", "pick": "主胜", "odds": 1.8}]]},
    "mid": {"cost": 6, "legs": [[{"matchNumStr": "004", "play": "had", "pick": "主胜", "odds": 1.7}]]},
    "upset": {"cost": 8, "multiplier": 4,
              "legs": [{"matchNumStr": "001", "play": "crs", "pick": "2:0", "odds": 9.0},
                       {"matchNumStr": "002", "play": "crs", "pick": "1:1", "odds": 5.8},
                       {"matchNumStr": "003", "play": "crs", "pick": "2:1", "odds": 8.0},
                       {"matchNumStr": "004", "play": "crs", "pick": "3:0", "odds": 20.0}]}}}

def test_direction():
    assert _direction("2:0") == "主胜" and _direction("1:1") == "平" and _direction("0:2") == "客胜"

def test_settle_partial_leg_hits():
    # 注：brief 原稿 "002": "0:0" 与断言 [True, False] 矛盾（0:0=平，pick 平应✓），改为 "1:0"
    res = settle(TICKET, {"001": "2:0", "002": "1:0", "003": "2:1", "004": "1:0"})
    assert res["legHits"]["upset"] == [[True, False, True, False]]   # 3/4 部分命中=数据点
    assert res["upsetHit"] is False and res["payout"] == 0.0
    assert res["legHits"]["base"][0] == [True, False]                # 001 主胜✓ 002 平✗

def test_settle_full_hit_payout():
    res = settle(TICKET, {"001": "2:0", "002": "1:1", "003": "2:1", "004": "3:0"})
    assert res["upsetHit"] is True
    raw = 2 * 9.0 * 5.8 * 8.0 * 20.0 * 4
    assert abs(res["payout"] - raw) < 1e-6
    assert res["densityRecovered"] > 0

def test_settle_missing_result_marks_none():
    res = settle(TICKET, {"001": "2:0"})
    assert res["legHits"]["upset"][0][1] is None and res["upsetHit"] is False
