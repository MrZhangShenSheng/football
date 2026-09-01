"""odds_fetch.normalize_row 单测：OU 三键扩展（T2）。"""
from odds_fetch import normalize_row

# 收盘判定走主通道 PPC*；老赛季 fd 无 PPC* 列，normalize_row 里 g("PPCH","Psh")
# 的小写归一通道会 fallback 到 Psh（Pinnacle 开盘价），语义=老赛季降级锚。
BASE = {"Date": "14/08/2026", "HomeTeam": "Arsenal", "AwayTeam": "Chelsea",
        "FTHG": "2", "FTAG": "1", "PPCH": "1.50", "PPCD": "4.20", "PPCA": "6.00"}

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

def test_ou_empty_string_treated_missing():
    """OU 列头在值为空串 → g 返回 "" 视为缺，配对不齐三键全 None。"""
    row = dict(BASE, **{"P>2.5": "", "P<2.5": "", "B365>2.5": "", "B365<2.5": ""})
    m = normalize_row(row)
    assert m["ou_over25"] is None
    assert m["ou_under25"] is None
    assert m["ou_source"] is None

def test_ou_asymmetric_pair_dropped():
    """over/under 不对称（pin over 有值 under 空）→ 配对不齐三键全 None，不得标 pin。"""
    row = dict(BASE, **{"P>2.5": "1.95", "P<2.5": ""})
    m = normalize_row(row)
    assert m["ou_over25"] is None
    assert m["ou_under25"] is None
    assert m["ou_source"] is None
