"""redteam 红队审查测试: pick方向解析 / R1-R4触发与不触发 / 双强反证警示 / 缺证据跳过. 开发者 sszhang"""
import redteam


def _m(**kw):
    base = {"code": "周一006", "league": "意甲(R3)", "match": "乌迪内斯 vs 拉齐奥",
            "pick": "HAD 主胜", "fused": [0.45, 0.28, 0.27]}
    base.update(kw)
    return base


def test_pick_direction():
    assert redteam.pick_direction("HAD 主胜") == "h"
    assert redteam.pick_direction("HHAD 客胜(+1)") == "a"
    assert redteam.pick_direction("HAD 平") == "d"
    assert redteam.pick_direction("CRS 2:0") == "h"
    assert redteam.pick_direction("CRS 1:1") == "d"
    assert redteam.pick_direction("CRS 0:2") == "a"
    assert redteam.pick_direction("TTG 3+") is None
    assert redteam.pick_direction("排除:胶着") is None


def test_r1_injury_reverse_triggers():
    out = redteam.assess(_m(), {"injuries_home": 4, "injuries_away": 1})
    r1 = next(e for e in out["evidences"] if e["type"] == "R1")
    assert r1["against_pick"] and r1["strength"] == "strong"
    assert "d=+3" in r1["detail"]


def test_r1_below_gap_not_against():
    out = redteam.assess(_m(), {"injuries_home": 2, "injuries_away": 1})
    r1 = next(e for e in out["evidences"] if e["type"] == "R1")
    assert not r1["against_pick"]


def test_r2_baseline_reverse():
    out = redteam.assess(_m(), {"form_home": "0胜3平7负", "form_away": "5胜1平4负"})
    r2 = next(e for e in out["evidences"] if e["type"] == "R2")
    assert r2["against_pick"] and "近10无胜" in r2["detail"]


def test_r3_odds_move_against_pick():
    out = redteam.assess(_m(), {"odds_prev": [1.80, 3.60, 4.20],
                                "odds_now": [2.05, 3.40, 3.60]})
    r3 = next(e for e in out["evidences"] if e["type"] == "R3")
    assert r3["against_pick"]


def test_two_strong_evidences_alert():
    out = redteam.assess(_m(), {"injuries_home": 4, "injuries_away": 1,
                                "form_home": "0胜3平7负"})
    assert out["alert"] and "2条强反证" in out["summary"]


def test_no_evidence_passes():
    out = redteam.assess(_m(), {"injuries_home": 1, "injuries_away": 1,
                                "form_home": "6胜2平2负", "form_away": "2胜3平5负"})
    assert not out["alert"]
    r5 = next(e for e in out["evidences"] if e["type"] == "R5")
    assert r5["detail"].startswith("wargame 未转正")


def test_r4_divergence_mid_strength():
    out = redteam.assess(_m(pick="HAD 客胜", fused=[0.20, 0.25, 0.55]),
                         {"market": [0.40, 0.30, 0.30]})
    r4 = next(e for e in out["evidences"] if e["type"] == "R4")
    assert r4["against_pick"] and r4["strength"] == "mid"   # 中强度不进强反证计数
    assert not out["alert"]
