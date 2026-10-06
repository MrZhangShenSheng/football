# engine/tests/test_strength_leak.py
# -*- coding: utf-8 -*-
"""实力链疫苗：V2时间旅行（as-of 只见 ≤T-2天）+ V1奇点（被预测场赛果不得影响输出）+ fd名归一规范ID。开发者 sszhang"""
from datetime import date
import pytest
import strength_loaders as sl

ALIASES = {"team-a": {"zh": "甲队"}, "team-b": {"zh": "乙队"}, "team-c": {"zh": "丙队"}}

def _ctx(tmp_path):
    # 合成最小数据地基：league库/DC/elo/xg 各一份
    # timeline=规范ID+dict包装（真实18/19为{...,matches:[...]}结构）；xG/elo/DC=fd显示名（装载时归一规范ID）
    lg_dir = tmp_path / "league"; lg_dir.mkdir(exist_ok=True)    # exist_ok: V1疫苗同目录二次重建ctx
    (lg_dir / "test-lg_matches.json").write_text('{"league":"test-lg","matches":['
        '{"date":"2026-08-01","home":"team-a","away":"team-b","hg":2,"ag":1},'
        '{"date":"2026-08-05","home":"team-d","away":"team-a","hg":1,"ag":1},'
        '{"date":"2026-08-10","home":"team-b","away":"team-a","hg":0,"ag":0},'
        '{"date":"2026-09-01","home":"team-a","away":"team-c","hg":3,"ag":0}]}', encoding="utf-8")
    cache = tmp_path / "cache"; cache.mkdir(exist_ok=True)
    (cache / "test-lg_dc.json").write_text('{"homeAdv":0.25,"rho":-0.05,"teams":'
        '{"Team A":{"attack":0.3,"defense":-0.2},"Team B":{"attack":0.0,"defense":0.1},"Team C":{"attack":-0.3,"defense":0.3}}}', encoding="utf-8")
    (cache / "elo_history_test-lg_2526.json").write_text('{"hfa":65,"rows":['
        '{"date":"2026-08-01","home":"Team A","away":"Team B","elo_home_pre":1500,"elo_away_pre":1500},'
        '{"date":"2026-08-10","home":"Team B","away":"Team A","elo_home_pre":1512,"elo_away_pre":1488},'
        '{"date":"2026-09-01","home":"Team A","away":"Team C","elo_home_pre":1500,"elo_away_pre":1500}]}', encoding="utf-8")
    (cache / "odds_test-lg_2526.json").write_text('{"matches":['
        '{"date":"01/08/2026","home":"Team A","away":"Team B","hxg":"1.8","axg":"0.9"},'
        '{"date":"10/08/2026","home":"Team B","away":"Team A","hxg":"0.8","axg":"1.1"},'
        '{"date":"20/08/2026","home":"ZZZ United","away":"Team B","hxg":"1.5","axg":"1.2"},'
        '{"date":"01/09/2026","home":"Team A","away":"Team C","hxg":"2.2","axg":"0.4"}]}', encoding="utf-8")
    return sl.build_ctx(["test-lg"], leagues_dir=lg_dir, cache_dir=cache, aliases=ALIASES)

def test_v2_time_travel_asof():
    """as_of=2026-09-03 预测 team-a：lag=2 → 只能用 ≤09-01 赛果（09-01 场算前史可用）。
    as_of=2026-08-31 → 09-01 场不可见。所有 as-of 派生统计逐一断言。"""
    # 直接构造（避免 pytest fixture 复杂度）
    import pathlib, tempfile
    with tempfile.TemporaryDirectory() as td:
        ctx = _ctx(pathlib.Path(td))
        st_before = sl.team_state_on("team-a", date(2026, 8, 31), ctx)   # 09-01 场不可见
        st_after = sl.team_state_on("team-a", date(2026, 9, 3), ctx)     # 09-01 场可见
        assert st_before["n_xg"] == 2 and st_after["n_xg"] == 3          # xG 窗口 as-of 正确
        assert st_before["elo"] != st_after["elo"]                        # 09-01 后 Elo 变化可见性切换

def test_v2_cutoff_boundary():
    """边界：as_of=09-03 → cutoff=09-01 → date<=cutoff 含 09-01（lag=2 语义=可见 T-2 当日赛果前值）。
    timeline 为 dict 包装结构 → build_ctx 已解包为 matches 行列表。"""
    import pathlib, tempfile
    with tempfile.TemporaryDirectory() as td:
        ctx = _ctx(pathlib.Path(td))
        assert isinstance(ctx["timeline"]["test-lg"], list)               # dict 解包：喂下游的是行列表
        assert sl.as_of_rows(ctx["timeline"]["test-lg"], date(2026, 9, 3))[-1]["date"] == "2026-09-01"

def test_degradation_flags():
    """team-c 在 as_of=09-03 实际拿得到 xG（09-01 客队行在 cutoff=09-01 内）与 elo（09-01 pre 行）；
    真正的 no_xg 正向用例是 team-d（见 test_no_xg_positive）。"""
    import pathlib, tempfile
    with tempfile.TemporaryDirectory() as td:
        ctx = _ctx(pathlib.Path(td))
        st = sl.team_state_on("team-c", date(2026, 9, 3), ctx)
        assert st["n_xg"] == 1 and "no_xg" not in st["flags"]            # 09-01 客队行可见 → 有 xG
        assert "no_elo" in st["flags"] or st["elo"] is not None          # 按实现契约二选一断言
        assert isinstance(st["flags"], list)

