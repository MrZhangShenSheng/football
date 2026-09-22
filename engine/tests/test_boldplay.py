import pytest
import boldplay as bp
from boldplay import (band_ok, cap_multiplier, monthly_spend, budget_gate,
                      pick_upset_legs, build_ticket, build_three_tier, SHAPES)

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
    """Task 6 settle() 接口：每条翻身腿必须有 play=crs + pick=比分串（两条路径都覆盖）。

    2026-09-16 Task3 放行无库场次后 mix 路径在本 fixture 激活（假队名全无库，
    原走 fallback）：mix 腿带 pick 键、fallback 腿带 score+pick 键——settle._leg_hit
    结算口径 = pick or score，两 schema 均合法，断言按结算兼容性写而非锁死单一键名。"""
    odds_day = {"matches": [
        {"matchNumStr": f"周一00{i}", "league": "意甲", "home": f"H{i}", "away": f"A{i}",
         "had": {"h": 1.6, "d": 4.0, "a": 6.0},
         "crs": {"2:0": 9.0, "3:1": 22.0, "1:1": 5.8, "1:0": 6.5, "2:1": 8.0}} for i in (1, 2, 3, 4)]}
    t = build_ticket(odds_day, {"italy-serie-a": {"__n": 1000, "2:0": 90, "3:1": 30, "1:1": 115, "1:0": 98, "2:1": 86}}, seq=1, method="amix")
    assert all(l["play"] == "crs" and ":" in str(l["pick"]) for l in t["tiers"]["upset"]["legs"])
    # pick_upset_legs 直取路径（带内 12.0）
    rows = [{"matchNumStr": "周一001", "leagueId": "italy-serie-a", "n": 400, "score": "2:0", "odds": 12.0, "ev": 0.4}]
    t2 = build_ticket({"matches": odds_day["matches"][:1] + [
        {"matchNumStr": f"周一00{i}", "league": "意甲", "home": f"H{i}", "away": f"A{i}",
         "had": {"h": 1.6, "d": 4.0, "a": 6.0}, "crs": {"2:0": 12.0, "1:1": 5.8}} for i in (2, 3, 4)]}, {}, seq=3, method="amix")
    # Task3 后无库场次直接进 mix（不再退 fallback）；mix 腿 schema=pick 键，同样可结算
    assert all(l["play"] == "crs" and ":" in str(l["pick"]) for l in t2["tiers"]["upset"]["legs"])
    for l in pick_upset_legs(rows, "guilin"):
        assert l["score"] == "2:0"  # 原始字段保留，规范化在 build_ticket 完成

def test_thin_pool_costs_truthful():
    """池薄时成本真实化 + degraded 标注（实跑 1 腿场景）。

    2026-09-16 Task3 放行无库场次后：1:1 赔率压到 3.6（低于下限 4.0）使其不入
    mix——否则无库腿 1:0+1:1 两条进 mix[:4]，upset 变 2 腿，1 腿场景不成立。"""
    odds_day = {"matches": [
        {"matchNumStr": "周二005", "league": "欧冠", "home": "LASK", "away": "凯尔特人",
         "had": {"h": 2.5, "d": 3.2, "a": 2.7}, "crs": {"1:0": 11.0, "1:1": 3.6}}]}
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
# HAD/HHAD N串1×1倍=2元，合格腿全上 N∈[4,8]，p_fused≥0.55（超低赔通道 09-22 撤），
# 无预算管理（拍板C）。2026-09-22 放宽：3 腿降级 3串1 选项卡，<3 关档。开发者 sszhang

_FAKE_DC = lambda m, zh: (2.0, 0.85, -0.05)   # 主强λ：p_dc 主胜 ~0.72


def _mk_had(i, h, d, a):
    return {"matchNumStr": f"周六00{i}", "match": f"m{i}", "league": "英超",
            "home": f"H{i}", "away": f"A{i}", "had": {"h": h, "d": d, "a": a}}


def test_lottery_constants_match_design():
    """常量与设计文档 §02 一致：门槛 0.55 / 腿数窗 [4,8]；超低赔通道已撤（0 触发死代码）。"""
    import boldplay as bp
    assert bp.LOTTERY_MIN_P == 0.55
    assert not hasattr(bp, "LOTTERY_LOW_ODDS")
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
    assert all(l["p"] >= bp.LOTTERY_MIN_P for l in legs)


