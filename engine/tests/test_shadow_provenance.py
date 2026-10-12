# engine/tests/test_shadow_provenance.py
# -*- coding: utf-8 -*-
"""回溯机制四件套测试（2026-10-12 大哥拍板）：链籍账本/腿级明细落盘/why/annotate。
开发者 sszhang"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "shadow"))
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import paper  # noqa: E402
import provenance  # noqa: E402


# ── 链籍账本 ──
def test_resolve_epoch_boundaries(tmp_path):
    reg = tmp_path / "chain_registry.json"
    reg.write_text(json.dumps({"chains": {"shadow_qcache": {"epochs": [
        {"from": "2026-09-08", "to": "2026-10-12", "engine": "freq_band v1"},
        {"from": "2026-10-13", "engine": "V3W-v2"}]}}}), encoding="utf-8")
    assert provenance.resolve_epoch("shadow_qcache", "2026-09-08", reg)["engine"] == "freq_band v1"
    assert provenance.resolve_epoch("shadow_qcache", "2026-10-12", reg)["engine"] == "freq_band v1"
    assert provenance.resolve_epoch("shadow_qcache", "2026-10-13", reg)["engine"] == "V3W-v2"
    assert provenance.resolve_epoch("shadow_qcache", "2026-09-07", reg) is None   # 建账前
    assert provenance.resolve_epoch("no_such_chain", "2026-10-13", reg) is None


def test_stamp_for_spec_by_pool(tmp_path):
    reg = tmp_path / "chain_registry.json"
    reg.write_text(json.dumps({"chains": {"market_devig": {"epochs": [
        {"from": "2026-09-08", "engine": "体彩去水"}]}}}), encoding="utf-8")
    st = provenance.stamp_for_spec({"pool": "strong"}, "2026-10-01", reg)
    assert st == {"chain": "market_devig", "engine": "体彩去水", "since": "2026-09-08"}
    assert provenance.stamp_for_spec({"pool": "crs"}, "2026-10-01", reg) is None  # registry 无该链


def test_stamp_for_spec_by_name_v18(tmp_path):
    """独立执行器 spec（v18·不在 PLAN_FAMILIES）按 spec_name 直查链籍。"""
    reg = tmp_path / "chain_registry.json"
    reg.write_text(json.dumps({"chains": {"shadow_v18": {"epochs": [
        {"from": "2026-10-07", "engine": "市场置信+V3W双信号"}]}}}), encoding="utf-8")
    st = provenance.stamp_for_spec({"name": "v18双信号N串1"}, "2026-10-13", reg)
    assert st == {"chain": "shadow_v18", "engine": "市场置信+V3W双信号", "since": "2026-10-07"}
    assert provenance.stamp_for_spec({"name": "不存在"}, "2026-10-13", reg) is None


def test_register_stamps_chain(tmp_path):
    ledger = tmp_path / "p.json"
    t = paper.register("4串1", "2026-10-13", [
        {"code": "周一001", "match": "A vs B", "market": "had", "pick": [0], "odds": [1.5, None, None]}],
        bets=[{"legs": [[0, 0]]}], mult=1, cost=2, path=ledger,
        chain={"chain": "market_devig", "engine": "体彩去水", "since": "2026-09-08"})
    assert t["chain"]["engine"] == "体彩去水"
    assert json.loads(ledger.read_text(encoding="utf-8"))[-1]["chain"]["chain"] == "market_devig"


# ── 腿级明细落盘 ──
def _mk_ticket(path, legs_spec):
    return paper.register("t", "2026-10-01", [
        {"code": c, "match": f"M{i}", "market": mk, "pick": pk, "odds": od}
        for i, (c, mk, pk, od) in enumerate(legs_spec)],
        bets=[{"legs": [[i, p[0]] for i, p in enumerate(
            [pk for _, _, pk, _ in legs_spec])]}], mult=1, cost=2, path=path)


def test_settle_all_annotates_legs(tmp_path):
    import shapes
    ledger = tmp_path / "p.json"
    _mk_ticket(ledger, [
        ("周一001", "had", [0], [2.0, 3.0, 3.5]),
        ("周一002", "crs", ["1:1"], {"1:1": 8.0, "0:0": 9.0}),
    ])
    rr = {"周一001": (0, (2, 0)), "周一002": (1, (1, 1))}
    settled = paper.settle_all(rr, date="2026-10-01", path=ledger)
    assert len(settled) == 1
    legs = settled[0]["legs"]
    assert legs[0]["result"] == "hit" and legs[0]["actual"] == "2:0" and legs[0]["hitPrice"] == 2.0
    assert legs[1]["result"] == "hit" and legs[1]["actual"] == "1:1" and legs[1]["hitPrice"] == 8.0
    # miss 分支
    ledger2 = tmp_path / "p2.json"
    _mk_ticket(ledger2, [("周一003", "had", [0], [2.0, 3.0, 3.5])])
    paper.settle_all({"周一003": (2, (0, 1))}, date="2026-10-01", path=ledger2)
    lg = paper.load_tickets(ledger2)[0]["legs"][0]
    assert lg["result"] == "miss" and lg["actual"] == "0:1"


def test_annotate_backfills_settled_history(tmp_path, monkeypatch):
    """已结算但腿无明细的历史票 → annotate 按票自身 date 取赛果补齐（幂等）。"""
    ledger = tmp_path / "p.json"
    _mk_ticket(ledger, [("周日001", "crs", ["1:0"], {"1:0": 7.0, "1:1": 8.0})])
    tickets = paper.load_tickets(ledger)
    tickets[0]["result"], tickets[0]["payout"] = "settled", 14.0   # 模拟历史票：已结算无腿级
    paper._save(tickets, ledger)
    monkeypatch.setattr(paper, "_all_rounds_by_day",
                        lambda: {"2026-09-30": [{"code": "周日001", "outcome": 0, "score": (1, 0)}]})
    n = paper.annotate(ledger)
    assert n == 1
    lg = paper.load_tickets(ledger)[0]["legs"][0]
    assert lg["result"] == "hit" and lg["actual"] == "1:0" and lg["hitPrice"] == 7.0
    assert paper.annotate(ledger) == 0                 # 幂等


def test_settle_no_crossweek_misannotate(tmp_path):
    """显式 date 窗口：他日已结算旧票同 code 不被本窗赛果补写（跨周同编号守卫）。"""
    ledger = tmp_path / "p.json"
    t = _mk_ticket(ledger, [("周日001", "had", [0], [2.0, 3.0, 3.5])])
    t["result"], t["payout"] = "settled", 0.0
    paper._save(paper.load_tickets(ledger), ledger)
    paper.settle_all({"周日001": (2, (0, 1))}, date="2026-10-11", path=ledger)
    lg = paper.load_tickets(ledger)[0]["legs"][0]
    assert lg.get("result") is None                    # 票日 2026-10-01 ≠ 窗口 10-11 不动


# ── freq_snap v3w 契约 ──
def test_freq_snap_v3w_contract(tmp_path, monkeypatch):
    """predict_fn 注入 fake V3W → 产出契约：crs 'h:a' 键 / ttg s0~s7（s8 并 s7）/ 映射不到跳过。"""
    import freq_snap

    def fake_pred(hid, aid, as_of, ctx, memo, beta=0.05):
        if hid == "no-matrix":
            return None
        return {"matrix": {"s01s00": 0.20, "s01s01": 0.15, "s06s06": 0.05},
                "ttg": {"s0": 0.1, "s1": 0.3, "s7": 0.05, "s8": 0.02}}

    monkeypatch.setattr(freq_snap, "_zh_map", lambda: {"甲队": "t1", "乙队": "t2", "丙队": "no-matrix"})
    monkeypatch.setattr("strength_chain_prod.LEAGUES", ["fake"], raising=False)
    import strength_loaders
    monkeypatch.setattr(strength_loaders, "build_ctx", lambda *a, **k: {})
    ms = [
        {"code": "周一001", "matchDate": "2026-10-13", "home": "甲队", "away": "乙队",
         "crs": {"s01s00": 7.0, "s01s01": 8.0}, "ttg": {"s1": 3.5, "s7": 13.0}},
        {"code": "周一002", "matchDate": "2026-10-13", "home": "丙队", "away": "甲队",
         "crs": {"s01s00": 6.0}, "ttg": {"s1": 3.0}},   # 模型 None → 跳过
    ]
    qc = freq_snap.snap("2026-10-13", matches=ms, out_dir=tmp_path,
                        engine="v3w", predict_fn=fake_pred, force=True)
    assert set(qc) == {"周一001"}
    assert qc["周一001"]["crs"] == [["1:0", 0.2], ["1:1", 0.15]]
    assert qc["周一001"]["ttg"] == [["s1", 0.3], ["s7", 0.07]]   # s8(0.02) 并入 s7


def test_freq_snap_frozen_semantics(tmp_path):
    """文件在即返回（冻结语义）：已有快照不被 --engine 换代重写。"""
    import freq_snap
    q = tmp_path / "2026-10-13.json"
    q.write_text(json.dumps({"周一001": {"crs": [["1:0", 0.5]]}}), encoding="utf-8")
    qc = freq_snap.snap("2026-10-13", matches=[{"code": "x", "matchDate": "nope"}],
                        out_dir=tmp_path)              # 无匹配场次也不重建
    assert qc == {"周一001": {"crs": [["1:0", 0.5]]}}


# ── restamp / 分链战绩 ──
def test_restamp_and_spec_chain_stats(tmp_path, monkeypatch):
    ledger = tmp_path / "p.json"
    paper.register("4串1", "2026-10-01", [                     # spec 名须在 PLAN_FAMILIES 内
        {"code": "周一001", "match": "A", "market": "had", "pick": [0], "odds": [1.5, 3.0, 4.0]}],
        bets=[{"legs": [[0, 0]]}], mult=1, cost=2, path=ledger)
    tickets = paper.load_tickets(ledger)
    tickets[0]["result"], tickets[0]["payout"] = "settled", 6.0
    paper._save(tickets, ledger)
    reg = tmp_path / "chain_registry.json"
    reg.write_text(json.dumps({"chains": {"market_devig": {"epochs": [
        {"from": "2026-09-08", "engine": "体彩去水"}]}}}), encoding="utf-8")
    monkeypatch.setattr(provenance, "REGISTRY_PATH", str(reg))
    import shapes
    assert shapes.PLAN_FAMILIES          # shapes 可用（懒加载）
    n = paper.restamp(ledger)
    assert n == 1
    t = paper.load_tickets(ledger)[0]
    assert t["chain"]["engine"] == "体彩去水"
    stats = paper.spec_chain_stats(paper.load_tickets(ledger))
    assert stats[("4串1", "体彩去水")]["payout"] == 6.0
    assert paper.restamp(ledger) == 0     # 幂等
