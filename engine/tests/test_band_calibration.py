from band_calibration import devid, band_of, bootstrap_ci, judge

def test_devid_sums_to_one():
    ph, pd_, pa = devid(2.0, 3.5, 4.0)
    assert abs(ph + pd_ + pa - 1.0) < 1e-9 and ph > pd_ > pa

def test_band_of_boundaries():
    assert band_of(0.14) == "<0.15" and band_of(0.15) == "0.15-0.30"
    assert band_of(0.30) == "0.30-0.45" and band_of(0.45) == "0.45-0.60"
    assert band_of(0.60) == ">=0.60" and band_of(0.9) == ">=0.60"

def test_bootstrap_ci_deterministic_and_reasonable():
    import statistics
    rets = [0.0] * 60 + [8.0] * 5                      # 均值 0.615
    lo, hi = bootstrap_ci(rets, n=200)
    assert lo <= 0.615 <= hi and 0 <= lo < 1.5         # 粗界内

def test_judge_rules():
    assert judge("0.30-0.45", 0.97, 0.95) == "可买带"   # 下界超全带基线
    assert judge("0.30-0.45", 0.90, 0.95) == "不差带"   # 未超但不毒
    assert judge("<0.15", 0.70, 0.95) == "排除带"       # 点估计毒药档