def test_lottery_legs_below_threshold_excluded():
    """p_fused<0.55 → 不入池。"""
    import boldplay as bp
    day = {"matches": [_mk_had(1, 3.00, 3.20, 2.15)]}   # 市场主胜~0.29 vs DC~0.72 → p_fused~0.37
    assert bp._lottery_legs(day, zh={}, dc_params_fn=_FAKE_DC, fusion=(0.4, 1.0)) == []


def test_lottery_tier_open_close_and_cap():
    """池≥4 出 N串1×1倍=2元（bets 全索引单注）；3 腿降级 3串1 选项卡（09-22 放宽）；
    <3 关档（2 腿 HAD 串=废除的低赔碎注形状）；>8 截前 8。"""
    import boldplay as bp
    leg = lambda i: {"matchNumStr": f"周六00{i}", "match": f"m{i}", "play": "had",
                     "pick": "主胜", "odds": 1.5, "p": 0.6, "ev": -0.1}
    t4 = bp._lottery_tier([leg(i) for i in range(1, 5)])
    assert t4["shape"] == "lottery-4x1" and t4["cost"] == 2
    assert t4["bets"] == [{"legs": [0, 1, 2, 3], "multiplier": 1}]
    assert t4["expOdds"] == 5.1 and t4["winIfHit"] == 10.0   # round 口径：1位/0位小数
    t3 = bp._lottery_tier([leg(i) for i in range(1, 4)])
    assert t3["shape"] == "lottery-3x1" and t3["cost"] == 2 and "放宽" in t3["note"]
    t2 = bp._lottery_tier([leg(i) for i in range(1, 3)])
    assert t2["shape"] == "closed" and t2["cost"] == 0 and "不硬凑" in t2["note"]
    t9 = bp._lottery_tier([leg(i) for i in range(1, 10)])
    assert t9["shape"] == "lottery-8x1" and len(t9["legs"]) == 8


def test_card_assertions_tier_rules():
    """机器断言（09-22 清单分流）：同注同场限一玩法 / 混串木桶 / 预算红线。"""
    import boldplay as bp
    leg = lambda code, play, odds=1.5: {"matchNumStr": code, "match": "m", "play": play,
                                        "pick": "x", "odds": odds, "p": 0.6, "ev": -0.1}
    card = {
        "tiers": {
            "搏奖档": {"cost": 28, "legs": [leg("周一001", "crs"), leg("周一001", "had")],
                     "bets": [{"legs": [0, 1], "multiplier": 1}]},
            "翻身档": {"cost": 6, "legs": [leg("周二002", "crs"), leg("周三003", "crs"),
                                       leg("周四004", "had"), leg("周五005", "had"), leg("周六006", "had")],
                     "bets": [{"legs": [0, 1, 2, 3, 4], "multiplier": 1}]},
        },
    }
    bp.validate_card_assertions(card)
    joined = "\n".join(card["warnings"])
    assert "同场多玩法违规" in joined                     # 官方第七条
    assert "木桶违规" in joined                          # CRS 木桶 4 < 5 关
    assert "红线" in joined                              # 28+6=34 > 30
    clean = {"tiers": {"彩票档": {"cost": 2,
                                "legs": [leg(f"周{i}001", "had") for i in ("一", "二", "三", "四")],
                                "bets": [{"legs": [0, 1, 2, 3], "multiplier": 1}]}}}
    bp.validate_card_assertions(clean)
    assert clean["warnings"] == []


def test_coupling_at_least_one_shared_leg():
    """耦合"至少中一注"精确对拍（SKILL v5.1 脚本化）：两注共享一腿的手算期望值。
    A/B/C 各 p=0.5，注1={A,B} 注2={A,C}：P(全灭)=A灭(0.5)+A中B灭C灭(0.125)=0.625。"""
    import boldplay as bp
    mk = lambda c: {"matchNumStr": c, "match": "m", "play": "had", "pick": "主胜",
                    "odds": 2.0, "p": 0.5, "ev": 0.0}
    card = {"tiers": {"t": {"legs": [mk("001"), mk("002"), mk("003")],
                            "bets": [{"legs": [0, 1], "multiplier": 1},
                                     {"legs": [0, 2], "multiplier": 1}]}}}
    r = bp.coupling_at_least_one(card)
    assert r["atLeastOne"] == 0.375                       # 1-0.625 精确
    assert r["independent"] == 0.4375                     # 1-0.75² 独立近似（高估）
    assert r["overestimatePp"] == 6.2                     # round(6.25,1)
    assert r["method"] == "enum-2^3" and r["pSource"] == "model"


