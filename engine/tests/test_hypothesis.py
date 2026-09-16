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


# ---------- 终审修正（2026-09-16）：铁律9 约束下移 + 假设优先排序 ----------


def _leg(code, pick, odds, verdict="pending"):
    return {"matchNumStr": code, "match": "A-B", "play": "crs", "pick": pick,
            "odds": odds, "hypothesis": make_hypothesis("x", verdict=verdict)}


def test_dedup_same_match_keeps_one_per_bet():
    """铁律9 在组注层执行：一注内同场至多 1 腿。

    候选池不再预筛（终审实证：预筛取最高赔 → 19 场清一色 0:5@1000，撤上限白撤），
    约束下移到成注时。同场多腿分放不同注是合法分散，此处只禁同注冲突。"""
    from hypothesis import dedup_same_match
    legs = [_leg("004", "4:0", 50.0), _leg("004", "3:0", 60.0), _leg("007", "2:1", 7.0)]
    kept = dedup_same_match(legs)
    codes = [l["matchNumStr"] for l in kept]
    assert len(codes) == len(set(codes)), "一注内不得含同场两腿"
    assert "007" in codes
    assert kept[0]["pick"] == "3:0", "同场保留赔率最高者（组注层内部裁决）"


def test_dedup_preserves_all_matches():
    """去重只压同场冲突，不得丢场次——3 场应留 3 腿。"""
    from hypothesis import dedup_same_match
    legs = [_leg("001", "1:0", 6.0), _leg("001", "4:0", 50.0),
            _leg("002", "2:1", 7.0), _leg("003", "0:2", 12.0)]
    kept = dedup_same_match(legs)
    assert sorted(l["matchNumStr"] for l in kept) == ["001", "002", "003"]


def test_sort_survived_before_pending():
    """假设优先排序（大哥 2026-09-16 选项 C）：做过功课的腿浮顶，没做的沉底。

    同 verdict 内才按赔率降序。这样卡面顶部不再是 0:5@1000 这类最荒谬比分。"""
    from hypothesis import sort_by_hypothesis
    legs = [_leg("001", "0:5", 1000.0, "pending"),
            _leg("002", "2:1", 7.0, "survived"),
            _leg("003", "3:3", 60.0, "refuted"),
            _leg("004", "4:0", 50.0, "survived")]
    order = [l["pick"] for l in sort_by_hypothesis(legs)]
    assert order[0] == "4:0" and order[1] == "2:1", "survived 段内赔率降序且居首"
    assert order[-1] == "3:3", "refuted 沉底"
    assert order.index("0:5") > order.index("2:1"), "pending 排在 survived 之后"
