# engine/tests/test_tail_dataset.py
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from tail_dataset import label_skeleton, market_anchor_features


def _full_pool(hi=8.0):
    pool = {f"{i}:{j}": hi for i in range(3) for j in range(3)}
    pool.update({"other_h": 40.0, "other_d": 40.0, "other_a": 40.0})
    pool.update({f"{i}:{j}": 30.0 for i in range(3, 6) for j in range(3)})
    pool.update({f"{i}:{j}": 30.0 for i in range(3) for j in range(3, 6)})
    return pool


def test_label_skeleton_axes():
    assert label_skeleton("2:1") == {"tg": 3, "tgBin": 3, "direction": 0, "cleanSheet": 2}
    assert label_skeleton("0:5") == {"tg": 5, "tgBin": 5, "direction": 2, "cleanSheet": 0}
    assert label_skeleton("3:0") == {"tg": 3, "tgBin": 3, "direction": 0, "cleanSheet": 1}
    assert label_skeleton("0:0") == {"tg": 0, "tgBin": 0, "direction": 1, "cleanSheet": 2}  # 0:0 归"无"
    assert label_skeleton("2:6")["tgBin"] == 7        # tg=8 截断到 7+


def test_market_anchor_dimensions_and_direction():
    row = {"crsOpen": _full_pool()}
    f = market_anchor_features(row)
    assert len(f) == 11
    assert 0.0 < f["mkt_p_home_open"] < 1.0
    assert f["mkt_p_tg_ge5_open"] > 0.0
    # 0:0 赔率调最低 → 三向里平局聚合应最大
    row2 = {"crsOpen": {**_full_pool(), "0:0": 1.5}}
    f2 = market_anchor_features(row2)
    assert f2["mkt_p_draw_open"] > f2["mkt_p_home_open"]


def test_market_anchor_insufficient_pool():
    assert market_anchor_features({"crsOpen": {"1:1": 7.0}}) == {}


# 追加到 engine/tests/test_tail_dataset.py
import json as _json
from tail_dataset import LeagueIndex, team_asof_features, LEAK_LAG_DAYS


def _mini_league(tmp_path):
    rows = []
    # arsenal 主队 3 场：10-01 大胜 4:0、10-08 大球 3:3、10-15 零封 2:0
    for d, (hg, ag) in [("2025-10-01", (4, 0)), ("2025-10-08", (3, 3)), ("2025-10-15", (2, 0))]:
        rows.append({"date": d, "home": "arsenal", "away": "chelsea", "hg": hg, "ag": ag})
    # chelsea 主场 2025-10-05 被 0:3
    rows.append({"date": "2025-10-05", "home": "chelsea", "away": "liverpool", "hg": 0, "ag": 3})
    p = tmp_path / "england-premier_matches.json"
    p.write_text(_json.dumps(rows), encoding="utf-8")
    return tmp_path


def test_window_respects_lag_and_sides(tmp_path):
    idx = LeagueIndex(_mini_league(tmp_path))
    # as_of=10-16，lag=2 → cutoff=10-14，arsenal 可见 10-01/10-08 两场
    ms = idx.window("arsenal", "2025-10-16", 10)
    assert [m["date"] for m in ms] == ["2025-10-01", "2025-10-08"]
    assert ms[0]["gf"] == 4 and ms[0]["isHome"]
    # chelsea 同 cutoff：10-01(客 0:4→gf0 ga4) + 10-05(主 0:3) + 10-08(客 3:3)
    mc = idx.window("chelsea", "2025-10-16", 10)
    assert len(mc) == 3 and mc[1]["gf"] == 0 and mc[1]["ga"] == 3


def test_team_asof_features_dims_and_null(tmp_path):
    idx = LeagueIndex(_mini_league(tmp_path))
    # 窗不足 MIN_WINDOW_N=5 → 统计 36 维全 None 但 38 键齐（rest 2 维与统计窗宽无关，照常有值）
    f = team_asof_features(idx, "arsenal", "chelsea", "2025-10-16")
    assert len(f) == 38
    assert all(v is None for k, v in f.items() if not k.startswith("rest"))
    assert f["rest_h"] == 8    # 10-16 距 cutoff 内最近一场 10-08 = 8 天
    # 造足 6 场后统计非空：arsenal 打了 3 场不够——用 liverpool 视角测正常路径
    rows = [{"date": f"2025-09-{i:02d}", "home": "arsenal", "away": "chelsea", "hg": 2, "ag": 1}
            for i in range(1, 7)]
    p2 = tmp_path / "spain-laliga_matches.json"
    p2.write_text(_json.dumps(rows + [{"date": "2025-09-07", "home": "x", "away": "arsenal", "hg": 0, "ag": 5}]), encoding="utf-8")
    idx = LeagueIndex(tmp_path)   # p2 后写须重建索引（__init__ 一次性装载，且不得早写以免污染上段窗）
    f2 = team_asof_features(idx, "chelsea", "arsenal", "2025-09-08")
    assert f2["h_gf_w10"] is not None and abs(f2["h_gf_w10"] - 1.0) < 1e-9   # chelsea 6 场全客负 1 球
    assert f2["a_gf_w10"] is not None                                       # arsenal 6+1 场
    assert f2["d_gf_w10"] == f2["h_gf_w10"] - f2["a_gf_w10"]
