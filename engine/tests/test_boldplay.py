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


# ---------- boldplay v2/v3（三档制+多池引擎+彩票档）回归薄壳 ----------
# 与 --selftest 同源（selftest 含 build_three_tier/render_ticket/settle 双形状/dry_streak
# /lottery 全链断言），pytest 入口保证 update.sh [7/7] 回归覆盖新代码。开发者 sszhang


def test_v2_selftest_full_chain():
    """v2/v3 全链：三档结构+可读性渲染+settle双形状+降半仓gate+彩票档（selftest 同源）。"""
    import boldplay as bp
    bp._selftest_three_tier()
    bp._selftest_settle()
    bp._selftest_dry_streak()
    bp._selftest_lottery()


def test_v2_upset_month_cap_constant():
    """月预算常量与 SKILL v5.5 文本承诺一致（40 元红线）。"""
    import boldplay as bp
    assert bp.MONTHLY_UPSET_CAP == 40


def test_filter_onsale_drops_finished_and_refreshes_had(tmp_path, monkeypatch):
    """2026-08-31 修复回归：跨日存档已完赛场（周日腿）被在售白名单滤掉，
    在售当前 HAD 价覆盖存档旧价（出票以终端实价为准）。开发者 sszhang"""
    import json as _json
    import boldplay as bp
    cache = tmp_path / "sporttery_matches.json"
    cache.write_text(_json.dumps({"matches": [
        {"code": "周一003", "had": {"h": "7.50", "d": "4.20", "a": "1.31"}},
    ]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(bp, "CACHE_DIR", tmp_path)
    day = {"matches": [
        {"matchNumStr": "周日011", "had": {"h": 1.57, "d": 3.8, "a": 4.4}},
        {"matchNumStr": "周一003", "had": {"h": 7.35, "d": 4.15, "a": 1.33}},
    ]}
    out = bp._filter_onsale(day)
    assert [m["matchNumStr"] for m in out["matches"]] == ["周一003"]
    assert out["matches"][0]["had"] == {"h": 7.5, "d": 4.2, "a": 1.31}


def test_filter_onsale_falls_back_when_cache_missing(tmp_path, monkeypatch):
    """在售缓存缺失/为空 → 原样退回（保持旧行为，不阻断出卡）。开发者 sszhang"""
    import boldplay as bp
    monkeypatch.setattr(bp, "CACHE_DIR", tmp_path)  # 无 sporttery_matches.json
    day = {"matches": [{"matchNumStr": "周日011", "had": {"h": 1.57, "d": 3.8, "a": 4.4}}]}
    out = bp._filter_onsale(day)
    assert [m["matchNumStr"] for m in out["matches"]] == ["周日011"]
    (tmp_path / "sporttery_matches.json").write_text('{"matches": []}', encoding="utf-8")
    out2 = bp._filter_onsale(day)
    assert [m["matchNumStr"] for m in out2["matches"]] == ["周日011"]


# ---------- 彩票档（docs/2026-09-02-lottery-tier-design.html）----------
# HAD/HHAD N串1×1倍=2元，合格腿全上 N∈[4,8]，p_fused≥0.55/超低赔≤1.25通道，
# 无预算管理（拍板C）。开发者 sszhang

_FAKE_DC = lambda m, zh: (2.0, 0.85, -0.05)   # 主强λ：p_dc 主胜 ~0.72


def _mk_had(i, h, d, a):
    return {"matchNumStr": f"周六00{i}", "match": f"m{i}", "league": "英超",
            "home": f"H{i}", "away": f"A{i}", "had": {"h": h, "d": d, "a": a}}


def test_lottery_constants_match_design():
    """常量与设计文档 §02 一致：门槛 0.55 / 超低赔 1.25+0.50 / 腿数窗 [4,8]。"""
    import boldplay as bp
    assert (bp.LOTTERY_MIN_P, bp.LOTTERY_LOW_ODDS, bp.LOTTERY_LOW_ODDS_MIN_P) == (0.55, 1.25, 0.50)
    assert (bp.LOTTERY_MIN_LEGS, bp.LOTTERY_MAX_LEGS) == (4, 8)


def test_lottery_legs_pool_dedup_and_ev_order():
    """合格腿全上 + 同场去重留 EV 最高 + EV 降序返回。"""
    import boldplay as bp
    day = {"matches": [_mk_had(i, 1.42 + 0.02 * i, 4.0, 6.2) for i in range(1, 6)]}
    hhad_map = {"周六001": {"goalLine": -1.0, "h": 2.10, "d": 3.30, "a": 3.05}}
    legs = bp._lottery_legs(day, zh={}, hhad_map=hhad_map, dc_params_fn=_FAKE_DC,
                            fusion=(0.4, 1.0))
    assert len(legs) == 5                                             # 池=合格场数全上
    assert sum(1 for l in legs if l["matchNumStr"] == "周六001") == 1  # 同场≤1腿（铁律9）
    assert [l["ev"] for l in legs] == sorted((l["ev"] for l in legs), reverse=True)
    assert all(l["p"] >= bp.LOTTERY_LOW_ODDS_MIN_P for l in legs)


def test_lottery_legs_below_threshold_excluded():
    """p_fused<0.55 且非超低赔（均势场）→ 不入池。"""
    import boldplay as bp
    day = {"matches": [_mk_had(1, 3.00, 3.20, 2.15)]}   # 市场主胜~0.29 vs DC~0.72 → p_fused~0.37
    assert bp._lottery_legs(day, zh={}, dc_params_fn=_FAKE_DC, fusion=(0.4, 1.0)) == []


def test_lottery_tier_open_close_and_cap():
    """池≥4 出 N串1×1倍=2元（bets 全索引单注）；<4 关档；>8 截前 8。"""
    import boldplay as bp
    leg = lambda i: {"matchNumStr": f"周六00{i}", "match": f"m{i}", "play": "had",
                     "pick": "主胜", "odds": 1.5, "p": 0.6, "ev": -0.1}
    t4 = bp._lottery_tier([leg(i) for i in range(1, 5)])
    assert t4["shape"] == "lottery-4x1" and t4["cost"] == 2
    assert t4["bets"] == [{"legs": [0, 1, 2, 3], "multiplier": 1}]
    assert t4["expOdds"] == 5.1 and t4["winIfHit"] == 10.0   # round 口径：1位/0位小数
    t3 = bp._lottery_tier([leg(i) for i in range(1, 4)])
    assert t3["shape"] == "closed" and t3["cost"] == 0 and "不硬凑" in t3["note"]
    t9 = bp._lottery_tier([leg(i) for i in range(1, 10)])
    assert t9["shape"] == "lottery-8x1" and len(t9["legs"]) == 8


def test_lottery_hhad_leg_hit_goal_line():
    """HHAD 让球判定：goalLine=-1 → 1:0让平 / 2:0让主 / 0:1让客；无 goalLine=None 待人工。"""
    import boldplay as bp
    base = {"play": "hhad", "pick": "让球平", "goalLine": -1.0}
    assert bp._leg_hit(base, "1:0", "hhad") is True
    assert bp._leg_hit({**base, "pick": "让球主胜"}, "2:0", "hhad") is True
    assert bp._leg_hit({**base, "pick": "让球客胜"}, "0:1", "hhad") is True
    assert bp._leg_hit({**base, "pick": "让球主胜"}, "1:0", "hhad") is False
    assert bp._leg_hit({"play": "hhad", "pick": "让球主胜"}, "1:0", "hhad") is None


def test_lottery_settle_all_hit_payout_and_break_zero():
    """彩票档结算走 bets 同源链：全中=2×Π赔率；断任一腿=0。"""
    import boldplay as bp
    legs = [{"matchNumStr": f"周六00{i}", "match": "m", "play": "had", "pick": "主胜",
             "odds": o} for i, o in enumerate((1.5, 1.7, 1.9, 2.1), 1)]
    tk = {"structure": "new", "totalCost": 24,
          "tiers": {"base": {"cost": 22, "legs": [], "bets": []},
                    "upset": {"shape": "closed", "cost": 0, "legs": []},
                    "lottery": bp._lottery_tier(legs)}}
    full = bp.settle(tk, {l["matchNumStr"]: "2:0" for l in legs})
    assert abs(full["tierPayout"]["lottery"] - 2 * 1.5 * 1.7 * 1.9 * 2.1) < 1e-9
    broke = bp.settle(tk, {l["matchNumStr"]: ("2:0" if i else "0:2")
                           for i, l in enumerate(legs)})          # 第0腿断
    assert broke["tierPayout"]["lottery"] == 0.0
