from live_odds_probe import verdict

def test_verdict_any_ok():
    rs = [{"source": "the-odds-api", "ok": True}, {"source": "pinnacle", "ok": False}]
    assert verdict(rs) == "layer1_live"

def test_verdict_all_fail():
    rs = [{"source": "the-odds-api", "ok": False}, {"source": "pinnacle", "ok": False}]
    assert verdict(rs) == "layer1_prior"
