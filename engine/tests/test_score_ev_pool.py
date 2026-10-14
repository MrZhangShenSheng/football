import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from score_ev import build_freq_table


def _pool_expect(blob):
    n = blob["__n"]
    return sum((c / n) * sum(int(x) for x in s.split(":"))
               for s, c in blob.items() if s != "__n")


def test_uefa_champions_enters_pool():
    """欧冠 1043 场须独立进池，期望 ~3.22（此前被 '杯赛不进池' 排除）。"""
    table = build_freq_table()
    assert "uefa-champions" in table, "欧冠库未进频率池"
    blob = table["uefa-champions"]
    assert blob["__n"] >= 1000, f"欧冠样本 {blob['__n']} 不足"
    assert 3.0 <= _pool_expect(blob) <= 3.45


def test_large_excluded_leagues_enter_pool():
    """8 个样本>=800 的被排除库全部进池。"""
    table = build_freq_table()
    for key in ("spain-liga2", "germany-bundesliga2", "belgium-first-a",
                "turkey-super-lig", "greece-super", "SC0",
                "uefa-champions", "uefa-europa"):
        assert key in table, f"{key} 未进池"
        assert table[key]["__n"] >= 800, f"{key} 样本不足 800"


def test_tiny_cup_libraries_still_excluded():
    """样本<800 的小库仍不进池（libertadores 9 场 / coppa-italia 32 场）。"""
    table = build_freq_table()
    for key in ("libertadores", "coppa-italia", "dfb-pokal", "asian-u23"):
        assert key not in table, f"{key} 样本不足却进池"


def test_total_pool_size_grew():
    """池总量须从 28438 增至约 41667（+13229）。"""
    table = build_freq_table()
    total = sum(b.get("__n", 0) for b in table.values())
    assert total >= 40000, f"池总量 {total} 未达预期"