def test_coupling_cross_tier_dedup_and_implied():
    """跨档共享腿折叠（保底⊂彩票同腿只算一次）+ 无概率腿 implied 兜底。"""
    import boldplay as bp
    shared = {"matchNumStr": "001", "match": "m", "play": "had", "pick": "主胜",
              "odds": 2.0, "p": 0.5, "ev": 0.0}
    bare = {"matchNumStr": "002", "match": "m", "play": "crs", "pick": "1:1", "odds": 8.0}
    card = {"tiers": {
        "a档": {"legs": [shared], "bets": [{"legs": [0], "multiplier": 1}]},
        "b档": {"legs": [dict(shared), bare], "bets": [{"legs": [0, 1], "multiplier": 1}]},
    }}
    r = bp.coupling_at_least_one(card)
    assert r["legs"] == 2                                  # shared 跨档折叠为同节点
    assert r["pSource"] == "mixed"                         # bare 无 p → implied
    # 手算：p(A)=0.5, p(bare)=1/8×0.661=0.082625；注1={A} 注2={A,bare}
    # A 中则注1必中 → 至少中一注 = P(A) = 0.5（dead=A灭0.5×[bare灭0.917375+bare中0.082625]）
    assert abs(r["atLeastOne"] - 0.5) < 0.0002
    assert abs(r["independent"] - 0.5207) < 0.0002         # 1-(0.5×0.917375) 独立近似


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


# ---------- 保底分层选腿（docs/2026-09-06-confidence-tiering-design.html §四/§五）----------
# T1: 2胆(p>=0.75)+3腿(p>=0.60)共5条·tier字段·踩线护栏(1.35/0.68)。
# had 赔率按 devig 反推档位（dc_params_fn=None → p=纯市场去水）。开发者 sszhang


def _tiered_day():
    """7场假数据（联赛全 fd 锚·英超）：2胆(@1.15→p≈0.777/@1.18→p≈0.757) + 1踩线
    (@1.32→p≈0.663<0.68) + 3标准(@1.35→0.681/@1.45→0.650/@1.55→0.622) + 1低门槛
    (@1.70→p≈0.581<0.60)——胆/标准/踩线/门槛四态全覆盖。"""
    return {"matches": [
        _mk_had(1, 1.15, 6.00, 12.0),   # dan
        _mk_had(2, 1.18, 5.50, 11.0),   # dan
        _mk_had(3, 1.32, 3.90, 7.8),    # 踩线: o<1.35 且 p<0.68 → 排除
        _mk_had(4, 1.35, 4.50, 8.0),    # std
        _mk_had(5, 1.45, 4.20, 7.5),    # std
        _mk_had(6, 1.55, 4.00, 7.0),    # std
        _mk_had(7, 1.70, 3.80, 6.2),    # p<0.60 → 不入保底
    ]}


def _tiered_day_few():
    """少场次轮：仅 3 场合格（1胆+2标准）+低门槛+踩线+无锚噪声——短列表返回。"""
    return {"matches": [
        _mk_had(1, 1.15, 6.00, 12.0),
        _mk_had(2, 1.45, 4.20, 7.5),
        _mk_had(3, 1.55, 4.00, 7.0),
        _mk_had(4, 1.70, 3.80, 6.2),                          # p<0.60 不合格
        _mk_had(5, 1.32, 3.90, 7.8),                          # 踩线不合格
        {**_mk_had(6, 1.15, 6.00, 12.0), "league": "日职"},   # 无 fd 锚被滤（铁律10）
    ]}


