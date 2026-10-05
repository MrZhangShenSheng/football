# engine/tests/test_strength_determinism.py
# -*- coding: utf-8 -*-
"""V5确定性疫苗：同输入跑两次产物逐字节一致 + 幂等重跑不改文件。开发者 sszhang"""
import json, pathlib, tempfile
from datetime import date
import pytest
import strength_loaders as sl
import strength_chain_run as scr

CTX_BUILDER = None   # 复用 test_strength_leak._ctx 的构造逻辑（此处内联最小版）

def _mini_env(p: pathlib.Path):
    (p / "league").mkdir()
    (p / "league" / "lgX_matches.json").write_text(
        '[{"date":"2026-09-01","home":"teamA","away":"teamB","hg":2,"ag":1}]', encoding="utf-8")
    c = p / "cache"; c.mkdir()
    (c / "lgX_dc.json").write_text('{"homeAdv":0.3,"rho":-0.05,"teams":{"teamA":{"attack":0.2,"defense":-0.1},"teamB":{"attack":0.0,"defense":0.0}}}', encoding="utf-8")
    (c / "elo_history_lgX_2526.json").write_text('{"hfa":65,"rows":[{"date":"2026-09-01","home":"teamA","away":"teamB","elo_home_pre":1520,"elo_away_pre":1480}]}', encoding="utf-8")
    (c / "odds_lgX_2526.json").write_text('{"matches":[{"date":"01/09/2026","home":"teamA","away":"teamB","hxg":"1.9","axg":"1.0"}]}', encoding="utf-8")
    # 合成夹具用 teamA/teamB 直作规范ID（identity 直通·裁定2）：aliases 传入防真实别名表误映射
    return sl.build_ctx(["lgX"], leagues_dir=p / "league", cache_dir=c,
                        aliases={"teamA": {"zh": "甲"}, "teamB": {"zh": "乙"}})

def _matches_feed(p: pathlib.Path):
    """当日预测场清单（体彩风格中文两队·已有 lgX 两队）。"""
    (p / "aliases.json").write_text(json.dumps(
        {"teamA": {"zh": "甲"}, "teamB": {"zh": "乙"}}, ensure_ascii=False), encoding="utf-8")
    return [{"matchId": "900001", "home": "甲", "away": "乙", "league": "lgX", "kickoff": "2026-09-10 21:00"}]

def test_v5_determinism_and_idempotent(tmp_path):
    ctx = _mini_env(tmp_path)
    out1 = tmp_path / "out1"; out2 = tmp_path / "out2"
    feed = _matches_feed(tmp_path)
    aliases_file = tmp_path / "aliases.json"
    scr.run_day("2026-09-10", ctx, out1, feed=feed, beta=0.05, now_iso="2026-10-05T12:00:00",
                aliases_file=aliases_file)
    scr.run_day("2026-09-10", ctx, out2, feed=feed, beta=0.05, now_iso="2026-10-05T12:00:00",
                aliases_file=aliases_file)
    for f in sorted(out1.rglob("*.json")):
        twin = out2 / f.relative_to(out1)
        assert twin.exists() and f.read_bytes() == twin.read_bytes(), f"非确定性: {f.name}"
    # 幂等：同目录重跑不改首版
    before = {f.name: f.read_bytes() for f in sorted(out1.rglob("*.json"))}
    scr.run_day("2026-09-10", ctx, out1, feed=feed, beta=0.05, now_iso="2026-10-05T12:00:00",
                aliases_file=aliases_file)
    after = {f.name: f.read_bytes() for f in sorted(out1.rglob("*.json"))}
    assert before == after

def test_run_day_steps_artifacts(tmp_path):
    ctx = _mini_env(tmp_path); feed = _matches_feed(tmp_path)
    out = tmp_path / "out"
    summary = scr.run_day("2026-09-10", ctx, out, feed=feed, beta=0.05,
                          now_iso="2026-10-05T12:00:00", aliases_file=tmp_path / "aliases.json")
    names = {f.name for f in out.rglob("*.json")}
    assert {"step1_states.json", "step3_compare.json", "step4_lambdas.json",
            "step5_matrix.json", "step6_picks.json"} <= names      # 每步落盘
    assert summary["matches"] >= 1 and summary["picks"] <= 2
