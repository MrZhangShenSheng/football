# engine/tests/test_paper_track.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "shadow"))
import paper   # 2026-09-07 迁址后 shapes 懒加载——track 单测不再依赖 shapes.py（已丢失待重建）

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
