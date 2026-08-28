"""boldplay settle 逐 leg 判定测试（phase2-plan 任务 6 · TDD 先行）。开发者 sszhang"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from boldplay import settle  # noqa: E402

TICKET = {"totalCost": 18, "tiers": {
    "base": {"cost": 4, "legs": [[{"matchNumStr": "001", "play": "had", "pick": "主胜", "odds": 1.8},
                                  {"matchNumStr": "002", "play": "had", "pick": "平", "odds": 3.4}],
                                 [{"matchNumStr": "003", "play": "had", "pick": "客胜", "odds": 2.1},
                                  {"matchNumStr": "001", "play": "had", "pick": "主胜", "odds": 1.8}]]},
    "upset": {"cost": 8, "multiplier": 4,
              "legs": [{"matchNumStr": "001", "play": "crs", "pick": "2:0", "odds": 9.0},
                       {"matchNumStr": "002", "play": "crs", "pick": "1:1", "odds": 5.8},
                       {"matchNumStr": "003", "play": "crs", "pick": "2:1", "odds": 8.0},
                       {"matchNumStr": "004", "play": "crs", "pick": "3:0", "odds": 20.0}]}}}


def test_settle_partial_leg_hits():
    res = settle(TICKET, {"001": "2:0", "002": "0:0", "003": "2:1", "004": "1:0"})
    assert res["legHits"]["upset"] == [[True, False, True, False]]  # 3/4 部分命中=数据点（{tier:[[注×腿]]} 二维口径）
    assert res["upsetHit"] is False and res["payout"] == 0.0


def test_settle_full_hit_payout():
    res = settle(TICKET, {"001": "2:0", "002": "1:1", "003": "2:1", "004": "3:0"})
    assert res["upsetHit"] is True
    raw = 2 * 9.0 * 5.8 * 8.0 * 20.0 * 4
    assert abs(res["payout"] - raw) < 1e-6                          # 全中按合赔×2×倍数
    assert res["densityRecovered"] > 0


def test_settle_missing_result_marks_none():
    res = settle(TICKET, {"001": "2:0"})
    assert res["legHits"]["upset"] == [[True, None, None, None]]
    assert res["upsetHit"] is False


def test_settle_real_schema_score_key():
    """真实出票 JSON（2026-08-24-boldplay.json）字节级复刻：upset 腿无 play 键（按 tier 推断 crs）、选项存 score。"""
    real = {"totalCost": 18, "tiers": {
        "upset": {"cost": 8, "multiplier": 4,
                  "legs": [{"matchNumStr": "周二005", "score": "1:0", "odds": 11.0},
                           {"matchNumStr": "周二006", "score": "1:0", "odds": 13.0}]}}}
    res = settle(real, {"周二005": "1:0", "周二006": "1:0"})
    assert res["legHits"]["upset"] == [[True, True]] and res["upsetHit"] is True
    assert abs(res["payout"] - 2 * 11.0 * 13.0 * 4) < 1e-6


def test_settle_had_direction_from_score():
    had = {"totalCost": 4, "tiers": {
        "base": {"cost": 4, "legs": [[{"matchNumStr": "001", "play": "had", "pick": "客胜", "odds": 2.1}]]}}}
    assert settle(had, {"001": "0:2"})["legHits"]["base"] == [[True]]
    assert settle(had, {"001": "1:1"})["legHits"]["base"] == [[False]]


def test_settle_ttg_leg():
    """A-MIX TTG 腿：总进球判定（含 7+ 档）。"""
    tk = {"totalCost": 8, "tiers": {"upset": {"cost": 8, "multiplier": 2, "legs": [
        {"matchNumStr": "001", "play": "ttg", "pick": "2球", "odds": 4.25},
        {"matchNumStr": "002", "play": "ttg", "pick": "7+球", "odds": 16.0}]}}}
    assert settle(tk, {"001": "2:0", "002": "4:3"})["legHits"]["upset"] == [[True, True]]
    assert settle(tk, {"001": "1:0", "002": "5:1"})["legHits"]["upset"] == [[False, False]]  # 1球≠2球;6球<7
    assert settle(tk, {"001": "2:0", "002": "2:1"})["legHits"]["upset"] == [[True, False]]


def test_settle_hafu_leg_needs_half():
    """A-MIX HAFU 腿：02-results 无半场比分 → None 待人工（诚实口径，不自动判）。"""
    tk = {"totalCost": 8, "tiers": {"upset": {"cost": 8, "multiplier": 2, "legs": [
        {"matchNumStr": "001", "play": "hafu", "pick": "dd", "odds": 4.75}]}}}
    res = settle(tk, {"001": "1:1"})
    assert res["legHits"]["upset"] == [[None]] and res["upsetHit"] is False and res["payout"] == 0.0


def test_settle_hafu_with_half_auto_judged():
    """HAFU 腿 + dict 赛果带 half（backfill 落盘半场）→ 自动判定，闭环不再人工。"""
    tk = {"totalCost": 8, "tiers": {"upset": {"cost": 8, "multiplier": 2, "legs": [
        {"matchNumStr": "001", "play": "hafu", "pick": "dd", "odds": 4.75}]}}}
    res = settle(tk, {"001": {"score": "1:1", "half": "0:0"}})      # 半平+全平 = dd
    assert res["legHits"]["upset"] == [[True]]
    res = settle(tk, {"001": {"score": "2:1", "half": "1:0"}})      # 半主胜+全主胜 = hh ≠ dd
    assert res["legHits"]["upset"] == [[False]]
    tk_dh = {"totalCost": 8, "tiers": {"upset": {"cost": 8, "multiplier": 2, "legs": [
        {"matchNumStr": "001", "play": "hafu", "pick": "dh", "odds": 15.0}]}}}
    assert settle(tk_dh, {"001": {"score": "2:1", "half": "0:0"}})["legHits"]["upset"] == [[True]]  # 半0:0=平(d)+全2:1=胜(h)=dh


def test_load_results_reads_half_from_disk(tmp_path, monkeypatch):
    """02-results 落盘 half（backfill 体彩链路）→ _load_results 读出冒号口径，HAFU 闭环数据流。"""
    import json as _json
    day = tmp_path / "data" / "02-results"
    day.mkdir(parents=True)
    (day / "2026-08-24.json").write_text(_json.dumps({
        "date": "2026-08-24",
        "matches": [{"code": "周一001", "result": "2-1", "half": "1-0"},
                    {"code": "周一002", "result": "0-0"}]}), encoding="utf-8")
    monkeypatch.setattr("boldplay.ROOT", tmp_path)
    from boldplay import _load_results
    res = _load_results("2026-08-24")
    assert res["周一001"] == {"score": "2:1", "half": "1:0"}   # half 转冒号
    assert res["周一002"] == {"score": "0:0", "half": None}    # 无 half（ESPN 链路）→ None


def test_cmd_settle_loops_all_cards(tmp_path, monkeypatch, capsys):
    """循环结算：旧卡（腿已完赛）捞回补结算、未完赛卡跳过、已结算卡幂等。
    2026-08-28 教训：只取最新一张时 08-27 卡被 08-28 卡顶住永远轮不到结算；
    路径走 ROOT 后与 cwd 无关（run.py sh() cwd=engine/scripts 下裸相对 glob 落空）。"""
    import json as _json
    import boldplay
    pred = tmp_path / "data" / "03-predictions"; pred.mkdir(parents=True)
    res_dir = tmp_path / "data" / "02-results"; res_dir.mkdir(parents=True)
    (res_dir / "2026-08-27.json").write_text(_json.dumps({"date": "2026-08-27",
        "matches": [{"code": "周四001", "result": "4-1"}]}), encoding="utf-8")
    (pred / "2026-08-26-boldplay.json").write_text(_json.dumps(
        {"date": "2026-08-26", "totalCost": 4, "settle": {"payout": 0.0}, "tiers": {}}), encoding="utf-8")
    (pred / "2026-08-27-boldplay.json").write_text(_json.dumps(
        {"date": "2026-08-27", "totalCost": 4, "tiers": {"upset": {"cost": 4, "multiplier": 1,
          "legs": [{"matchNumStr": "周四001", "play": "had", "pick": "主胜", "odds": 1.98}]}}}), encoding="utf-8")
    (pred / "2026-08-28-boldplay.json").write_text(_json.dumps(
        {"date": "2026-08-28", "totalCost": 4, "tiers": {"upset": {"cost": 4, "multiplier": 1,
          "legs": [{"matchNumStr": "周五001", "play": "had", "pick": "主胜", "odds": 1.5}]}}}), encoding="utf-8")
    monkeypatch.setattr(boldplay, "ROOT", tmp_path)
    boldplay.cmd_settle()
    out = capsys.readouterr().out
    assert "已结算" in out and "赛果未回填" in out            # 幂等跳过 + 未完赛跳过
    settled = _json.loads((pred / "2026-08-27-boldplay.json").read_text(encoding="utf-8"))
    assert settled["settle"]["legHits"]["upset"] == [[True]]  # 旧卡被循环捞回结算
    pending = _json.loads((pred / "2026-08-28-boldplay.json").read_text(encoding="utf-8"))
    assert "settle" not in pending                             # 未完赛卡不写 settle