class TestBaseTieredLegs:
    """保底3*4*5分层选腿(设计§四/§五): 2胆+3腿·5条返回·踩线过滤·补位规则"""

    def test_returns_five_legs_with_tier_field(self):
        import boldplay as bp
        legs = bp._base_legs(_tiered_day(), zh={}, dc_params_fn=lambda m, z: None)
        assert len(legs) == 5
        assert all("tier" in l for l in legs)
        dans = [l for l in legs if l["tier"] == "dan"]
        stds = [l for l in legs if l["tier"] == "std"]
        # 胆级优先2席; 不足2条胆时高p标准腿补位(补位腿tier=std, 审核修订B)
        assert len(dans) == 2
        assert len(dans) + len(stds) == 5

    def test_dan_requires_p075(self):
        import boldplay as bp
        legs = bp._base_legs(_tiered_day(), zh={}, dc_params_fn=lambda m, z: None)
        for l in legs:
            if l["tier"] == "dan":
                assert l["p"] >= bp.BASE_TIER_DAN_P

    def test_treadline_filter(self):
        # 踩线降级(设计§四): 赔率<1.35 且 p<0.68 不入保底(朗斯案护栏)
        import boldplay as bp
        legs = bp._base_legs(_tiered_day(), zh={}, dc_params_fn=lambda m, z: None)
        for l in legs:
            assert not (l["odds"] < bp.BASE_TREADLINE_ODDS and l["p"] < bp.BASE_TREADLINE_P)

    def test_std_threshold_060(self):
        # 标准腿门槛 p>=0.60(设计§4.4: 0.60-0.65段实测74%)
        import boldplay as bp
        legs = bp._base_legs(_tiered_day(), zh={}, dc_params_fn=lambda m, z: None)
        assert all(l["p"] >= bp.BASE_TIER_STD_P for l in legs)

    def test_fewer_than_five_closes(self):
        # 零腿轮(设计§四): 合格腿<5返回短列表(关档由调用方判断)
        import boldplay as bp
        legs = bp._base_legs(_tiered_day_few(), zh={}, dc_params_fn=lambda m, z: None)
        assert len(legs) < 5

    def test_single_dan_backfill_with_std(self):
        # T1 审查 Minor-1 补测(审核修订B): 单胆轮——1条胆级@1.15 + 5条标准档,
        # 胆不足2席时高p标准腿按标准档口径补位: dans==1 且第5条腿(补位末席)tier=="std"
        import boldplay as bp
        day = {"matches": [
            _mk_had(1, 1.15, 6.00, 12.0),   # 唯一胆级 p≈0.777
            _mk_had(2, 1.35, 4.50, 8.0),    # 标准档 p≈0.681
            _mk_had(3, 1.38, 4.40, 7.8),    # 标准档 p≈0.671
            _mk_had(4, 1.40, 4.30, 7.6),    # 标准档 p≈0.662
            _mk_had(5, 1.45, 4.20, 7.5),    # 标准档 p≈0.650
            _mk_had(6, 1.55, 4.00, 7.0),    # 标准档 p≈0.622
        ]}
        legs = bp._base_legs(day, zh={}, dc_params_fn=lambda m, z: None)
        assert len(legs) == 5
        assert sum(1 for l in legs if l["tier"] == "dan") == 1   # 胆仅1条不虚标
        assert legs[4]["tier"] == "std"                          # 补位腿按标准档口径


# ---------- 保底档 3*4*5 重构 + 覆盖闸（设计§四/§4.1·Task 2 2026-09-06）----------
# 16 注 32 元（3串1×10+4串1×5+5串1×1）bets 显式声明；覆盖闸 coverGate
# (P_full≥32n+N，n=1 无叙事仓)；轮红线检查废除（legacy 档保留旧预算逻辑）。开发者 sszhang


def _fake_day():
    """build_three_tier 入口假数据 = T1 分层 fixture（恰 5 条合格腿：2胆+3标准 →
    保底 3*4*5 开档；@1.70 场 p<0.60 不入保底；周六006/007 兼作 A/B 三池卡场）。"""
    return _tiered_day()


def _fake_table():
    """freq 模板假数据：英超 n=200 免 low_conf（selftest 德乙模板同源口径）。"""
    from collections import Counter
    return {"england-premier": Counter({"1:1": 120, "2:2": 40, "__n": 200})}


