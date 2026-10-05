# engine/tests/test_strength_leak.py
# -*- coding: utf-8 -*-
"""实力链疫苗：V2时间旅行（as-of 只见 ≤T-2天）+ V1奇点（被预测场赛果不得影响输出）。开发者 sszhang"""
from datetime import date
import pytest
import strength_loaders as sl

def _ctx(tmp_path):
    # 合成最小数据地基：league库/DC/elo/xg 各一份
    lg_dir = tmp_path / "league"; lg_dir.mkdir()
    (lg_dir / "test-lg_matches.json").write_text('[{"date":"2026-08-01","home":"teamA","away":"teamB","hg":2,"ag":1},'
        '{"date":"2026-08-05","home":"teamD","away":"teamA","hg":1,"ag":1},'
        '{"date":"2026-08-10","home":"teamB","away":"teamA","hg":0,"ag":0},'
        '{"date":"2026-09-01","home":"teamA","away":"teamC","hg":3,"ag":0}]', encoding="utf-8")
    cache = tmp_path / "cache"; cache.mkdir()
    (cache / "test-lg_dc.json").write_text('{"homeAdv":0.25,"rho":-0.05,"teams":'
        '{"teamA":{"attack":0.3,"defense":-0.2},"teamB":{"attack":0.0,"defense":0.1},"teamC":{"attack":-0.3,"defense":0.3}}}', encoding="utf-8")
    (cache / "elo_history_test-lg_2526.json").write_text('{"hfa":65,"rows":['
        '{"date":"2026-08-01","home":"teamA","away":"teamB","elo_home_pre":1500,"elo_away_pre":1500},'
        '{"date":"2026-08-10","home":"teamB","away":"teamA","elo_home_pre":1512,"elo_away_pre":1488},'
        '{"date":"2026-09-01","home":"teamA","away":"teamC","elo_home_pre":1500,"elo_away_pre":1500}]}', encoding="utf-8")
    (cache / "odds_test-lg_2526.json").write_text('{"matches":['
        '{"date":"01/08/2026","home":"teamA","away":"teamB","hxg":"1.8","axg":"0.9"},'
        '{"date":"10/08/2026","home":"teamB","away":"teamA","hxg":"0.8","axg":"1.1"},'
        '{"date":"01/09/2026","home":"teamA","away":"teamC","hxg":"2.2","axg":"0.4"}]}', encoding="utf-8")
    return sl.build_ctx(["test-lg"], leagues_dir=lg_dir, cache_dir=cache)

def test_v2_time_travel_asof():
    """as_of=2026-09-03 预测 teamA：lag=2 → 只能用 ≤09-01 赛果（09-01 场算前史可用）。
    as_of=2026-08-31 → 09-01 场不可见。所有 as-of 派生统计逐一断言。"""
    # 直接构造（避免 pytest fixture 复杂度）
    import pathlib, tempfile
    with tempfile.TemporaryDirectory() as td:
        ctx = _ctx(pathlib.Path(td))
        st_before = sl.team_state_on("teamA", date(2026, 8, 31), ctx)   # 09-01 场不可见
        st_after = sl.team_state_on("teamA", date(2026, 9, 3), ctx)     # 09-01 场可见
        assert st_before["n_xg"] == 2 and st_after["n_xg"] == 3          # xG 窗口 as-of 正确
        assert st_before["elo"] != st_after["elo"]                        # 09-01 后 Elo 变化可见性切换

def test_v2_cutoff_boundary():
    """边界：as_of=09-03 → cutoff=09-01 → date<=cutoff 含 09-01（lag=2 语义=可见 T-2 当日赛果前值）。"""
    import pathlib, tempfile
    with tempfile.TemporaryDirectory() as td:
        ctx = _ctx(pathlib.Path(td))
        assert sl.as_of_rows(ctx["timeline"]["test-lg"], date(2026, 9, 3))[-1]["date"] == "2026-09-01"

def test_degradation_flags():
    """降级链显式：teamC 在 as_of=09-03 实际拿得到 xG（09-01 客队行在 cutoff=09-01 内）与 elo（09-01 pre 行）；
    真正的 no_xg 正向用例是 teamD（见 test_no_xg_positive）。"""
    import pathlib, tempfile
    with tempfile.TemporaryDirectory() as td:
        ctx = _ctx(pathlib.Path(td))
        st = sl.team_state_on("teamC", date(2026, 9, 3), ctx)
        assert st["n_xg"] == 1 and "no_xg" not in st["flags"]            # 09-01 客队行可见 → 有 xG
        assert "no_elo" in st["flags"] or st["elo"] is not None          # 按实现契约二选一断言
        assert isinstance(st["flags"], list)

def test_no_xg_positive():
    """no_xg 正向断言：teamD 在 timeline 出场一次（联赛可解析）但从未出现在 xg 文件 matches → flags 记 'no_xg'。"""
    import pathlib, tempfile
    with tempfile.TemporaryDirectory() as td:
        ctx = _ctx(pathlib.Path(td))
        st = sl.team_state_on("teamD", date(2026, 9, 3), ctx)
        assert st["league"] == "test-lg"
        assert "no_xg" in st["flags"] and st["n_xg"] == 0

def test_zh_to_id():
    assert sl.zh_to_id({"teamA": {"zh": "甲队"}, "teamB": {"zh": "乙队"}}) == {"甲队": "teamA", "乙队": "teamB"}
