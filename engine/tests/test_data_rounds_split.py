# -*- coding: utf-8 -*-
"""load_rounds 拆轮归并测试（-rN 盲区第三个消费者）。开发者 sszhang"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shadow"))
import data as sdata


@pytest.fixture()
def results_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(sdata, "RESULTS_DIR", str(tmp_path))
    return tmp_path


def _write(d, name, matches):
    (d / name).write_text(json.dumps({"matches": matches}, ensure_ascii=False), encoding="utf-8")


def test_split_round_merges_into_sale_date(results_dir):
    _write(results_dir, "2026-10-14.json", [{"code": "周三001", "result": "3-0"}])
    _write(results_dir, "2026-10-14-r1.json",
           [{"code": "周三013", "result": "1-2"}, {"code": "周三014", "result": "0-0"}])
    rounds = {r["date"]: r["legs"] for r in sdata.load_rounds()}
    assert set(rounds) == {"2026-10-14"}                      # 不再冒出 '2026-10-14-r1'
    codes = {l["code"]: l for l in rounds["2026-10-14"]}
    assert set(codes) == {"周三001", "周三013", "周三014"}
    assert codes["周三013"]["outcome"] == 2 and codes["周三014"]["outcome"] == 1


def test_main_file_wins_on_conflict(results_dir):
    _write(results_dir, "2026-10-14.json", [{"code": "周三001", "result": "1-0"}])
    _write(results_dir, "2026-10-14-r1.json", [{"code": "周三001", "result": "9-9"}])
    legs = sdata.load_rounds()[0]["legs"]
    assert legs[0]["score"] == (1, 0)        # 主文件胜（'-r1' 排序在前·主文件后写覆盖）


def test_non_date_filenames_ignored(results_dir):
    _write(results_dir, "2026-10-14.json", [{"code": "周三001", "result": "1-0"}])
    _write(results_dir, "notes-draft.json", [{"code": "周三999", "result": "5-5"}])
    rounds = sdata.load_rounds()
    assert [r["date"] for r in rounds] == ["2026-10-14"]
