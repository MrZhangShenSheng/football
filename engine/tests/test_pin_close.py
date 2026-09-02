# -*- coding: utf-8 -*-
"""pin_close 三键匹配测试。"""
import json
from pathlib import Path

import pytest

from pin_close import parse_fd_date, fd_league_name, match_pin_close, apply_pin_close


class TestParseFdDate:
    def test_ddmmyyyy(self):
        assert parse_fd_date("23/08/2026") == "2026-08-23"

    def test_invalid(self):
        assert parse_fd_date("bad") is None
        assert parse_fd_date(None) is None


class TestLeagueMap:
    def test_known(self):
        assert fd_league_name("英超") == "england-premier"
        assert fd_league_name("德乙(R3)") == "germany-bundesliga2"   # strip 轮次后缀
        assert fd_league_name("欧冠") == "EC0"

    def test_ucl_qualifiers_variant(self):
        # I-2：'欧冠资格赛(次回合生死战)' 剥后缀 → 欧冠资格赛 → EC0（真实错题 3 场救回）
        assert fd_league_name("欧冠资格赛(次回合生死战)") == "EC0"
        assert fd_league_name("欧冠附加赛") == "EC0"

    def test_unknown_returns_none(self):
        assert fd_league_name("日职") is None          # fd 不覆盖
        assert fd_league_name("韩职") is None


class TestMatchPinClose:
    def _cache(self, tmp_path, rows):
        d = tmp_path / "odds_england-premier_2627.json"
        d.write_text(json.dumps({"season": "2627", "matches": rows}), encoding="utf-8")
        return tmp_path

    def test_unique_match(self, tmp_path):
        c = self._cache(tmp_path, [
            {"date": "28/08/2026", "home": "A", "away": "B", "fthg": "2", "ftag": "1",
             "pin_h": "2.0", "pin_d": "3.4", "pin_a": "3.8"},
            {"date": "28/08/2026", "home": "C", "away": "D", "fthg": "0", "ftag": "0",
             "pin_h": "1.5", "pin_d": "4.0", "pin_a": "6.0"},
        ])
        out = match_pin_close("英超", "2026-08-28", "2-1", c)
        assert out[0] == "fd"
        pin = out[1]
        assert abs(sum(pin) - 1.0) < 1e-6          # devig 去水归一
        assert pin[0] > pin[1] and pin[0] > pin[2] # 2.0 最低赔 → 主胜概率最高

    def test_ambiguous_same_score(self, tmp_path):
        c = self._cache(tmp_path, [
            {"date": "28/08/2026", "home": "A", "away": "B", "fthg": "1", "ftag": "1",
             "pin_h": "2.5", "pin_d": "3.2", "pin_a": "2.8"},
            {"date": "28/08/2026", "home": "C", "away": "D", "fthg": "1", "ftag": "1",
             "pin_h": "1.9", "pin_d": "3.5", "pin_a": "4.0"},
        ])
        out = match_pin_close("英超", "2026-08-28", "1-1", c)
        assert out[0] == "ambiguous" and out[1] is None

    def test_date_window_plus_minus_one(self, tmp_path):
        # fd 当地日=27（完赛日 28 的前一天，跨时区晚场）
        c = self._cache(tmp_path, [
            {"date": "27/08/2026", "home": "A", "away": "B", "fthg": "2", "ftag": "1",
             "pin_h": "2.0", "pin_d": "3.4", "pin_a": "3.8"},
        ])
        out = match_pin_close("英超", "2026-08-28", "2-1", c)
        assert out[0] == "fd"

    def test_no_league_coverage(self, tmp_path):
        out = match_pin_close("日职", "2026-08-28", "2-1", tmp_path)
        assert out[0] == "none" and out[1] is None

    def test_no_row(self, tmp_path):
        c = self._cache(tmp_path, [])
        out = match_pin_close("英超", "2026-08-28", "2-1", c)
        assert out[0] == "none" and out[1] is None

    def test_invalid_match_date_safe(self):
        # matchDate 非 ISO 格式（体彩口径可能是 '2026-08-28 00:00:00'）→ 安全降级 none 不崩
        out = match_pin_close("英超", "28/08/2026 03:00", "2-1", Path("."))
        assert out[0] == "none" and out[1] is None

    def test_leading_zero_score_normalized(self, tmp_path):
        # fd fthg='05' 补零 vs result '5-x' → int 归一后匹配（M1）
        c = self._cache(tmp_path, [
            {"date": "28/08/2026", "fthg": "05", "ftag": "1",
             "pin_h": "2.0", "pin_d": "3.4", "pin_a": "3.8"},
        ])
        out = match_pin_close("英超", "2026-08-28", "5-1", c)
        assert out[0] == "fd"

    def test_annotated_score_conservative_miss(self, tmp_path):
        # result 带注释 '2-1（加时）' → 保守 miss none（M1）
        c = self._cache(tmp_path, [
            {"date": "28/08/2026", "fthg": "2", "ftag": "1",
             "pin_h": "2.0", "pin_d": "3.4", "pin_a": "3.8"},
        ])
        out = match_pin_close("英超", "2026-08-28", "2-1（加时）", c)
        assert out[0] == "none" and out[1] is None