class TestThreeByFourByFive:
    """保底3*4*5机制(设计§四): 16注32元·bets显式·覆盖闸P_full>=32n+N。
    2026-09-16 起默认关档(spec §1.4·BASE_TIER_ENABLED=False·大哥「不保本无所谓」),
    本类开档断言改经 monkeypatch 显式开档运行——保留复活路径的机制覆盖。"""

    def test_base_tier_closed_by_default(self):
        # spec §1.4: 大哥「资金小量不保本其实无所谓」——保底档设计目的即保本,冲突故关。
        # 显式开关关档:_base_legs 有独立门槛 min(o3)<1.10 不读 ODDS_RANGE,改赔率域不会自动关它。
        t = build_three_tier(_fake_day(), _fake_table(), seq=9, zh={}, form={})
        base = t["tiers"]["base"]
        assert base["cost"] == 0 and base["coverGate"] is None
        assert "关档" in base["note"]
        assert not base.get("bets")              # 关档不产生注

    def test_base_shape_16_bets(self, monkeypatch):
        monkeypatch.setattr(bp, "BASE_TIER_ENABLED", True)   # 显式开档测机制
        t = build_three_tier(_fake_day(), _fake_table(), seq=9, zh={}, form={})
        base = t["tiers"]["base"]
        assert base["play"] == "had-3*4*5"
        assert base["cost"] == 32
        assert len(base["bets"]) == 16            # C(5,3)+C(5,4)+C(5,5)=10+5+1
        sizes = {len(b["legs"]) for b in base["bets"]}
        assert sizes == {3, 4, 5}                 # 3串1×10+4串1×5+5串1×1
        assert len(base["legs"]) == 5             # 全消费 5 腿（T1 过渡[:4]切片已移除）

    def test_cover_gate_ok(self, monkeypatch):
        monkeypatch.setattr(bp, "BASE_TIER_ENABLED", True)
        t = build_three_tier(_fake_day(), _fake_table(), seq=9, zh={}, form={})
        gate = t["tiers"]["base"]["coverGate"]
        assert "pFull" in gate and "cap" in gate and gate["ok"] in (True, False)
        # n=1无叙事仓时: P_full >= 32 必然成立(设计§4.1)
        assert gate["pFull"] >= 32 or not gate["ok"]

    def test_cover_gate_narrative_cap(self, monkeypatch):
        # 覆盖闸(设计§4.1): 叙事仓上限 = P_full - 32n
        monkeypatch.setattr(bp, "BASE_TIER_ENABLED", True)
        t = build_three_tier(_fake_day(), _fake_table(), seq=9, zh={}, form={})
        gate = t["tiers"]["base"]["coverGate"]
        assert gate["cap"] == round(gate["pFull"] - 32, 2)

    def test_payout_full_hit(self):
        from boldplay import payout_full_hit
        legs = [{"odds": 1.3}, {"odds": 1.4}, {"odds": 1.5}, {"odds": 1.6}, {"odds": 1.7}]
        p = payout_full_hit(legs, unit=2.0, mult=1)
        assert abs(p - (2*(1.3*1.4*1.5 + 1.3*1.4*1.6 + 1.3*1.4*1.7 + 1.3*1.5*1.6 + 1.3*1.5*1.7 + 1.3*1.6*1.7 + 1.4*1.5*1.6 + 1.4*1.5*1.7 + 1.4*1.6*1.7 + 1.5*1.6*1.7
                          + 1.3*1.4*1.5*1.6 + 1.3*1.4*1.5*1.7 + 1.3*1.4*1.6*1.7 + 1.3*1.5*1.6*1.7 + 1.4*1.5*1.6*1.7
                          + 1.3*1.4*1.5*1.6*1.7))) < 0.01

    def test_base_closed_when_legs_short(self):
        # 零腿轮关档(设计§四): 合格腿<5 → cost=0 · coverGate=None · 不硬凑
        t = build_three_tier(_tiered_day_few(), _fake_table(), seq=9, zh={}, form={})
        base = t["tiers"]["base"]
        assert base["play"] == "had-3*4*5"
        assert base["cost"] == 0 and base["coverGate"] is None
        assert not base.get("bets")

    def test_hypothesis_warning_on_card(self, monkeypatch):
        # Task 6 接入：卡上须有 pending 腿告警（闸门全撤后唯一拦截）
        monkeypatch.setattr(bp, "BASE_TIER_ENABLED", True)
        t = build_three_tier(_fake_day(), _fake_table(), seq=9, zh={}, form={})
        assert any("假设未通过" in w for w in t.get("warnings", []))

    def test_round_redline_removed(self, monkeypatch):
        # 设计§四: 轮红线废除。2026-09-16 起保底默认关档,开档断言经 monkeypatch 显式开档:
        # totalCost 32 已超旧红线 30，也不再落 budgetWarning
        monkeypatch.setattr(bp, "BASE_TIER_ENABLED", True)
        t = build_three_tier(_fake_day(), _fake_table(), seq=9, zh={}, form={})
        assert t["totalCost"] >= 32
        assert "budgetWarning" not in t


# ---------- 终审补测（2026-09-16）：索引→腿键转换路径 ----------


