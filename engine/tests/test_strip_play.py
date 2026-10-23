"""strip_play 注释清理 + 三向判定回归（2026-10-23 周三014 漏判事故）。开发者 sszhang

病理：pick 人工注释格式持续演化，清理表跟不上就静默漏判——残留后缀使
pick_outcome_idx 认不出"主胜"→ directionHit=None。本测试把历轮已出现过的
注释形态全部钉住，新格式进来先在这里加用例。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from backfill import pick_outcome_idx, strip_play  # noqa: E402


def test_dot_suffix_stripped():
    """顿号状态后缀（10-14 轮 '候选待终审'）——本次事故形态。"""
    assert strip_play("HAD 主胜·候选待终审") == "主胜"
    assert pick_outcome_idx({"pick": "HAD 主胜·候选待终审"}) == 0
    assert pick_outcome_idx({"pick": "HAD 客胜·候选待终审"}) == 2
    assert pick_outcome_idx({"pick": "HAD 平·候选待终审"}) == 1


def test_legacy_annotation_forms_unchanged():
    """历轮已支持的注释形态不得回归。"""
    assert strip_play("HAFU dd(平/平)(方案外)") == "dd"
    assert strip_play("HAD 主胜@1.31") == "主胜"           # 10-08 轮尾部赔率
    assert strip_play("HAD 客胜(终审剔除)") == "客胜"
    assert strip_play("HAD 平维持") == "平"
    assert strip_play("HAD 主胜（用户实票@1.70存续）") == "主胜"


def test_outcome_idx_legacy():
    assert pick_outcome_idx({"pick": "HAD 主胜"}) == 0
    assert pick_outcome_idx({"pick": "HAD 平维持"}) == 1
    assert pick_outcome_idx({"pick": "HAD 客胜(终审剔除)"}) == 2
    assert pick_outcome_idx({"pick": "HAD 主胜@1.31"}) == 0


def test_non_direction_plays_stay_none():
    """非三向玩法仍走各自专用判定器（HHAD 让球线/CRS 比分/TTG 总球），
    这里返 None 是正确分流，不是漏判。"""
    for pick in ("HHAD 让球客胜(+1)·候选待终审", "HHAD 让球主胜(-2)",
                 "CRS 主胜3:0", "TTG 总进球3", "HAFU dd(平/平)"):
        assert pick_outcome_idx({"pick": pick}) is None


def test_pointer_picks_stay_none():
    """指针式占位 pick（票账本引用，无玩法语义）不应被误解析成方向。"""
    for pick in ("T048腿", "闯关票腿(自动车道)·主链排除",
                 "排除:纯市场锚(无DC无画像·pure_template)"):
        assert pick_outcome_idx({"pick": pick}) is None
