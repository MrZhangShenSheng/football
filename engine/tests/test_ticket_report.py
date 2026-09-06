"""ticket_report 看板三新列测试（confidence-tiering 任务 8 · TDD 先行）。
兼容面①：旧票 22 张无 coverGate/playType 新字段 → 新 KPI 行不渲染不报错。开发者 sszhang"""
import ticket_report as tr

def test_kpi_row_skipped_when_no_new_fields():
    # 兼容面①: 旧票无coverGate/playType新前缀→新KPI行不渲染(不报错)
    old_ticket = {"id": "T001", "stake": 22, "legs": [], "settled": {"payout": 20.4}}
    assert tr.has_new_kpi([old_ticket]) is False

def test_kpi_row_present_for_new_ticket():
    new_ticket = {"id": "T023", "stake": 32, "legs": [], "playType": "N-CRS-2x1",
                  "coverGate": {"ok": True}, "settled": {"payout": 0}}
    assert tr.has_new_kpi([new_ticket]) is True

def test_breakthrough_count():
    tickets = [
        {"id": "T023", "note": "突破覆盖:大哥拍板", "stake": 32, "legs": []},
        {"id": "T024", "stake": 32, "legs": []},
    ]
    assert tr.count_breakthrough(tickets) == 1

def test_narrative_split():
    tickets = [
        {"id": "T023", "playType": "N-CRS-2x1", "stake": 2, "legs": [], "settled": {"payout": 0}},
        {"id": "T024", "playType": "N-HAFU-3x1", "stake": 1, "legs": [], "settled": {"payout": 0}},
        {"id": "T025", "stake": 32, "legs": [], "settled": {"payout": 0}},
    ]
    n = tr.narrative_tickets(tickets)
    assert len(n) == 2
