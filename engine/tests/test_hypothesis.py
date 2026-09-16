"""假设层测试（spec §二）。开发者 sszhang"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from hypothesis import (make_hypothesis, validate_hypothesis, is_buyable,
                        filter_buyable, check_shared_legs)


def _h2h_check(gapless=True, matches=4):
    return {"kind": "h2h", "matches": matches, "seasonComplete": gapless,
            "gaps": [] if gapless else ["E1 2526 缺 2026-01-05 之后"],
            "finding": "米堡主场两次仅进1球"}


def test_refuted_leg_not_buyable():
    """verdict:refuted 的腿不进候选——米堡2:1 反面案例(spec §二)。"""
    h = make_hypothesis("米堡主场场均进3球所以能打出2:1",
                        checks=[_h2h_check()], verdict="refuted")
    assert not is_buyable(h)


def test_pending_leg_not_buyable():
    """verdict 未填写标 pending，同样不得出票。"""
    h = make_hypothesis("奥萨苏纳客场零封")
    assert h["verdict"] == "pending"
    assert not is_buyable(h)


def test_survived_leg_buyable():
    h = make_hypothesis("奥萨苏纳客场零封", checks=[_h2h_check()], verdict="survived")
    assert validate_hypothesis(h) == []
    assert is_buyable(h)


def test_missing_h2h_check_reports_problem():
    """checks[] 缺 H2H 完整度记录时报错(2026-09-15 残缺缓存事故)。"""
    h = make_hypothesis("奥萨苏纳客场零封",
                        checks=[{"kind": "form", "finding": "近5场2胜"}],
                        verdict="survived")
    probs = validate_hypothesis(h)
    assert any("h2h" in p for p in probs)
    assert not is_buyable(h)


def test_h2h_check_must_record_completeness():
    """H2H 项须同时记录场次数、赛季完整度、缺口区间。"""
    h = make_hypothesis("奥萨苏纳客场零封",
                        checks=[{"kind": "h2h", "matches": 4}],   # 缺 seasonComplete/gaps
                        verdict="survived")
    probs = validate_hypothesis(h)
    assert any("seasonComplete" in p or "gaps" in p for p in probs)


def test_invalid_verdict_rejected():
    h = make_hypothesis("测试", checks=[_h2h_check()], verdict="maybe")
    probs = validate_hypothesis(h)
    assert any("verdict" in p for p in probs)


def test_filter_buyable_splits_legs():
    ok = {"pick": "4:0", "hypothesis": make_hypothesis(
        "阿布艾因主场强于远征的利雅胜利", checks=[_h2h_check()], verdict="survived")}
    bad = {"pick": "2:1", "hypothesis": make_hypothesis(
        "米堡主场场均3球", checks=[_h2h_check()], verdict="refuted")}
    buyable, blocked = filter_buyable([ok, bad])
    assert [l["pick"] for l in buyable] == ["4:0"]
    assert [l["pick"] for l in blocked] == ["2:1"]


def test_shared_leg_across_bets_warns():
    """共用腿跨注复用告警——T033 三注共用米堡2:1=伪分散(spec §二)。"""
    bets = [{"legs": ["周三006 马竞4:0", "周二010 米堡2:1"]},
            {"legs": ["周三006 马竞3:0", "周二010 米堡2:1"]},
            {"legs": ["周三006 马竞2:0", "周二010 米堡2:1"]}]
    warns = check_shared_legs(bets)
    assert any("米堡2:1" in w and "3" in w for w in warns)


def test_independent_bets_no_warning():
    bets = [{"legs": ["A", "B"]}, {"legs": ["C", "D"]}]
    assert check_shared_legs(bets) == []
