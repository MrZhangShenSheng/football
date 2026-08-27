"""freq-band 比分选法测试（2026-08-27 重设计：联赛频率+球队平移+形状带+q排序）。开发者 sszhang"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import freq_band  # noqa: E402


def test_league_base_rates():
    """频率 Counter → (主队场均, 客队场均)：Σh·c/n 与 Σa·c/n。"""
    blob = Counter({"__n": 3, "1:0": 1, "0:0": 1, "2:1": 1})
    assert freq_band.league_base_rates(blob) == (1.0, 1 / 3)   # (1+0+2)/3, (0+0+1)/3
    assert freq_band.league_base_rates(Counter()) == (0.0, 0.0)


def test_global_pool():
    """全局池 = 各联赛 Counter 汇总（欧冠等无映射联赛的模板）。"""
    table = {"a": Counter({"__n": 2, "1:0": 1, "0:0": 1}),
             "b": Counter({"__n": 2, "1:0": 2})}
    pool = freq_band.global_pool(table)
    assert pool["__n"] == 4 and pool["1:0"] == 3 and pool["0:0"] == 1


def test_build_team_form_fd_and_local(tmp_path):
    """fd 行 + 本地 league 库 → norm 键近况表；离网注入隔离（phase1 铁律）。"""
    fd_rows = [{"HomeTeam": "Arsenal", "AwayTeam": "Man City", "FTHG": "2", "FTAG": "1"},
               {"HomeTeam": "Man City", "AwayTeam": "Arsenal", "FTHG": "3", "FTAG": "0"}]
    lg = tmp_path / "korea_matches.json"
    lg.write_text('{"matches": [{"home": "ulsan", "away": "pohang-steelers", "hg": 2, "ag": 0}]}',
                  encoding="utf-8")
    form = freq_band.build_team_form(fetch_rows_fn=lambda s, d: fd_rows if (d == "E0" and s == "2425") else [],
                                     league_glob=str(tmp_path / "*_matches.json"))
    assert form["arsenal"] == [(2, 1), (0, 3)]        # (进球, 失球) 源内时序
    assert form["mancity"] == [(1, 2), (3, 0)]        # norm("Man City") == norm("man-city")
    assert form["ulsan"] == [(2, 0)] and form["pohangsteelers"] == [(0, 2)]


def test_team_strength_window_and_gate():
    """近 RECENT_WINDOW 场均值；<FORM_MIN_MATCHES → None（降级不平移）。"""
    form = {"x": [(1, 0)] * (freq_band.RECENT_WINDOW - 1) + [(3, 1)] * 2}
    gf, ga = freq_band.team_strength(form, "x")      # 12 场取近 10：8×(1,0)+2×(3,1)
    assert abs(gf - (8 + 6) / 10) < 1e-9 and abs(ga - 2 / 10) < 1e-9
    assert freq_band.team_strength({"y": [(1, 1)] * 4}, "y") is None
    assert freq_band.team_strength({}, "none") is None


def test_lambdas_multiplicative_and_clamp():
    """λh=主进×客失/模板主场场均；λa 对称；缺强度/零基准→None；clamp 生效。"""
    base = (1.5, 1.0)
    assert freq_band.lambdas(base, (2.0, 0.8), (0.9, 1.5)) == (2.0 * 1.5 / 1.5, 0.9 * 0.8 / 1.0)
    assert freq_band.lambdas(base, None, (0.9, 1.5)) is None      # 近况缺失降级
    assert freq_band.lambdas((0.0, 1.0), (2.0, 1.0), (1.0, 1.0)) is None  # 空基准
    assert freq_band.lambdas(base, (9.0, 0.1), (0.2, 9.0))[0] <= freq_band.LAMBDA_CLAMP[1]
    assert freq_band.lambdas(base, (0.05, 0.1), (0.05, 0.1)) == (freq_band.LAMBDA_CLAMP[0],) * 2


def _mini_blob():
    return Counter({"__n": 100, "1:1": 30, "1:0": 25, "0:0": 20, "2:0": 15, "0:1": 10})


def test_shifted_q_pure_mode():
    """lam=None → 纯联赛频率归一化（零破坏降级）；c=0 比分恒 0；空 Counter → 空。"""
    q = freq_band.shifted_q(_mini_blob(), None)
    assert abs(sum(q.values()) - 1.0) < 1e-9
    assert abs(q["1:1"] - 0.30) < 1e-9
    assert "3:3" not in q                                  # c=0 格子不出现（真实数据从未出现）
    assert freq_band.shifted_q(Counter(), None) == {}


def test_shifted_q_direction_and_conservation():
    """强主平移(λh 2.4/λa 0.5)：靠近目标的比分 q 抬升、远离的回落；归一化守恒。"""
    pure = freq_band.shifted_q(_mini_blob(), None)
    shifted = freq_band.shifted_q(_mini_blob(), (2.4, 0.5))   # T=2.9, D=1.9
    assert shifted["2:0"] > pure["2:0"]          # (t=2,d=2) 靠近目标
    assert shifted["0:1"] < pure["0:1"]          # (t=1,d=-1) 远离目标
    assert abs(sum(shifted.values()) - 1.0) < 1e-9
