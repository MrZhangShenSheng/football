"""odds_fetch.normalize_row 单测：OU 三键扩展（T2）+ 收盘列双命名兼容（2026-10-07）。"""
from odds_fetch import normalize_row

# 收盘双命名（2026-10-07 v16 自检修复后）：PPC*（2627 系）与 PSC*（2425/2526 系）都是
# Pinnacle 收盘·按序优先；两套皆缺整行丢弃——不再有"老赛季降级到 PSH 赛前价"通道。
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


# ---- 2026-10-07 v16 自检修复：收盘列双命名兼容（PPC*=2627 系·PSC*=2425/2526 系）----
# 实证：odds_england-premier_{2425,2526} 590/590 场 pin==PSH(赛前)≠PSCH(收盘)；
# 2627 系 50/50==PPCH(收盘)。修复前老赛季被静默降级到赛前价，污染一切"收盘"消费方
# （7216 sweep/v16/实力链三水）。开发者 sszhang
ROW_2425 = {"Date": "16/08/2025", "HomeTeam": "Tottenham", "AwayTeam": "Burnley",
            "FTHG": "3", "FTAG": "0",
            "PSH": "1.40", "PSD": "4.71", "PSA": "8.79",
            "PSCH": "1.56", "PSCD": "4.20", "PSCA": "6.70"}
ROW_2627 = {"Date": "21/08/2026", "HomeTeam": "Arsenal", "AwayTeam": "Coventry",
            "FTHG": "3", "FTAG": "0",
            "PPH": "1.17", "PPD": "7.00", "PPA": "15.0",
            "PPCH": "1.17", "PPCD": "7.00", "PPCA": "15.0"}

def test_psc_is_closing_for_2425_style():
    """2425/2525 列体系（PSH=赛前·PSCH=收盘）：pin_* 必须取 PSCH·开盘取 PSH。"""
    m = normalize_row(dict(ROW_2425))
    assert m is not None
    assert m["pin_h"] == "1.56" and m["pin_d"] == "4.20" and m["pin_a"] == "6.70"
    assert m["pin_open_h"] == "1.40" and m["pin_open_d"] == "4.71" and m["pin_open_a"] == "8.79"

def test_ppc_is_closing_for_2627_style():
    """2627 列体系（PPH=赛前·PPCH=收盘）：维持取 PPCH·开盘取 PPH。"""
    m = normalize_row(dict(ROW_2627))
    assert m is not None
    assert m["pin_h"] == "1.17"
    assert m["pin_open_h"] == "1.17"

def test_no_closing_columns_drops_row():
    """两套收盘列皆缺：宁缺毋滥整行丢弃·不再静默降级到赛前价。"""
    bad = {k: v for k, v in ROW_2425.items() if not k.startswith("PSC")}
    assert normalize_row(bad) is None
