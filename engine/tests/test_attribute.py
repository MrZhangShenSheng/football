# -*- coding: utf-8 -*-
"""归因引擎单元测试。"""
import pytest

from attribute import pick_to_index, result_to_idx, correction_flipped, classify, odds_drift_buy_heat


class TestIndexParse:
    """pick→方向下标 / result→结果下标。"""

    def test_pick_had_away(self):
        assert pick_to_index("HAD", "客胜") == 2

    def test_pick_had_home(self):
        assert pick_to_index("HAD", "主胜") == 0

    def test_pick_had_draw(self):
        assert pick_to_index("HAD", "平") == 1

    def test_non_had_returns_none(self):
        assert pick_to_index("CRS", "2-1") is None

    def test_result_home_win(self):
        assert result_to_idx("3-1") == 0

    def test_result_away_win(self):
        assert result_to_idx("0-2") == 2

    def test_result_draw(self):
        assert result_to_idx("2-2") == 1

    def test_result_invalid(self):
        assert result_to_idx("弃赛") is None
        assert result_to_idx(None) is None


class TestCorrectionReplay:
    """F5 近似：DC 对(dc_best==result) 但 fused 错(≠result) → 修正/融合背锅。"""

    def test_dc_right_fused_wrong(self):
        # DC 看主、fused 看客、结果主胜 → DC 对融合错
        dc = [0.6, 0.2, 0.2]; fused = [0.2, 0.2, 0.6]; result_idx = 0
        assert correction_flipped(dc, fused, result_idx) is True

    def test_dc_also_wrong(self):
        # DC 也看主、结果客胜 → DC 也错，不是修正背锅
        dc = [0.6, 0.2, 0.2]; fused = [0.6, 0.2, 0.2]; result_idx = 2
        assert correction_flipped(dc, fused, result_idx) is False

    def test_dc_right_fused_right(self):
        # DC 和 fused 都对 → 不是错题场景，但函数应返 False
        dc = [0.2, 0.2, 0.6]; fused = [0.2, 0.2, 0.6]; result_idx = 2
        assert correction_flipped(dc, fused, result_idx) is False

    def test_missing_data_returns_none(self):
        assert correction_flipped(None, [0.6, 0.2, 0.2], 0) is None
        assert correction_flipped([0.6, 0.2, 0.2], [0.6, 0.2, 0.2], None) is None


class TestClassify:
    """主判别树：错题 → (primary, secondary, evidence, confidence)。"""

    def _mk(self, **kw):
        base = dict(code="x", pick="HAD 客胜", dc=[0.15, 0.2, 0.65],
                    fused=[0.1, 0.15, 0.75], result="2-2",
                    directionHit=False, scoreHit=False, chain="R3×0.95")
        base.update(kw)
        return base

    def test_F5_dc_right_fused_wrong(self):
        # DC 看客、fused 看主、结果客胜 → pick=主胜错，DC对被改成错 = F5
        r = self._mk(pick="HAD 主胜", dc=[0.1, 0.2, 0.7],
                     fused=[0.6, 0.2, 0.2], result="0-2")
        out = classify(r)
        assert out["primary"] == "F5"
        assert out["confidence"] == "low"   # R4 近似版标低

    def test_F9_dc_also_wrong(self):
        # DC 和 fused 都看主、结果客胜 → 都错 = F9 随机兜底
        r = self._mk(pick="HAD 主胜", dc=[0.6, 0.2, 0.2],
                     fused=[0.6, 0.2, 0.2], result="0-2")
        out = classify(r)
        assert out["primary"] == "F9"
        assert out["confidence"] == "high"

    def test_non_had_falls_to_F9_low(self):
        # 非 HAD 玩法（CRS 等）→ 暂落 F9 低置信（R7 变体待 P2）
        r = self._mk(pick="CRS 2-1", result="2-2")
        out = classify(r)
        assert out["primary"] == "F9"
        assert out["confidence"] == "low"

    def test_unparseable_result_falls_to_F9_low(self):
        r = self._mk(result="弃赛")
        out = classify(r)
        assert out["primary"] == "F9"
        assert out["confidence"] == "low"


class TestOddsDrift:
    """F10：score_odds 出票后赔率向不利方向漂移=追热（R5）。"""

    def test_drift_against_pick_buy_heat(self):
        # pick=客胜 odds=1.55，漂移后 1.65（赔率升=买热更难赚）
        drift = {"pickOdds": 1.55, "laterOdds": 1.65}
        assert odds_drift_buy_heat(drift) is True

    def test_drift_toward_pick_not_buy_heat(self):
        # 赔率降=卖冷，不是追热
        drift = {"pickOdds": 1.55, "laterOdds": 1.45}
        assert odds_drift_buy_heat(drift) is False

    def test_small_drift_below_threshold(self):
        # 涨幅 <2% 阈值=噪声，不算追热
        drift = {"pickOdds": 1.55, "laterOdds": 1.56}
        assert odds_drift_buy_heat(drift) is False

    def test_missing_odds(self):
        assert odds_drift_buy_heat(None) is False
        assert odds_drift_buy_heat({}) is False