def test_bets_index_refs_converted_to_leg_keys():
    """bets[].legs 是**索引整数**时须转成规范腿键。

    原共用腿测试用字符串腿 ['A','X'] 且 tier 无 legs 列表，走的是非索引透传分支,
    索引转换代码从未被覆盖（终审变异实验：整行退回裸透传，358 全过）。
    索引不转换会把不同档的索引 0 当成同一条腿而误报共用。"""
    tier = {"legs": [{"matchNumStr": "001", "play": "crs", "pick": "4:0"},
                     {"matchNumStr": "002", "play": "crs", "pick": "2:1"}],
            "bets": [{"legs": [0, 1]}, {"legs": [1]}]}
    bets = bp._bets_with_leg_keys(tier)
    assert bets[0]["legs"] == ["001|crs|4:0", "002|crs|2:1"]
    assert bets[1]["legs"] == ["002|crs|2:1"]
    warns = bp.check_shared_legs(bets)
    assert any("002|crs|2:1" in w for w in warns), "索引 1 被两注复用须告警"
    assert not any("4:0" in w for w in warns), "索引 0 只用一次不得告警"


def test_leg_key_includes_match_code():
    """腿键须含场次段——不同场的同一比分不是共用腿。

    终审变异：_leg_key 退回只用 pick，358 全过 = 无覆盖。"""
    a = bp._leg_key({"matchNumStr": "001", "play": "crs", "pick": "2:1"})
    b = bp._leg_key({"matchNumStr": "007", "play": "crs", "pick": "2:1"})
    assert a != b, "不同场次的 2:1 必须是不同腿键"
    assert bp.check_shared_legs([{"legs": [a]}, {"legs": [b]}]) == []


def test_lottery_high_divergence_leg_not_excluded():
    """彩票档高分歧腿只标注不排除（Task 2 撤熔断，终审 C4 补测）。

    终审变异：还原 P0-3 的 `continue` 熔断，358 全过 = 撤销从未被锁住。
    分歧值无法区分「敢跟市场对赌」与「DC 参数算坏」，故降级为标注 + 假设层人工否证。"""
    m = {"matchNumStr": "006", "league": "西甲", "home": "马竞", "away": "奥萨苏纳",
         "had": {"h": 1.30, "d": 5.0, "a": 9.0}}
    day = {"matches": [m]}
    blocked = []
    legs = bp._lottery_legs(day, {}, dc_params_fn=lambda mm, z: (3.2, 0.35, 0.0),
                            blocked=blocked)   # p_fused 0.786 vs p_mkt → 7.4pp > 5pp 线
    flagged = [l for l in legs if l.get("divergenceFlag")]
    assert flagged, "高分歧腿须仍在候选池（标注不排除）"
    assert blocked, "高分歧须归档供卡面 warnings 展示"
    codes = {l["matchNumStr"] for l in legs}
    assert "006" in codes, "该场不得因分歧被整场剔除"


def test_combinatorial_tier_no_false_shared_leg_warning():
    """组合式档（3*4*5 复式）的腿共用是设计本意，不得报伪分散（终审 C6）。

    T033 伪分散指手工挑三注却共用同一条腿——表面分散实为单点。3*4*5 是复式全组合,
    部分命中即回款，腿必然跨注复用。实测复活保底档会喷 5 条误报告警。"""
    from itertools import combinations
    legs = [{"matchNumStr": f"00{i}", "play": "had", "pick": "主胜", "odds": 1.5}
            for i in range(1, 6)]
    bets = [{"legs": list(c), "multiplier": 1}
            for n in (3, 4, 5) for c in combinations(range(5), n)]
    t = {"tiers": {"base": {"play": "had-3*4*5", "cost": 30,
                            "legs": legs, "bets": bets}}}
    bp.annotate_hypothesis_warnings(t)
    shared = [w for w in (t.get("warnings") or []) if "伪分散" in w]
    assert shared == [], f"组合式档不得报伪分散，实得 {len(shared)} 条"


