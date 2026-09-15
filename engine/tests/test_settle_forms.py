# -*- coding: utf-8 -*-
"""结算器形态兼容测试：T032 引入的 列表pick多选腿 + bets [腿索引,选项] 注结构。
09-15 教训：str(['0:1']) 变 repr 字符串 → option_hit 提不出比分 → 腿永久卡 pending。
开发者 sszhang"""
import backfill as bf


def _multi_leg():
    """T032 科莫腿真实形态：列表 pick + dict 赔率。"""
    return {"market": "CRS", "pick": ["1:0", "2:0"], "odds": {"1:0": 6.7, "2:0": 5.7}}


def _single_list_leg():
    """T032 尤文腿：单元素列表 pick。"""
    return {"market": "CRS", "pick": ["0:1"], "odds": {"0:1": 7.25}}


class TestLegHitListPick:
    def test_multi_pick_any_option_hits(self):
        assert bf.leg_hit(_multi_leg(), (1, 0), (0, 0)) is True
        assert bf.leg_hit(_multi_leg(), (2, 0), (0, 0)) is True

    def test_multi_pick_all_miss(self):
        assert bf.leg_hit(_multi_leg(), (2, 1), (1, 0)) is False

    def test_single_element_list_degrades_to_scalar(self):
        assert bf.leg_hit(_single_list_leg(), (0, 1), (0, 0)) is True
        assert bf.leg_hit(_single_list_leg(), (3, 2), (0, 0)) is False

    def test_scalar_pick_not_regressed(self):
        assert bf.leg_hit({"market": "CRS", "pick": "0:2"}, (0, 2), (0, 1)) is True
        assert bf.leg_hit({"market": "TTG", "pick": "2球"}, (1, 1), (0, 0)) is True


def _t032_ticket(leg_results):
    """T032 缩微：2 腿（科莫双选 + 罗马单选）×2 注 [idx,option] 结构。"""
    legs = [
        {"market": "CRS", "pick": ["1:0", "2:0"], "odds": {"1:0": 6.7, "2:0": 5.7},
         "result": leg_results[0][0], "actual": leg_results[0][1]},
        {"market": "CRS", "pick": ["0:2"], "odds": {"0:2": 7.5},
         "result": leg_results[1][0], "actual": leg_results[1][1]},
    ]
    return {
        "id": "T-test", "legs": legs, "unitStake": 2, "stake": 4, "units": 2,
        "bets": [
            {"legs": [[0, "1:0"], [1, "0:2"]], "multiplier": 1},
            {"legs": [[0, "2:0"], [1, "0:2"]], "multiplier": 1},
        ],
    }


class TestSettlePayoutIdxOptionBets:
    def test_option_level_payout_uses_dict_odds(self):
        """科莫实际 1:0：只有锁 '1:0' 的注中，赔率取 dict 键值 6.7×7.5。"""
        t = _t032_ticket([("hit", "1:0(半0:0)"), ("hit", "0:2(半0:1)")])
        res = bf.settle_payout(t)
        assert res["winUnits"] == 1
        assert res["payout"] == round(2 * 6.7 * 7.5, 2)

    def test_leg_hit_but_other_option_bet_misses(self):
        """腿级 hit（任一比分中）≠ 全注中：科莫 2:0 时锁 '1:0' 的注不中。"""
        t = _t032_ticket([("hit", "2:0(半1:0)"), ("hit", "0:2(半0:1)")])
        res = bf.settle_payout(t)
        assert res["winUnits"] == 1
        assert res["payout"] == round(2 * 5.7 * 7.5, 2)

    def test_t032_actual_outcome_all_miss(self):
        """T032 实况缩微：科莫 2-1 双选全 miss + 罗马 0:2 hit → 串关全灭 0 派彩。"""
        t = _t032_ticket([("miss", "2:1(半1:0)"), ("hit", "0:2(半0:1)")])
        res = bf.settle_payout(t)
        assert res["payout"] == 0.0
        assert res["winUnits"] == 0
        assert res["hits"] == 1
        assert res["net"] == -4

    def test_index_only_bets_not_regressed(self):
        """旧形态（T031）：bets.legs 纯索引 + 标量赔率，行为不变。"""
        t = {
            "id": "T-old", "unitStake": 2, "stake": 2, "units": 1,
            "legs": [{"market": "TTG", "pick": "2球", "odds": 4.35,
                      "result": "hit", "actual": "1:1(半0:0)"}],
            "bets": [{"legs": [0], "multiplier": 10}],
        }
        res = bf.settle_payout(t)
        assert res["payout"] == round(2 * 10 * 4.35, 2)
        assert res["winUnits"] == 1
