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
    """欧冠 1043 场须独立进池，期望 ~3.22（此前被 '杯赛不进池' 排除）。"""
    assert "uefa-champions" in table, "欧冠库未进频率池"
    blob = table["uefa-champions"]
    assert blob["__n"] >= 1000, f"欧冠样本 {blob['__n']} 不足"
    assert 3.0 <= _pool_expect(blob) <= 3.45


def test_large_excluded_leagues_enter_pool(table):
    """8 个样本>=800 的被排除库全部进池。"""
    for key in ("spain-liga2", "germany-bundesliga2", "belgium-first-a",
                "turkey-super-lig", "greece-super", "SC0",
                "uefa-champions", "uefa-europa"):
        assert key in table, f"{key} 未进池"
        assert table[key]["__n"] >= 800, f"{key} 样本不足 800"


def test_tiny_cup_libraries_still_excluded(table):
    """样本<800 的小库仍不进池（libertadores 9 场 / coppa-italia 32 场）。"""
    for key in ("libertadores", "coppa-italia", "dfb-pokal", "asian-u23"):
        assert key not in table, f"{key} 样本不足却进池"


def test_total_pool_size_grew(table):
    """池总量须从 28438 增至约 41667（+13229）。"""
    total = sum(b.get("__n", 0) for b in table.values())
    assert total >= 40000, f"池总量 {total} 未达预期"


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
