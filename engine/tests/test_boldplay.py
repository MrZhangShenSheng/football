import pytest
from boldplay import (band_ok, cap_multiplier, monthly_spend, budget_gate,
                      pick_upset_legs, build_ticket, SHAPES)

def test_band_ok_rules():
    assert band_ok({"h": 1.2, "d": 6.0, "a": 15.0}) == "偏好"     # 主胜去水 >= 0.60
    assert band_ok({"h": 3.5, "d": 3.5, "a": 2.2}) == "中性"      # max ≈ 0.44（brief 原 8/4/1.3 去水后 0.672 实为偏好，按裁定③意图修正）
    assert band_ok({"h": 4.0, "d": 3.5, "a": 1.9}) == "中性"      # max ≈ 0.50（同上，原 6/3.8/1.5 去水 0.608 为偏好）

def test_cap_multiplier():
    assert cap_multiplier(36540.0, 4) == 4            # 2*36540*4=29.2万 < 50万
    assert cap_multiplier(36540.0, 8) == 6            # 8倍=58.5万超限 → floor(50万/73080)=6
    assert cap_multiplier(250000.0, 5) == 1           # 单注已 50万 → 倍数 1
    assert cap_multiplier(100.0, 50) == 50            # 上限 50 倍

def test_monthly_spend_and_gate():
    recs = [{"date": "2026-08-24", "totalCost": 20}, {"date": "2026-08-25", "cost": 18}]
    assert monthly_spend(recs, "2026-08") == 38       # totalCost/cost 兼容
    assert budget_gate(220.0) is True and budget_gate(230.0) is False

def test_pick_upset_legs_shape_band():
    rows = [   # 已按 ev 降序
        {"matchNumStr": "周一001", "leagueId": "italy-serie-a", "n": 400, "score": "2:0", "odds": 12.0, "ev": -0.1},
        {"matchNumStr": "周一002", "leagueId": "italy-serie-a", "n": 400, "score": "3:1", "odds": 22.0, "ev": -0.3},
        {"matchNumStr": "周一003", "leagueId": "england-premier", "n": 300, "score": "1:1", "odds": 5.8, "ev": -0.2},
        {"matchNumStr": "周一004", "leagueId": None, "n": 0, "score": "5:0", "odds": 700.0, "ev": 4.39},
    ]
    guilin = pick_upset_legs(rows, "guilin")          # 带 10-17
    assert len(guilin) == 1 and guilin[0]["matchNumStr"] == "周一001"
    meizhou = pick_upset_legs(rows, "meizhou")        # 带 18-28
    assert [l["matchNumStr"] for l in meizhou] == ["周一002"]
    assert all(l["n"] > 0 for l in guilin + meizhou)  # n=0 先验噪声永不入选

def test_build_ticket_structure():
    odds_day = {"matches": [
        {"matchNumStr": f"周一00{i}", "league": "意甲", "home": f"H{i}", "away": f"A{i}",
         "had": {"h": 1.6, "d": 4.0, "a": 6.0},
         "crs": {"2:0": 9.0, "3:1": 22.0, "1:1": 5.8, "1:0": 6.5, "2:1": 8.0}} for i in (1, 2, 3, 4)]}
    t = build_ticket(odds_day, {"italy-serie-a": {"__n": 1000, "2:0": 90, "3:1": 30, "1:1": 115, "1:0": 98, "2:1": 86}}, seq=1)
    assert t["seq"] == 1 and t["shape"] == "guilin"   # 奇数轮桂林
    assert t["tiers"]["base"]["cost"] == 4 and len(t["tiers"]["base"]["legs"]) == 2
    assert t["tiers"]["mid"]["cost"] == 6
    assert 1 <= t["tiers"]["upset"]["multiplier"] <= 4
    assert t["tiers"]["upset"]["cost"] <= 10 and len(t["tiers"]["upset"]["legs"]) <= 4
    assert t["totalCost"] <= 20 and "postTaxNote" in t and "densityNote" in t
