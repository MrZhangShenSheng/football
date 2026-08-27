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
        {"matchNumStr": "周一001", "leagueId": "italy-serie-a", "n": 400, "score": "2:0", "odds": 12.0, "ev": 0.5},
        {"matchNumStr": "周一002", "leagueId": "italy-serie-a", "n": 400, "score": "3:1", "odds": 22.0, "ev": 0.3},
        {"matchNumStr": "周一003", "leagueId": "england-premier", "n": 300, "score": "1:1", "odds": 16.0, "ev": -0.2},
        {"matchNumStr": "周一004", "leagueId": None, "n": 0, "score": "5:0", "odds": 700.0, "ev": 4.39},
    ]
    guilin = pick_upset_legs(rows, "guilin")          # 带 10-17
    assert len(guilin) == 1 and guilin[0]["matchNumStr"] == "周一001"
    meizhou = pick_upset_legs(rows, "meizhou")        # 带 18-28
    assert [l["matchNumStr"] for l in meizhou] == ["周一002"]
    assert all(l["n"] > 0 for l in guilin + meizhou)  # n=0 先验噪声永不入选

def test_pick_upset_legs_positive_ev_guard():
    """带内但负 EV 的行永不入选；无带内正 EV 行 → 空（触发频率退路）。"""
    rows = [
        {"matchNumStr": "周一001", "leagueId": "italy-serie-a", "n": 400, "score": "2:0", "odds": 12.0, "ev": -0.1},
        {"matchNumStr": "周一002", "leagueId": "italy-serie-a", "n": 400, "score": "3:1", "odds": 22.0, "ev": -0.3},
        {"matchNumStr": "周一003", "leagueId": "england-premier", "n": 300, "score": "1:1", "odds": 16.0, "ev": -0.2},
    ]
    assert pick_upset_legs(rows, "guilin") == []      # 带内全负 EV
    assert pick_upset_legs(rows, "meizhou") == []
    ok = [{"matchNumStr": "周一001", "leagueId": "italy-serie-a", "n": 400,
           "score": "2:0", "odds": 12.0, "ev": 0.1}]
    assert [l["matchNumStr"] for l in pick_upset_legs(ok, "guilin")] == ["周一001"]
    assert pick_upset_legs(rows + [{"matchNumStr": "周一001", "n": 0, "score": "2:0",
                                    "odds": 12.0, "ev": None}], "guilin") == []  # ev 缺失/None 同样排除

def test_build_ticket_structure():
    odds_day = {"matches": [
        {"matchNumStr": f"周一00{i}", "league": "意甲", "home": f"H{i}", "away": f"A{i}",
         "had": {"h": 1.6, "d": 4.0, "a": 6.0},
         "crs": {"2:0": 9.0, "3:1": 22.0, "1:1": 5.8, "1:0": 6.5, "2:1": 8.0}} for i in range(1, 7)]}
    t = build_ticket(odds_day, {"italy-serie-a": {"__n": 1000, "2:0": 90, "3:1": 30, "1:1": 115, "1:0": 98, "2:1": 86}}, seq=1, method="amix")
    assert t["seq"] == 1 and t["shape"] == "guilin"   # 奇数轮桂林
    base = t["tiers"]["base"]
    assert base["cost"] == 4 and len(base["legs"]) == 2          # 2 注 × 2 元
    assert all(len(note) == 4 for note in base["legs"])          # 每注 4 串
    shared = {l["matchNumStr"] for l in base["legs"][0]} & {l["matchNumStr"] for l in base["legs"][1]}
    assert len(shared) == 2                                      # 共享场次 ≤2
    assert "degraded" not in base
    mid = t["tiers"]["mid"]
    assert mid["cost"] == 6 and mid["multiplier"] == 3           # 5串1 ×3 倍 = 6 元
    assert mid["play"] == "had-5串1×3倍" and len(mid["legs"][0]) == 5
    assert "degraded" not in mid
    assert 1 <= t["tiers"]["upset"]["multiplier"] <= 4
    assert t["tiers"]["upset"]["cost"] <= 10 and len(t["tiers"]["upset"]["legs"]) <= 4
    assert t["totalCost"] == sum(v["cost"] for v in t["tiers"].values()) <= 20
    assert "postTaxNote" in t and "densityNote" in t