def test_handpicked_tier_still_warns_shared_leg():
    """非组合式档仍须报伪分散——别把 T033 的真告警一起关掉。"""
    legs = [{"matchNumStr": "001", "play": "crs", "pick": "4:0", "odds": 50.0},
            {"matchNumStr": "010", "play": "crs", "pick": "2:1", "odds": 7.25},
            {"matchNumStr": "003", "play": "crs", "pick": "3:0", "odds": 60.0}]
    bets = [{"legs": [0, 1]}, {"legs": [2, 1]}]       # 索引 1 被两注复用
    t = {"tiers": {"upset": {"play": "mix-2串1", "cost": 4,
                             "legs": legs, "bets": bets}}}
    bp.annotate_hypothesis_warnings(t)
    assert any("伪分散" in w and "010|crs|2:1" in w for w in t["warnings"])


def test_lottery_legs_carry_hypothesis():
    """彩票档两处腿生产点都须带 hypothesis 容器（终审 C5）。

    靠 filter_buyable「无字段视为 pending」兜底虽不漏放行，但卡面数据里没有
    assumption/checks 容器可回填功课，等于假设层对彩票档失效。"""
    m = {"matchNumStr": "003", "home": "阿布艾因", "away": "沙迦"}
    legs = bp._odds_only_had_legs(m, "003", {"h": 2.0, "d": 3.3, "a": 4.5})
    assert legs and "hypothesis" in legs[0], "_odds_only_had_legs 缺 hypothesis"
    assert legs[0]["hypothesis"]["verdict"] == "pending"
    assert "checks" in legs[0]["hypothesis"], "须有 checks 容器可回填"


# ---------- Task 7（2026-09-16）：卡面排序与呈现（spec §三）----------


def _grouped_legs():
    """同场两腿（长尾无模型 + 低赔 DC）+ 另一场一腿，验证分组与组内降序。"""
    return [
        {"matchNumStr": "001", "match": "A-B", "play": "crs", "pick": "1:0",
         "odds": 6.5, "modelSupport": "dc", "divergenceFlag": False,
         "hypothesis": {"verdict": "pending"}},
        {"matchNumStr": "001", "match": "A-B", "play": "crs", "pick": "4:0",
         "odds": 175.0, "modelSupport": "none", "divergenceFlag": False,
         "hypothesis": {"verdict": "pending"}},
        {"matchNumStr": "002", "match": "C-D", "play": "ttg", "pick": "s3",
         "odds": 12.0, "modelSupport": "template", "divergenceFlag": True,
         "hypothesis": {"verdict": "survived"}},
    ]


def test_card_groups_by_match_odds_desc():
    """卡面按场次分组、组内赔率降序(spec §三)：排序用赔率决定先看到哪条，
    选腿用假设决定买不买。不定义排序则'只看最上面几条'会成隐性门槛。"""
    txt = bp.render_legs_grouped(_grouped_legs())
    assert txt.index("4:0") < txt.index("1:0"), "组内须赔率降序"
    assert txt.index("001") < txt.index("002"), "按场次编号分组"
    assert "无模型" in txt and "DC" in txt and "模板" in txt   # 三种 modelSupport 都可辨


def test_card_marks_verdict_and_divergence():
    """每行标 verdict(✓survived/○pending) 与 ⚠分歧旗——卡面须能一眼看出功课状态。"""
    txt = bp.render_legs_grouped(_grouped_legs())
    lines = txt.splitlines()
    tail = [l for l in lines if "s3" in l][0]
    assert "✓" in tail and "⚠分歧" in tail        # survived + 分歧标注
    pend = [l for l in lines if "4:0" in l][0]
    assert "○" in pend                             # pending 标记


def test_render_ticket_includes_grouped_section():
    """render_ticket 候选区须调用分组渲染（否则新排序不上卡=白做）。

    手造 tiers 直接喂 render_ticket——不依赖 build_three_tier 是否恰好选出腿
    （条件断言 `if legs:` 在关档轮会静默跳过，等于没测）。"""
    t = {"structure": "new", "seq": 9, "totalCost": 4, "cards": [],
         "tiers": {"base": {"cost": 0, "legs": [], "play": "had-3*4*5", "note": "关档"},
                   "upset": {"cost": 4, "play": "mix-2串1", "note": "",
                             "legs": _grouped_legs()}},
         "warnings": ["[upset] 2 腿假设未通过（pending/refuted），出票前须逐条验证"]}
    txt = bp.render_ticket(t)
    assert "候选腿" in txt, "卡面须含分组候选区"
    assert txt.index("4:0") < txt.index("1:0"), "卡面组内赔率降序"
    assert "无模型" in txt                                  # modelSupport 上卡
    assert "假设未通过" in txt                               # 卡级告警上卡
    assert "○待验证" in txt                                  # 图例说明
