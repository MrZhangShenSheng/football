# -*- coding: utf-8 -*-
"""recalibrate 校准曲线测试：分箱/幂等门槛/conf_tiers 冷启动（轨道C·T5）。"""
import json
from pathlib import Path
import recalibrate as rc

def test_bins_from_corpus(tmp_path, monkeypatch):
    recs = [
        {"p_final": [0.7, 0.2, 0.1], "directionHit": True,  "pick": "主胜"},
        {"p_final": [0.7, 0.2, 0.1], "directionHit": False, "pick": "主胜"},
        {"p_final": [0.7, 0.2, 0.1], "directionHit": True,  "pick": "主胜"},
        {"p_final": [0.7, 0.2, 0.1], "directionHit": True,  "pick": "主胜"},
    ]
    bins = rc.build_bins(recs)
    assert bins[0]["n"] == 4 and abs(bins[0]["actual"] - 0.75) < 0.01

def test_skip_when_small_delta(tmp_path, monkeypatch):
    # 幂等护栏: 语料增量<10场跳过(设计§六)
    assert rc.should_skip(prev_n=400, cur_n=405) is True
    assert rc.should_skip(prev_n=400, cur_n=415) is False

def test_conf_tiers_coldstart(tmp_path, monkeypatch):
    # 冷启动(兼容面⑤): conf_tiers.json缺失→内置默认值落盘
    monkeypatch.setattr(rc, "CACHE", tmp_path)
    tiers = rc.load_tiers()
    assert tiers == {"dan": 0.75, "std": 0.60, "obs": 0.55}
    assert (tmp_path / "conf_tiers.json").exists()
