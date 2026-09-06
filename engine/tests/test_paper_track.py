# engine/tests/test_paper_track.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts" / "scratch" / "replay_v2"))
import pytest
# paper.py 顶层 import shapes——shapes.py/data.py 属 scratch(已 gitignore 不入库)，
# fresh clone 缺 shapes 时跳过本模块而非炸全仓收集(final-fix I-1)
pytest.importorskip("shapes")
import paper

def test_old_ticket_defaults_track_a():
    t = {"id": "S001", "spec_name": "x", "legs": [], "cost": 2}   # 旧影子票无track
    assert paper._track_of(t) == "A"

def test_new_ticket_keeps_track():
    assert paper._track_of({"track": "N"}) == "N"

def test_by_track_stats():
    tickets = [
        {"id": "S1", "track": "A", "payout": 10, "cost": 2, "result": "hit"},
        {"id": "S2", "track": "N", "payout": 0, "cost": 2, "result": "miss"},
        {"id": "S3", "payout": 0, "cost": 2, "result": "miss"},   # 旧票→A
    ]
    stats = paper._by_track(tickets)
    assert stats["A"]["n"] == 2 and stats["N"]["n"] == 1
    assert stats["A"]["payout"] == 10
