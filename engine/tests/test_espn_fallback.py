# engine/tests/test_espn_fallback.py
# -*- coding: utf-8 -*-
"""ESPN 兜底链路测试（2026-10-12 批二）：黑名单口径一致性 + 别名表健康 + 匹配纪律。

立项实证：圣路易城 6-2 洛城银河当日须人肉从 ESPN 补,根因三层——
  ① "美职"在 ESPN_UNAVAILABLE 黑名单(而 usa.1 接口一直可用,黑名单写下即未复测);
  ② 两处联赛码表(backfill.LEAGUE_ESPN / espn_fetch.ESPN_LEAGUES)各自演化致口径分裂;
  ③ 别名表体彩译名与 zh 一字之差(洛城银河 vs 洛杉矶银河)致 match_team 返 None。
开发者 sszhang
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import backfill as bf  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def test_blacklist_not_overlap_league_codes():
    """黑名单与联赛码表不得交集——有码却列'不可得'=口径分裂(美职实证)。"""
    overlap = set(bf.LEAGUE_ESPN) & bf.ESPN_UNAVAILABLE
    assert not overlap, f"联赛同时有 ESPN 码又在黑名单: {overlap}"


def test_unlocked_leagues_have_codes():
    """批二解锁六项 + 欧战两项（10-12 晚）须有码（防回退）。"""
    for lg in ("美职", "荷乙", "法乙", "德乙", "英冠", "友谊赛", "欧冠", "欧罗巴"):
        assert lg in bf.LEAGUE_ESPN, f"{lg} 解锁后应有 ESPN 码"
        assert lg not in bf.ESPN_UNAVAILABLE


def test_uefa_codes_consistent_across_tables():
    """欧战码须两表同有且标杯赛——批二病根即两表各自演化（美职实证），此为防复发锚。
    2026-10-12 实测依据：dates=YYYY&limit=500 全量 189 场/季可取。"""
    import espn_fetch as ef
    for code in ("uefa.champions", "uefa.europa"):
        assert code in ef.ESPN_LEAGUES, f"{code} 须在 espn_fetch.ESPN_LEAGUES"
        assert ef.ESPN_LEAGUES[code] in ef.CUP_LEAGUES, "欧战属杯赛（dc_fit 跳过·频率池排除）"
    assert bf.LEAGUE_ESPN["欧冠"] == "uefa.champions"
    assert bf.LEAGUE_ESPN["欧罗巴"] == "uefa.europa"


def test_espn_name_map_covers_uefa_regulars():
    """欧冠常客 espn 字段可解析（含三处'有值但串不符 ESPN displayName'的改值修复：
    Red Bull Salzburg→RB Salzburg / Ferencváros→Ferencvaros / Slavia Praha→Slavia Prague）。"""
    import espn_fetch as ef
    m = ef.espn_name_map()
    for name in ("RB Salzburg", "Ferencvaros", "Slavia Prague", "Shakhtar Donetsk",
                 "Union St.-Gilloise", "Sparta Prague", "Olympiacos", "Rangers"):
        assert name in m, f"{name} 须可经 espn_name_map 解析（历史库回填丢失即此因）"


def test_known_400_leagues_stay_blacklisted():
    """实测真 400 的联赛须留黑名单（日乙 jpn.2 / 韩职 kor.1·2026-10-12 复测）。"""
    for lg in ("日乙", "韩职"):
        assert lg in bf.ESPN_UNAVAILABLE, f"{lg} 实测 400,不应解锁"


def test_no_harmful_duplicate_tid():
    """别名表重复 tid 不得'生效条 espn 空而他条有值'——load_aliases 后者覆盖前者,
    静默吞掉可用别名(qpr/millwall 实证)。"""
    raw = json.loads((ROOT / "data" / "01-teams" / "_aliases.json").read_text(encoding="utf-8"))
    seen: dict[str, list] = {}
    for league, teams in raw.items():
        if league.startswith("_") or not isinstance(teams, dict):
            continue
        for tid, srcs in teams.items():
            if isinstance(srcs, dict):
                seen.setdefault(tid, []).append(srcs.get("espn"))
    harmful = {t: v for t, v in seen.items()
               if len(v) > 1 and v[-1] is None and any(x for x in v[:-1])}
    assert not harmful, f"有害重复 tid（生效条 espn 空）: {harmful}"


def test_sporttery_names_resolvable_for_unlocked():
    """解锁联赛的体彩译名须能查到 espn 名（本批六队补齐后的回归锚）。"""
    zh_map = bf.zh_to_espn_map()
    for zh, expect in [("洛城银河", "LA Galaxy"), ("朴次茅斯", "Portsmouth"),
                       ("女王巡游", "Queens Park Rangers"), ("布城", "Bristol City"),
                       ("达姆施塔", "SV Darmstadt 98"), ("海尔蒙特", "Helmond Sport")]:
        assert zh_map.get(zh) == expect, f"{zh} → {zh_map.get(zh)}（应 {expect}）"


def test_match_team_requires_both_sides():
    """匹配纪律：任一侧别名缺失即不得成单边匹配（防错配赛果——比不补更毒）。"""
    zh_map = {"甲队": "Team A"}
    assert bf.match_team("甲队", zh_map, "Team A", "Team B") == "home"
    assert bf.match_team("乙队", zh_map, "Team A", "Team B") is None   # 无别名→None
    assert bf.match_team("甲队", zh_map, "Team C", "Team B") is None   # 别名不在场上→None