class TestApplyPinClose:
    """回填集成点：rec 增补 pinClose/pinSource。"""

    def test_apply(self, tmp_path):
        (tmp_path / "odds_england-premier_2627.json").write_text(json.dumps(
            {"season": "2627", "matches": [
                {"date": "28/08/2026", "fthg": "2", "ftag": "1",
                 "pin_h": "2.0", "pin_d": "3.4", "pin_a": "3.8"}]}), encoding="utf-8")
        rec = {"league": "英超", "result": "2-1"}
        apply_pin_close(rec, "2026-08-28", tmp_path)
        assert rec["pinSource"] == "fd"
        assert abs(sum(rec["pinClose"]) - 1.0) < 1e-6

    def test_idempotent_no_overwrite(self, tmp_path):
        rec = {"league": "英超", "result": "2-1", "pinSource": "fd", "pinClose": [0.5, 0.2, 0.3]}
        assert apply_pin_close(rec, "2026-08-28", tmp_path) is False   # 已有→无变更
        assert rec["pinClose"] == [0.5, 0.2, 0.3]      # 已有不覆盖（幂等）

    def test_none_to_none_no_change(self, tmp_path):
        # 两次跑都是 none → 第二次返回 False（不置 dirty·M2）
        rec = {"league": "英超", "result": "2-1", "pinSource": "none"}
        assert apply_pin_close(rec, "2026-08-28", tmp_path) is False
        assert rec["pinSource"] == "none"


class TestMatchPinCloseV2:
    """层2 四键匹配（docs/2026-09-02-data-backfill-design.html）：队名主键 + 赛季隔离。

    真实桥接表（_aliases.json）不在 tmp_path，队名路径需 monkeypatch 映射表。
    """

    def test_season_of(self):
        from pin_close import season_of
        assert season_of("2026-09-02") == "2627"
        assert season_of("2026-03-01") == "2526"
        assert season_of("2026-07-31") == "2627"
        assert season_of("bad") is None

    def test_team_key_resolves_ambiguous(self, tmp_path, monkeypatch):
        # 旧三键 ambiguous（同窗同比分两行）→ 队名第四键唯一命中（层2 根因1）
        import pin_close
        monkeypatch.setattr(pin_close, "_zh_to_fd_names",
                            lambda: {"女王巡游": "qpr", "加的夫城": "cardiff"})
        c = tmp_path / "odds_england-championship_2627.json"
        c.write_text(json.dumps({"matches": [
            {"date": "28/08/2026", "home": "QPR", "away": "Cardiff", "fthg": "1", "ftag": "1",
             "pin_h": "2.5", "pin_d": "3.2", "pin_a": "2.8"},
            {"date": "28/08/2026", "home": "Millwall", "away": "Wrexham", "fthg": "1", "ftag": "1",
             "pin_h": "1.9", "pin_d": "3.5", "pin_a": "4.0"}]}), encoding="utf-8")
        out = pin_close.match_pin_close("英冠", "2026-08-28", "1-1", tmp_path,
                                        match_zh="女王巡游 vs 加的夫城")
        assert out[0] == "fd" and abs(sum(out[1]) - 1.0) < 2e-3   # round4 舍入容差

    def test_team_key_score_conflict_degrades(self, tmp_path, monkeypatch):
        # 队名唯一命中但比分不符 → 数据冲突诚实降级 none（不硬收）
        import pin_close
        monkeypatch.setattr(pin_close, "_zh_to_fd_names",
                            lambda: {"女王巡游": "qpr", "加的夫城": "cardiff"})
        c = tmp_path / "odds_england-championship_2627.json"
        c.write_text(json.dumps({"matches": [
            {"date": "28/08/2026", "home": "QPR", "away": "Cardiff", "fthg": "3", "ftag": "0",
             "pin_h": "2.5", "pin_d": "3.2", "pin_a": "2.8"}]}), encoding="utf-8")
        out = pin_close.match_pin_close("英冠", "2026-08-28", "1-1", tmp_path,
                                        match_zh="女王巡游 vs 加的夫城")
        assert out[0] == "none" and out[1] is None

    def test_cross_season_isolation_on_fallback(self, tmp_path, monkeypatch):
        # 回退路径（队名桥接失败）只读当季文件：上季同窗同比分唯一行不再被误配（层2 根因2）
        import pin_close
        monkeypatch.setattr(pin_close, "_zh_to_fd_names", lambda: {})
        for season in ("2526", "2627"):
            (tmp_path / f"odds_england-premier_{season}.json").write_text(json.dumps(
                {"matches": [
                    {"date": "28/08/2026", "home": "A", "away": "B", "fthg": "2", "ftag": "1",
                     "pin_h": "2.0", "pin_d": "3.4", "pin_a": "3.8"}]}), encoding="utf-8")
        # 旧版 glob 两季联扫：两行 ambiguous；新版当季单读：唯一行 → fd
        out = pin_close.match_pin_close("英超", "2026-08-28", "2-1", tmp_path)
        assert out[0] == "fd"

    def test_team_key_reads_window_seasons(self, tmp_path, monkeypatch):
        # 主路径（队名键）跨季边界窗口扫两季文件——真两队名不会错配
        import pin_close
        monkeypatch.setattr(pin_close, "_zh_to_fd_names",
                            lambda: {"女王巡游": "qpr", "加的夫城": "cardiff"})
        # 完赛 2026-06-30（2526 赛季末轮）：窗口 {06-29..07-01} 跨 2526/2627；行在 2526 文件
        (tmp_path / "odds_england-championship_2526.json").write_text(json.dumps(
            {"matches": [
                {"date": "30/06/2026", "home": "QPR", "away": "Cardiff", "fthg": "1", "ftag": "0",
                 "pin_h": "2.5", "pin_d": "3.2", "pin_a": "2.8"}]}), encoding="utf-8")
        out = pin_close.match_pin_close("英冠", "2026-06-30", "1-0", tmp_path,
                                        match_zh="女王巡游 vs 加的夫城")
        assert out[0] == "fd" and out[1] is not None   # 跨季窗口主路径可达（精度见上容差）
