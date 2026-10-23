import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pytest

from score_ev import build_freq_table, map_league, resolve_pool


@pytest.fixture(scope="module")
def table():
    """池构建走网络（fd 多季），module 级复用——逐测试重建会让本文件耗时 13 分钟。"""
    return build_freq_table()


def _pool_expect(blob):
    n = blob["__n"]
    return sum((c / n) * sum(int(x) for x in s.split(":"))
               for s, c in blob.items() if s != "__n")


def test_uefa_champions_enters_pool(table):
    """欧冠须独立进池，期望 ~3.20（此前被 '杯赛不进池' 排除）。

    2026-10-23 口径修正：原断言写 1043 场/>=1000，那是跨时区日双记的膨胀值
    （同一场按两个日期各记一行）。去重后真实 706 场，期望 3.197 几乎不变——
    重复行是整场复制，不改变分布，只虚增样本量。"""
    assert "uefa-champions" in table, "欧冠库未进频率池"
    blob = table["uefa-champions"]
    assert blob["__n"] >= 700, f"欧冠样本 {blob['__n']} 不足"
    assert 3.0 <= _pool_expect(blob) <= 3.45


def test_large_excluded_leagues_enter_pool(table):
    """8 个样本>=POOL_MIN_N 的被排除库全部进池。

    2026-10-23：门槛由 800 降至 600（见 score_ev.POOL_MIN_N 注释）。去重后
    欧冠 706/欧联 653/苏超 734 的真实样本介于 600~800，按旧门槛会回退全局池，
    欧冠将吃 −0.46 球偏差——正是 10-14 低分偏置修正要消除的缺陷。"""
    from score_ev import POOL_MIN_N
    for key in ("spain-liga2", "germany-bundesliga2", "belgium-first-a",
                "turkey-super-lig", "greece-super", "SC0",
                "uefa-champions", "uefa-europa"):
        assert key in table, f"{key} 未进池"
        assert table[key]["__n"] >= POOL_MIN_N, f"{key} 样本不足 {POOL_MIN_N}"


def test_tiny_cup_libraries_still_excluded(table):
    """样本<POOL_MIN_N 的小库仍不进池（libertadores 9 场 / coppa-italia 32 场）。"""
    for key in ("libertadores", "coppa-italia", "dfb-pokal", "asian-u23"):
        assert key not in table, f"{key} 样本不足却进池"


def test_total_pool_size_grew(table):
    """池总量须显著高于改造前的 28438。

    2026-10-23 口径修正：原断言 >=40000 对标 41667，而那个数含 league 库 8214 行
    重复（fd 八库整库双导 + 四库跨时区日双记）。去重后真实总量约 36600，仍远高于
    改造前基线——该测试守的是"被排除的大库进来了"，不是守具体膨胀值。"""
    total = sum(b.get("__n", 0) for b in table.values())
    assert total >= 35000, f"池总量 {total} 未达预期"


def test_uefa_champions_mapped():
    """欧冠须映射到自己的池，不再返回 None 落全局池。"""
    assert map_league("欧冠") == "uefa-champions"


def test_cup_fallback_to_regional_pool(table):
    """样本不足的杯赛回退地域近似池，而非全局池。"""
    assert resolve_pool("解放者杯", table)[0] == "brazil"


def test_multi_key_fallback_merges_all_pools(table):
    """多键回退须合并全部候选为加权池，不可只取首个（2026-10-14 korea 空转缺陷）。"""
    key, blob = resolve_pool("亚冠精英", table)
    assert key == "japan+korea+saudi", f"亚冠回退键 {key} 未合并三国"
    assert blob["__n"] == sum(table[k]["__n"] for k in ("japan", "korea", "saudi"))


def test_pool_alias_resolves_keyname_mismatch(table):
    """库文件名与 LEAGUE_MAP 目标键的两处不一致须由别名表消解（Ruling R1）。"""
    assert resolve_pool("西乙", table)[0] == "spain-liga2"
    assert resolve_pool("德乙", table)[0] == "germany-bundesliga2"


def test_unmapped_still_returns_none():
    """未知联赛仍返回 None（调用方落全局池，行为不变）。"""
    assert map_league("火星联赛") is None
