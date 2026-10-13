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


# 追加（Task 4）
from tail_dataset import (build_hst_index, build_elo_index, EloIndex,
                          ctx_features, TAIL_LEAGUE_MAP)


def test_hst_index_reads_shots(tmp_path):
    (tmp_path / "hst_england-premier.json").write_text(_json.dumps(
        {"rows": [{"date": "2025-10-01", "home": "arsenal", "away": "chelsea", "hst": 8, "ast": 1}]}),
        encoding="utf-8")
    idx = build_hst_index(tmp_path)
    ms = idx.window("arsenal", "2025-10-05", 5)
    assert ms and ms[0]["gf"] == 8 and ms[0]["ga"] == 1   # gf=射正


def test_elo_index_pre_match_value(tmp_path):
    (tmp_path / "elo_history_england-premier_GLOBAL.json").write_text(_json.dumps(
        {"rows": [{"date": "2025-10-01", "home": "arsenal", "away": "chelsea",
                   "elo_home_pre": 1600.0, "elo_away_pre": 1500.0}]}), encoding="utf-8")
    ei = build_elo_index(tmp_path)
    assert ei.elo("arsenal", "2025-10-01") == 1600.0     # 当日赛前值可见（lag=0）
    assert ei.elo("arsenal", "2025-09-30") is None       # 无更早值 → None
    assert ei.elo("chelsea", "2025-10-03") == 1500.0


def test_ctx_features_template_and_month(tmp_path):
    _mini_league(tmp_path)                                # arsenal/chelsea 4 场小库
    idx = LeagueIndex(tmp_path)
    f = ctx_features(idx, "英超", "2025-10-16")
    assert f["lg_p_tg4"] is not None and 0.0 <= f["lg_p_tg4"] <= 1.0
    assert f["lg_p_blow"] is not None
    assert -1.0 <= f["ctx_month_sin"] <= 1.0
    # 未映射联赛（如澳超）→ 模板 None 但月份特征仍在
    f2 = ctx_features(idx, "澳超", "2025-10-16")
    assert f2["lg_p_tg4"] is None and f2["ctx_month_sin"] is not None
    assert "英超" in TAIL_LEAGUE_MAP


# 追加（Task 5）
from tail_dataset import build_rows, write_dataset

_REAL_LEAGUE_DIR = pathlib.Path(__file__).resolve().parents[2] / "data" / "02-results" / "league"


def _mini_hist(tmp_path):
    pool = {f"{i}:{j}": 8.0 for i in range(3) for j in range(3)}
    pool.update({"other_h": 40.0, "other_d": 40.0, "other_a": 40.0,
                 **{f"{i}:{j}": 30.0 for i in range(3, 6) for j in range(6)},
                 **{f"{i}:{j}": 30.0 for i in range(6) for j in range(3)}})
    matches = [
        {"date": "2025-10-16", "code": "四001", "league": "英超", "home": "阿森纳",
         "away": "切尔西", "score": "2:1", "had": {"h": 1.5, "d": 4.0, "a": 5.0},
         "ttg": {str(i): 3.0 for i in range(8)}, "crs": dict(pool), "crsOpen": dict(pool)},
        {"date": "2025-10-17", "code": "四002", "league": "英超", "home": "利物浦",
         "away": "曼城", "score": "0:5", "had": {"h": 2.2, "d": 3.2, "a": 2.5},
         "ttg": {str(i): 3.0 for i in range(8)}, "crs": dict(pool), "crsOpen": dict(pool)},
    ]
    p = tmp_path / "crs_hist_mini.json"
    p.write_text(_json.dumps({"matches": matches}), encoding="utf-8")
    return tmp_path


def test_build_rows_end_to_end(tmp_path):
    hist_dir = _mini_hist(tmp_path)
    lg_dir = _mini_league(tmp_path)                       # arsenal/chelsea 小库
    rows, meta = build_rows("2025-10-01", "2025-10-31",
                            hist_dir=hist_dir, league_dir=lg_dir, cache_dir=tmp_path)
    assert len(rows) == 2
    r0 = rows[0]
    assert r0["homeId"] == "arsenal" and r0["label"] == {"tg": 3, "tgBin": 3, "direction": 0, "cleanSheet": 2}
    assert r0["crs"] and r0["ttg"]                              # 对拍快照顶层挂载
    assert r0["features"]["mkt_p_tg_ge5_open"] > 0
    assert r0["features"]["h_gf_w10"] is None             # 窗不足 MIN_WINDOW_N
    assert r0["nNullTeam"] > 0                            # 缺失计数入特征
    assert 0.0 <= meta["mappedRate"] <= 1.0
    # 真实库 smoke（review 移交·matches 键 dict 库非空读——修雷断言）
    assert LeagueIndex(_REAL_LEAGUE_DIR).window("arsenal", "2026-10-11", 5)


def test_write_dataset_fingerprint_stable(tmp_path):
    hist_dir = _mini_hist(tmp_path)
    lg_dir = _mini_league(tmp_path)
    rows, _ = build_rows("2025-10-01", "2025-10-31",
                         hist_dir=hist_dir, league_dir=lg_dir, cache_dir=tmp_path)
    meta1 = write_dataset(rows, tmp_path / "out1")
    meta2 = write_dataset(rows, tmp_path / "out2")
    assert meta1["fingerprint"] == meta2["fingerprint"]
    assert (tmp_path / "out1" / "dataset.jsonl").exists()
    assert meta1["featureNames"][0] == "mkt_e_tg_open"    # 键序冻结·市场锚打头
    lines = (tmp_path / "out1" / "dataset.jsonl").read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
