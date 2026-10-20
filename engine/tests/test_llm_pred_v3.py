# -*- coding: utf-8 -*-
"""llm_pred v3.6 结构化生成测试（批一·设计 docs/plans/2026-10-20-llm-dist-structured-design.html）。

口径：market 参照=去水 pick 键分布（须含 other_* 三格·与 llm_eval 计分键集同源）。
开发者 sszhang"""
import itertools
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from llm_pred import dir_of_key, generate_dist, tot_of_key           # noqa: E402

# 近似 31 格市场形状（和≈0.94，带内比例是真参照——生成器按带归一故不要求和为 1）
MKT = {"1:0": 0.10, "2:0": 0.07, "2:1": 0.09, "1:1": 0.15, "0:0": 0.07,
       "0:1": 0.10, "1:2": 0.09, "0:2": 0.06, "0:3": 0.029, "1:3": 0.02,
       "other_h": 0.05, "other_d": 0.04, "other_a": 0.07}


def test_dir_and_tot_of_key():
    assert dir_of_key("3:1") == "h" and dir_of_key("2:2") == "d" and dir_of_key("0:3") == "a"
    assert dir_of_key("other_h") == "h" and dir_of_key("other_a") == "a"
    assert tot_of_key("2:1") == 3
    assert tot_of_key("other_d") > 5          # 兜底格代表 6+ 球质


def test_generate_dist_deterministic_and_banded():
    d1 = generate_dist({"h": .30, "d": .25, "a": .45}, MKT, {})
    d2 = generate_dist({"h": .30, "d": .25, "a": .45}, MKT, {})
    assert d1 == d2                                     # 可复现（P26）
    assert abs(sum(d1.values()) - 1.0) < 1e-9
    for d in "hda":
        band = sum(v for k, v in d1.items() if dir_of_key(k) == d)
        assert abs(band - {"h": .30, "d": .25, "a": .45}[d]) < 1e-9   # 三向质量精确落带


def test_generate_dist_inherits_band_shape():           # 003 反例的机制解
    d = generate_dist({"h": .20, "d": .30, "a": .50}, MKT, {})
    assert abs(d["0:3"] / d["1:2"] - MKT["0:3"] / MKT["1:2"]) < 1e-9  # 带内比例继承