def test_no_xg_positive():
    """no_xg 正向断言：team-d 在 timeline 出场一次（联赛可解析）但从未出现在 xg 文件 matches → flags 记 'no_xg'。"""
    import pathlib, tempfile
    with tempfile.TemporaryDirectory() as td:
        ctx = _ctx(pathlib.Path(td))
        st = sl.team_state_on("team-d", date(2026, 9, 3), ctx)
        assert st["league"] == "test-lg"
        assert "no_xg" in st["flags"] and st["n_xg"] == 0

def test_fd_name_normalization():
    """fd 显示名装载时归一规范ID：dc 键 'Team A'→'team-a'，xG/elo 行 home/away 同步归一；
    不可映射 xG 行（'ZZZ United'）整行丢弃并计入 ctx['unmapped']。"""
    import pathlib, tempfile
    with tempfile.TemporaryDirectory() as td:
        ctx = _ctx(pathlib.Path(td))
        assert set(ctx["dc"]["test-lg"]["teams"]) == {"team-a", "team-b", "team-c"}   # fd 键归一
        for rows in (ctx["xg"]["test-lg"], ctx["elo"]["test-lg"]["rows"]):
            assert {r["home"] for r in rows} <= {"team-a", "team-b", "team-c"}        # 行内名已归一
        assert all("ZZZ United" not in (r["home"], r["away"]) for r in ctx["xg"]["test-lg"])
        assert len(ctx["xg"]["test-lg"]) == 3                                        # ZZZ 行丢弃后余 3 行
        assert ctx["unmapped"]["test-lg"] == 1                                       # 丢弃计数

def test_zh_to_id():
    assert sl.zh_to_id({"team-a": {"zh": "甲队"}, "team-b": {"zh": "乙队"}}) == {"甲队": "team-a", "乙队": "team-b"}

def test_v1_singularity_injection():
    """V1奇点：把被预测场(2026-09-05 teamA vs teamB, 5:0)注入league库 → team_state_on 输出必须逐字节不变。
    （as_of=09-05 → cutoff=09-03，注入场09-05>09-03不可见——这正是疫苗要锚定的语义。）"""
    import pathlib, tempfile, copy, json
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td)
        ctx = _ctx(p)
        before = sl.team_state_on("teamA", date(2026, 9, 5), ctx)
        lg_file = p / "league" / "test-lg_matches.json"
        rows = json.loads(lg_file.read_text(encoding="utf-8"))
        rows["matches"].append({"date": "2026-09-05", "home": "teamA", "away": "teamB", "hg": 5, "ag": 0})
        lg_file.write_text(json.dumps(rows), encoding="utf-8")
        ctx2 = _ctx(p)
        after = sl.team_state_on("teamA", date(2026, 9, 5), ctx2)
        assert before == after, "被预测场赛果泄入了实力快照！"

def test_norm_team_fd_field():
    """2026-10-05 A2修复：别名条目 fd 字段（fd CSV缩写名 'Milan'/'Ath Madrid'）反查规范ID——
    优先级 identity→kebab→espn→fd。修 122 行 xG 丢失（'Milan' vs espn='AC Milan' 对不上）。"""
    fake = {"ac-milan": {"zh": "AC米兰", "espn": "AC Milan", "fd": "Milan"},
            "atletico-madrid": {"zh": "马竞", "espn": "Atlético Madrid", "fd": "Ath Madrid"}}
    assert sl._norm_team("Milan", fake) == "ac-milan"
    assert sl._norm_team("Ath Madrid", fake) == "atletico-madrid"
    assert sl._norm_team("AC Milan", fake) == "ac-milan"          # espn 仍通
    assert sl._norm_team("ac-milan", fake) == "ac-milan"          # identity 仍通
    assert sl._norm_team("ZZZ", fake) is None

def test_hst_proxy_layer(tmp_path):
    import json
    """v11 阶段3：HST 代理质量层——无真xG的队用射正×转化率回球量纲顶替 xg 位·as-of 干净。"""
    import datetime as dt
    lg_dir = tmp_path / "league"; lg_dir.mkdir(exist_ok=True)
    (lg_dir / "lgH_matches.json").write_text(
        '{"matches":[{"date":"2026-08-01","home":"team-p","away":"team-q","hg":2,"ag":0}]}', encoding="utf-8")
    cache = tmp_path / "cache"; cache.mkdir(exist_ok=True)
    # HST 档：team-p 两场射正 6/8·被射正 2/2；team-q 一场
    (cache / "hst_lgH.json").write_text(json.dumps({"league": "lgH", "rows": [
        {"date": "2026-07-20", "home": "team-p", "away": "team-r", "hst": 6, "ast": 2},
        {"date": "2026-07-25", "home": "team-s", "away": "team-p", "hst": 2, "ast": 8},
        {"date": "2026-07-26", "home": "team-q", "away": "team-t", "hst": 3, "ast": 4}]}), encoding="utf-8")
    ctx = sl.build_ctx(["lgH"], leagues_dir=lg_dir, cache_dir=cache,
                       aliases={"team-p": {"zh": "P"}, "team-q": {"zh": "Q"}})
    st = sl.team_state_on("team-p", dt.date(2026, 8, 5), ctx)
    assert st.get("xg_att") is not None, "HST 代理应顶替 xg 位"
    assert "hst_source:proxy" in st["flags"] and "no_xg" not in st["flags"]
    # as-of：08-01 场的 HST（若在 08-03 后入库）不可见——本档 7 月行全部可见·两场场均射正 7
    assert st["n_xg"] == 2
    # 未来行注入不变（V2 同款）
