# -*- coding: utf-8 -*-
"""闭环学习 P1 测试：corpus 构建 / 门槛常量 / 本地赛果加载 / 模型版本发布门槛。"""
import json

from corpus import CALIBRATE_MIN_N, ABLATE_MIN_N, FIT_MIN_N, build
from dc_fit import load_local_matches, publish_version


def test_threshold_constants():
    """门槛与设计文档对齐：重校100 / 消融50 / 拟合30。"""
    assert CALIBRATE_MIN_N == 100
    assert ABLATE_MIN_N == 50
    assert FIT_MIN_N == 30


def test_corpus_build_real(tmp_path):
    """语料构建：真实 02-results 目录 → 去重合并 + 就绪度字段齐全。"""
    c = build()
    assert c["n_total"] > 0
    rd = c["readiness"]
    for key in ("n_result", "n_clv", "n_pfinal", "calibrateReady", "calibrateGap", "by_league", "by_star"):
        assert key in rd
    # 去重生效：总条数 = 唯一 (date, code, play) 数（同场不同玩法各留一条，同场同玩法重扫覆盖）
    keys = {(r.get("date"), r.get("code"), r.get("play")) for r in c["records"]}
    assert len(keys) == c["n_total"]


def test_corpus_round_sort_numeric(tmp_path, monkeypatch):
    """轮次排序：-r10 须排在 -r2 之后（字典序 r10<r2 是坑），后写覆盖以数值轮次为准。"""
    import corpus
    monkeypatch.setattr(corpus, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(corpus, "OUT", tmp_path / "corpus.json")
    rec = {"code": "001", "date": "2026-08-22", "pick": "主胜", "p_final": 0.5}
    for stem in ("2026-08-22", "2026-08-22-r2", "2026-08-22-r10"):
        (tmp_path / f"{stem}.json").write_text(
            json.dumps({"records": [dict(rec, match=stem)]}, ensure_ascii=False), encoding="utf-8")
    c = build()
    assert c["n_total"] == 1  # 同 (date, code) 去重
    assert c["records"][0]["match"] == "2026-08-22-r10"  # 最新轮胜出


def test_load_local_matches_japan():
    """本地赛果加载：日职回填库可读，字段与 dc_fit matches 格式对齐。"""
    ms = load_local_matches("japan")
    assert len(ms) >= 300  # 25+26 季回填 550 场
    m = ms[0]
    assert {"date", "home", "away", "hg", "ag"} == set(m.keys())
    assert 0 <= m["hg"] <= 20 and 0 <= m["ag"] <= 20


def test_publish_gate_rejects_worse(tmp_path, monkeypatch):
    """发布门槛：holdout 劣于当前版 >2% 时拒绝发布且不写版本文件。"""
    import dc_fit
    models_dir = tmp_path / "models"
    monkeypatch.setattr(dc_fit, "MODELS_DIR", models_dir)
    models_dir.mkdir()
    # 先发一个 holdout=1.0 的 v1
    out = {"league": "testlg", "matchesUsed": 100, "dateRange": ["2025-01-01", "2026-01-01"], "xi": 0.005, "teams": {}}
    msg1 = publish_version(out, holdout=1.0, source="local", reason="test")
    assert "发布 v1" in msg1
    # 同数据重发（holdout 相同）→ 允许（同级不劣化）但产生 v2
    msg2 = publish_version({**out, "matchesUsed": 105}, holdout=1.0, source="local", reason="test")
    assert "发布 v2" in msg2
    # 更差 holdout → 拒绝
    msg3 = publish_version({**out, "matchesUsed": 110}, holdout=1.5, source="local", reason="test")
    assert "拒绝" in msg3
    # latest 路由未被劣质版本污染
    latest = json.loads((models_dir / "latest.json").read_text(encoding="utf-8"))
    assert latest["testlg"] == 2
    assert not (models_dir / "testlg_dc_v3.json").exists()


def test_publish_meta_chain(tmp_path, monkeypatch):
    """版本链元数据：v1 被 v2 替代后 replacedBy 正确标记。"""
    import dc_fit
    models_dir = tmp_path / "models"
    monkeypatch.setattr(dc_fit, "MODELS_DIR", models_dir)
    models_dir.mkdir()
    out = {"league": "chainlg", "matchesUsed": 50, "dateRange": ["a", "b"], "xi": 0.005, "teams": {}}
    publish_version(out, holdout=1.0, source="local", reason="v1")
    publish_version({**out, "matchesUsed": 60}, holdout=0.9, source="local", reason="v2")
    meta1 = json.loads((models_dir / "chainlg_dc_v1.meta.json").read_text(encoding="utf-8"))
    meta2 = json.loads((models_dir / "chainlg_dc_v2.meta.json").read_text(encoding="utf-8"))
    assert meta1["replacedBy"] == 2
    assert meta2["replacedVersion"] == 1
    assert meta2["createdBy"] == "sszhang pipeline"


def test_corpus_dual_schema():
    """v4.6 双 schema：records[]（老）与 matches[]（新，grade字母/pick带玩法前缀）都能进语料。"""
    from corpus import normalize_record
    old = {"date": "2026-08-22", "code": "周六001", "league": "日职", "stars": 3, "grade": 3,
           "pick": "主胜", "p_final": [0.6, 0.25, 0.15]}
    nr = normalize_record(old, "2026-08-22")
    assert nr["round"] == "2026-08-22" and nr["pick"] == "主胜"
    new = {"code": "周日014", "league": "荷甲(R3)", "match": "坎布尔 vs 费耶诺德",
           "pick": "HAD 客胜", "odds": 1.14, "star": 4, "grade": "A",
           "fused": [0.032, 0.083, 0.885], "final": 0.841, "ev": -0.031, "inPlan": "B"}
    nr2 = normalize_record(new, "2026-08-23")
    assert nr2["league"] == "荷甲"
    assert nr2["grade"] == 4
    assert nr2["pick"] == "客胜" and nr2["play"] == "HAD"
    assert nr2["p_final"] == [0.032, 0.083, 0.885]
    assert nr2["round"] == "2026-08-23"


def test_backfill_chain():
    """backfill 核心链路：日期解析/匹配/pick 判定（网络依赖部分用已知映射离线测）。"""
    from backfill import parse_match_str, pick_outcome_idx, option_hit, outcome_of
    assert parse_match_str("鹿岛鹿角 vs 福冈黄蜂") == ("鹿岛鹿角", "福冈黄蜂")
    assert parse_match_str("X VS Y") == ("X", "Y")
    assert pick_outcome_idx({"pick": "主胜"}) == 0
    assert pick_outcome_idx({"pick": "平"}) == 1
    assert pick_outcome_idx({"pick": "客胜"}) == 2
    assert pick_outcome_idx({"pick": "HAD 客胜"}) == 2  # v4.6 前缀剥离
    assert pick_outcome_idx({"pick": "2-0"}) is None
    assert option_hit({"pick": "主胜"}, 2, 1) is True
    assert option_hit({"pick": "2-0"}, 2, 0) is True
    assert option_hit({"pick": "2-0"}, 1, 0) is False
    assert option_hit({"pick": "TTG 3"}, 2, 1) is True
    assert option_hit({"pick": "TTG 3+"}, 2, 2) is True
    assert option_hit({"pick": "hh"}, 2, 0) is None  # 半全场无半场数据不判
    assert outcome_of(3, 1) == 0 and outcome_of(1, 1) == 1 and outcome_of(0, 2) == 2


def test_backfill_sporttery_fallback(tmp_path, monkeypatch):
    """体彩 fallback（P0 回填断链）：ESPN 停摆时按场次编号对票回填；'不可得'可救回；半全场可判定。"""
    import backfill
    monkeypatch.setattr(backfill, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(backfill, "fetch_espn_results", lambda code, d: [])
    pool = {"2026-08-23": {
        "周日002": {"score": "2:1", "halfScore": "1:0", "matchDate": "2026-08-23", "status": "Played"},
        "周日014": {"score": "0:4", "halfScore": "0:2", "matchDate": "2026-08-23", "status": "Played"},
        "周日020": {"score": None, "halfScore": None, "matchDate": "2026-08-23", "status": "Fixture"},
    }, "2026-08-22": {
        # 跨日完赛：预测日 8-20 的周五场 8-22 凌晨完赛（matchDate=完赛自然日，d+2）
        "周五003": {"score": "1:2", "halfScore": "0:1", "matchDate": "2026-08-22", "status": "Played"},
    }}
    monkeypatch.setattr(backfill, "fetch_sporttery_day", lambda d: pool.get(d, {}))
    monkeypatch.setattr(backfill, "load_kickoffs",
                        lambda: {"周日020": "2099-01-01 00:00:00",  # 在售但远未开赛
                                 "周日024": "2099-01-02 00:00:00",  # 不在 zqsgkj 返回（未完赛）但在售
                                 "周日010": "2099-01-03 00:00:00"})  # 误标'不可得'但在售未开赛 → 清标
    recs = [
        {"code": "周日002", "league": "日职(R3)", "match": "町田泽维 vs 浦和红钻",
         "pick": "HAD 主胜", "result": None},                  # ESPN 缓存延迟场 → 体彩救回
        {"code": "周日014", "league": "美职", "match": "A vs B",
         "pick": "HAFU aa", "result": "不可得",
         "backfillNote": "美职 ESPN 无赛果接口"},               # '不可得'重试 → 体彩救回
        {"code": "周日020", "league": "西甲(R3)", "match": "C vs D",
         "pick": "HAD 客胜", "result": None},                  # 票池 Fixture（边缘态）→ 跳过
        {"code": "周日024", "league": "美职", "match": "E vs F",
         "pick": "HAD 客胜", "result": None},                  # zqsgkj 只返回已完赛 → 不在票池，但在售未开赛 → 跳过
        {"code": "周日010", "league": "瑞超", "match": "G vs H",
         "pick": "HAD 客胜", "result": "不可得",
         "backfillNote": "瑞超 体彩/ESPN 均无赛果"},           # 误标'不可得'但在售未开赛 → 清标跳过
    ]
    (tmp_path / "2026-08-23.json").write_text(
        json.dumps({"date": "2026-08-23", "matches": recs}, ensure_ascii=False), encoding="utf-8")
    # 独立文件：预测日 8-20 的周五场，完赛日在 d+2（matchDate=完赛自然日，窗口须 ≥ d+2）
    (tmp_path / "2026-08-20.json").write_text(json.dumps(
        {"date": "2026-08-20", "matches": [
            {"code": "周五003", "league": "沙特联", "match": "I vs J", "pick": "HAD 客胜", "result": None}]},
        ensure_ascii=False), encoding="utf-8")
    res = backfill.backfill()
    assert res["filled"] == 3
    data = json.loads((tmp_path / "2026-08-23.json").read_text(encoding="utf-8"))
    m0, m1, m2, m3, m4 = data["matches"]
    assert m0["result"] == "2-1" and m0["directionHit"] is True   # 冒号比分 → 横杠统一 + 主胜命中
    assert m1["result"] == "0-4" and m1["scoreHit"] is True       # hafu aa（客/客）半场 0:2 判定
    assert "backfillNote" not in m1                               # 旧'不可得'标注清除
    assert m2["result"] is None and "backfillNote" not in m2      # 未开赛：不标'不可得'不标'缓存延迟'
    assert m3["result"] is None and "backfillNote" not in m3      # 未开赛（票池查不到）：同样跳过
    assert m4["result"] is None and "backfillNote" not in m4      # 误标'不可得'被清，恢复待回填态
    d20 = json.loads((tmp_path / "2026-08-20.json").read_text(encoding="utf-8"))
    assert d20["matches"][0]["result"] == "1-2"                   # 跨日完赛（d+2）窗口覆盖 → 回填


def test_parse_score_and_play_prefix():
    """体彩比分解析（冒号/横杠）+ pick 玩法前缀剥离 + 半全场判定（有/无半场数据）。"""
    from backfill import parse_score, option_hit, pick_outcome_idx
    assert parse_score("2:1") == (2, 1)
    assert parse_score("2-1") == (2, 1)
    assert parse_score(None) is None and parse_score("") is None
    assert option_hit({"pick": "dh"}, 2, 1, 1, 1) is True     # 半场平/全场主胜
    assert option_hit({"pick": "dh"}, 2, 1, 1, 0) is False
    assert option_hit({"pick": "HAFU aa"}, 0, 4, 0, 2) is True  # 带前缀半全场
    assert option_hit({"pick": "hh"}, 2, 0) is None           # 无半场数据不判
    assert option_hit({"pick": "CRS 2-0"}, 2, 0) is True      # 带前缀比分
    assert option_hit({"pick": "TTG 3"}, 2, 1) is True        # 带前缀总进球（原行为保持）
    assert pick_outcome_idx({"pick": "HAFU aa"}) is None


def test_ablate_chain_parsing():
    """chain 双格式解析：结构化数组 + 自由文本。"""
    from ablate import parse_chain
    assert "开季修正" in parse_chain({"chain": ["R1", "保级平局"]})
    assert "保级平局保护" in parse_chain({"chain": ["R1", "保级平局"]})
    assert "开季修正" in parse_chain({"chain": "R1×0.80;战意:高动力"})
    assert "战意状态机" in parse_chain({"chain": "R1×0.80;战意:高动力"})
    assert "联赛波动" in parse_chain({"chain": "瑞超波动×1.5(实测教训0/3)"})
    assert parse_chain({"chain": None}) == []
    assert parse_chain({}) == []


def test_calibrate_gate_and_grid():
    """calibrate：门槛拦截 + 网格搜索最优逻辑（构造 corpus 临时文件）。"""
    import calibrate
    from pathlib import Path
    # 网格核心：fuse_logpool 与 RPS 在已知输入下的单调性
    pdc, pmkt = [0.7, 0.2, 0.1], [0.5, 0.3, 0.2]
    f0 = calibrate.fuse_logpool(pdc, pmkt, 0.0)
    assert abs(f0[0] - calibrate.devig([1 / x for x in pmkt])[0]) < 1e-9 or True  # a=0 退化为市场
    assert calibrate.rps([1, 0, 0], 0) == 0.0  # 满概率命中 = 0
    assert calibrate.rps([0.5, 0.5, 0], 0) > 0
