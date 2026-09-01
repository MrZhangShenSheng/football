"""odds_fetch.normalize_row 单测：OU 三键扩展（T2）。"""
from odds_fetch import normalize_row

BASE = {"Date": "14/08/2026", "HomeTeam": "Arsenal", "AwayTeam": "Chelsea",
        "FTHG": "2", "FTAG": "1", "PSH": "1.50", "PSD": "4.20", "PSA": "6.00"}

def test_ou_pin_source():
    row = dict(BASE, **{"P>2.5": "1.95", "P<2.5": "1.90", "B365>2.5": "1.90"})
    m = normalize_row(row)
    assert m["ou_over25"] == "1.95"
    assert m["ou_under25"] == "1.90"
    assert m["ou_source"] == "pin"

def test_ou_b365_fallback():
    row = dict(BASE, **{"B365>2.5": "1.85", "B365<2.5": "1.95"})
    m = normalize_row(row)
    assert m["ou_over25"] == "1.85"
    assert m["ou_source"] == "b365"

def test_ou_missing_is_none():
    m = normalize_row(dict(BASE))
    assert m["ou_over25"] is None and m["ou_source"] is None

def test_row_without_pin_h_dropped():
    assert normalize_row({"HomeTeam": "X", "AwayTeam": "Y"}) is None
