import pytest
from sporttery_fetch import extract_odds

def test_extract_odds_keys():
    m = {"matchNumStr": "周一001", "homeTeamAbbName": "博洛尼亚", "awayTeamAbbName": "拉齐奥",
         "leagueAbbName": "意甲", "matchTime": "2026-08-25 02:45", "sellStatus": 2,
         "had": {"h": "2.15", "d": "3.10", "a": "3.36"},
         "crs": {"s01s00": "6.50", "s01s01": "5.75", "s00s02": "17.00", "s1sh": "100.0",
                 "s1sd": "350.0", "s1sa": "200.0", "updateDate": "2026-08-24", "updateTime": "17:26:45"},
         "ttg": {"s0": "10.0", "s1": "4.5"}}
    out = extract_odds(m)
    assert out["crs"]["1:0"] == 6.5 and out["crs"]["1:1"] == 5.75
    assert out["crs"]["0:2"] == 17.0
    assert out["crs"]["胜其他"] == 100.0          # s1sh 映射
    assert out["had"]["d"] == 3.10
    assert out["oddsUpdatedAt"] == "2026-08-24 17:26:45"
    assert out["matchNumStr"] == "周一001"

def test_extract_odds_none_safe():
    assert extract_odds({"crs": None, "had": None}).get("crs") == {}   # 停售场不崩
