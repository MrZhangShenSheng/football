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
    # 去重生效：总条数 = 唯一 (date, code) 数
    keys = {(r.get("date"), r.get("code")) for r in c["records"]}
    assert len(keys) == c["n_total"]


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