def test_build_ticket_base_degraded_four_pool():
    """池 4-5 场 → base 单注降级（4 串 1 注 2 元）。"""
    odds_day = {"matches": [
        {"matchNumStr": f"周一00{i}", "league": "意甲", "home": f"H{i}", "away": f"A{i}",
         "had": {"h": 1.6, "d": 4.0, "a": 6.0},
         "crs": {"2:0": 9.0, "3:1": 22.0, "1:1": 5.8, "1:0": 6.5, "2:1": 8.0}} for i in (1, 2, 3, 4)]}
    t = build_ticket(odds_day, {"italy-serie-a": {"__n": 1000, "2:0": 90, "3:1": 30, "1:1": 115, "1:0": 98, "2:1": 86}}, seq=1, method="amix")
    base = t["tiers"]["base"]
    assert base["cost"] == 2 and len(base["legs"]) == 1 and len(base["legs"][0]) == 4
    assert base["degraded"] is True

def test_upset_legs_schema():
    """Task 6 settle() 接口：每条翻身腿必须有 play=crs + pick=比分串（两条路径都覆盖）。"""
    odds_day = {"matches": [
        {"matchNumStr": f"周一00{i}", "league": "意甲", "home": f"H{i}", "away": f"A{i}",
         "had": {"h": 1.6, "d": 4.0, "a": 6.0},
         "crs": {"2:0": 9.0, "3:1": 22.0, "1:1": 5.8, "1:0": 6.5, "2:1": 8.0}} for i in (1, 2, 3, 4)]}
    t = build_ticket(odds_day, {"italy-serie-a": {"__n": 1000, "2:0": 90, "3:1": 30, "1:1": 115, "1:0": 98, "2:1": 86}}, seq=1, method="amix")
    assert all(l["play"] == "crs" and l["pick"] == l.get("score") for l in t["tiers"]["upset"]["legs"])
    # pick_upset_legs 直取路径（带内 12.0）
    rows = [{"matchNumStr": "周一001", "leagueId": "italy-serie-a", "n": 400, "score": "2:0", "odds": 12.0, "ev": 0.4}]
    t2 = build_ticket({"matches": odds_day["matches"][:1] + [
        {"matchNumStr": f"周一00{i}", "league": "意甲", "home": f"H{i}", "away": f"A{i}",
         "had": {"h": 1.6, "d": 4.0, "a": 6.0}, "crs": {"2:0": 12.0, "1:1": 5.8}} for i in (2, 3, 4)]}, {}, seq=3, method="amix")
    # freq_table 为空 → ev_scan 无带内行 → 走 fallback；断言兜底路径同样规范
    assert all(l["play"] == "crs" and l["pick"] == l.get("score") for l in t2["tiers"]["upset"]["legs"])
    for l in pick_upset_legs(rows, "guilin"):
        assert l["score"] == "2:0"  # 原始字段保留，规范化在 build_ticket 完成

def test_thin_pool_costs_truthful():
    """池薄时成本真实化 + degraded 标注（实跑 1 腿场景）。"""
    odds_day = {"matches": [
        {"matchNumStr": "周二005", "league": "欧冠", "home": "LASK", "away": "凯尔特人",
         "had": {"h": 2.5, "d": 3.2, "a": 2.7}, "crs": {"1:0": 11.0, "1:1": 6.0}}]}
    t = build_ticket(odds_day, {}, seq=1, method="amix")
    assert t["tiers"]["base"]["cost"] == 2 and t["tiers"]["base"]["degraded"] is True   # 仅 1 非空注组
    assert t["tiers"]["mid"]["cost"] == 6 and t["tiers"]["mid"]["multiplier"] == 3      # 1 腿仍 ×3 倍真实成本
    assert t["tiers"]["mid"]["degraded"] is True    # 1 腿 <5
    assert len(t["tiers"]["upset"]["legs"]) == 1 and t["tiers"]["upset"]["degraded"] is True  # 1 腿 <4
    assert t["totalCost"] == t["tiers"]["base"]["cost"] + t["tiers"]["mid"]["cost"] + t["tiers"]["upset"]["cost"]
