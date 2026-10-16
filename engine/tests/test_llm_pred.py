# engine/tests/test_llm_pred.py
import sys, json, pathlib, pytest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from llm_pred import validate_pred, normalize_to_keys, freeze


def test_validate_and_issues():
    keys = ["1:1", "1:0", "0:1"]
    ok = {"code": "周二001", "p_crs": {"1:1": 0.5, "1:0": 0.3, "0:1": 0.2},
          "keyJudgments": [{"judgment": "j", "evidence": "语境层#1"}], "confidence": "medium"}
    good, issues = validate_pred(ok, keys)
    assert good and not issues
    bad = dict(ok, p_crs={"1:1": 0.9, "9:9": 0.1})                    # 9:9 不在键集
    good2, issues2 = validate_pred(bad, keys)
    assert not good2 and any("9:9" in i for i in issues2)
    bad2 = dict(ok, keyJudgments=[])                                   # 无判断
    good3, issues3 = validate_pred(bad2, keys)
    assert not good3


def test_normalize_to_keys():
    p, raw_sum = normalize_to_keys({"1:1": 0.5, "1:0": 0.25, "0:1": 0.25, "2:2": 0.5},
                                   ["1:1", "1:0", "0:1"])
    assert abs(raw_sum - 1.5) < 1e-9
    assert abs(sum(p.values()) - 1.0) < 1e-9 and "2:2" not in p


def test_freeze_idempotent_and_strict(tmp_path, monkeypatch):
    monkeypatch.setattr("llm_pred.PREDS_ROOT", tmp_path)
    # 造卡（freeze 校验 cardHash 链）
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
    from llm_card import build_card, save_card
    # fixture 修正：迷你完整 crs 池（i,j∈0..4 全积 25 s 键 + other 三键 = 28 键），
    # 预测 p_crs 用其中 3 键（1:1=s01s01 / 1:0=s01s00 / 0:1=s00s01）——真实卡为 s 键，
    # freeze 的 sXXsYY→pick 转换后键集须完整覆盖预测键集
    crs = {f"s{i:02d}s{j:02d}": 5.0 + i + j for i in range(5) for j in range(5)}
    crs.update({"s1sh": 30.0, "s1sd": 30.0, "s1sa": 30.0})
    card = build_card("2026-10-13",
                      {"matchNumStr": "周二001", "league": "英超", "home": "A", "away": "B",
                       "matchTime": "t", "had": {}, "crs": crs, "ttg": {}},
                      insight_path=None, lineups={}, now="2026-10-13T18:00:00")
    save_card(card, "2026-10-13", root=tmp_path / "cards")
    monkeypatch.setattr("llm_pred.CARDS_ROOT", tmp_path / "cards")
    pred = {"code": "周二001", "p_crs": {"1:1": 0.5, "1:0": 0.3, "0:1": 0.2},
            "keyJudgments": [{"judgment": "j", "evidence": "e"}], "confidence": "medium"}
    p1 = freeze("2026-10-13", [pred])
    assert p1.exists()
    p2 = freeze("2026-10-13", [dict(pred, confidence="high")])     # 幂等不覆写
    doc = json.loads(p1.read_text(encoding="utf-8"))
    assert doc["preds"][0]["confidence"] == "medium"
    assert len(doc["preds"][0]["cardHash"]) == 16


def test_llm_family_build_ticket():
    """L 族 v2 停用回归锁（2026-10-16 大哥拍板淘汰）。

    原测试锁的是 v2 选腿行为（右尾 top1 降序选场 + 每场 top2 双选 → 4 注 2串1）。
    v2 已淘汰：实测右尾选腿与模型最看好格交集 0/22，@30 赔率阈值筛候选池等于『只准押
    市场认为不会发生的事』（R022）。此处转为锁死停用状态，防 spec 被误恢复；
    v3 自主判断轨的出票口径（Q1）待拍板，定后另立测试。"""
    import importlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "shadow"))
    shapes = importlib.import_module("shapes")
    assert not [s for s in shapes.PLAN_FAMILIES if s.get("family") == "L"], \
        "L 族 spec 已停用(R022)；若需恢复出票须先拍板 Q1 出票口径并过铁律 15 准入"
    assert not [s for s in shapes.PLAN_FAMILIES if s.get("pool") == "llm_crs"]
    # 其余 15 票型不受牵连（停用须是外科式的）
    assert len(shapes.PLAN_FAMILIES) == 15


def test_chinese_other_keys_mapped():
    """pick 键存档的中文 other 键（胜/平/负其他）须映射为 other_* 可押（review fix·右尾点名肥档）。"""
    import llm_pred as le
    card = {"matchNumStr": "周二001", "league": "英超", "home": "A", "away": "B",
            "matchTime": "t", "had": {},
            "crs": {**{f"{i}:{j}": 12.0 for i in range(6) for j in range(6)},
                    "胜其他": 60.0, "平其他": 60.0, "负其他": 60.0},
            "ttg": {}}
    import pathlib, sys, json
    cards_dir = pathlib.Path(le.CARDS_ROOT) / "2026-10-14z"
    cards_dir.mkdir(parents=True, exist_ok=True)
    from llm_card import build_card, save_card
    c = build_card("2026-10-14z", card, insight_path=None, lineups={}, now="2026-10-14T10:00:00")
    save_card(c, "2026-10-14z", root=pathlib.Path(le.CARDS_ROOT))
    pred = {"code": "周二001",
            "p_crs": {**{f"{i}:{j}": 0.02 for i in range(3) for j in range(3)},
                      "other_h": 0.3, "other_d": 0.1, "other_a": 0.1},
            "keyJudgments": [{"judgment": "j", "evidence": "e"}], "confidence": "low"}
    out = pathlib.Path(le.PREDS_ROOT) / "2026-10-14z.json"
    if out.exists():
        out.unlink()
    p = le.freeze("2026-10-14z", [pred])
    doc = json.loads(p.read_text(encoding="utf-8"))
    assert "other_h" in doc["preds"][0]["p_crs"] and abs(sum(doc["preds"][0]["p_crs"].values()) - 1.0) < 1e-9
    # 清理测试产物
    out.unlink()
    (pathlib.Path(le.CARDS_ROOT) / "2026-10-14z" / "周二001.json").unlink()
    (pathlib.Path(le.CARDS_ROOT) / "2026-10-14z").rmdir()


def test_llm_other_pick_e2e_freeze_and_settle(tmp_path, monkeypatch):
    """review fix·HIGH 端到端：L 族 other_h 选腿全程打通——建票 picks 含 other_h →
    shadow_all 覆盖层冻到实盘池 s1sh 真实价 → settle 赛果 6:0（outcome=0 主胜，6:0 属
    胜其他命中域）→ payout>0。复现路径=reviewer：pools.crs 体彩原始键 s1sh，L 族 picks
    归一 pick 键 other_h——覆盖层缺 s1sh→other_h 映射则冻不到价、_pick_price 缺 other_*
    分支则结算恒 miss。开发者 sszhang"""
    import importlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "shadow"))
    paper = importlib.import_module("paper")
    shapes = importlib.import_module("shapes")
    import llm_pred as le
    from llm_card import build_card, save_card
    import subprocess

    DATE = "2026-10-13"
    # ① 数据卡（市场层 crs 含 s1sh=30.0 真实价）+ llm 冻结预测（p_crs 含 other_h）
    crs = {f"s{i:02d}s{j:02d}": 5.0 + i + j for i in range(5) for j in range(5)}
    crs.update({"s1sh": 30.0, "s1sd": 30.0, "s1sa": 30.0, "s00s03": 40.0})
    cards_root = tmp_path / "cards"
    preds = []
    for i in range(1, 5):
        m = {"matchNumStr": f"周二{i:03d}", "league": "英超", "home": f"A{i}", "away": f"B{i}",
             "matchTime": "t", "had": {"h": 1.2 + 0.1 * i, "d": 5.0, "a": 9.0}, "crs": dict(crs), "ttg": {}}
        save_card(build_card(DATE, m, insight_path=None, lineups={}, now="2026-10-13T10:00:00"),
                  DATE, root=cards_root)
        preds.append({"code": f"周二{i:03d}",
                      "p_crs": {"other_h": 0.12, "0:3": 0.10, "1:1": 0.50, "1:0": 0.28},
                      "keyJudgments": [{"judgment": "j", "evidence": "e"}], "confidence": "low"})
    monkeypatch.setattr(le, "CARDS_ROOT", cards_root)
    monkeypatch.setattr(le, "PREDS_ROOT", tmp_path / "preds")
    le.freeze(DATE, preds)                       # 真实冻结链：卡键集校验+归一落盘
    # ② shadow_all 全链（假 sporttery 清单+假 REPO；qcache/v4b 快照缺→诚实跳过，subprocess 禁跑）
    fake_repo = tmp_path / "repo"
    (fake_repo / "engine" / "cache").mkdir(parents=True)
    (fake_repo / "engine" / "cache" / "sporttery_matches.json").write_text(json.dumps({"matches": [
        {"code": f"周二{i:03d}", "matchDate": DATE, "home": f"A{i}", "away": f"B{i}", "league": "英超",
         "had": {"h": 1.2 + 0.1 * i, "d": 5.0, "a": 9.0}, "crs": dict(crs), "ttg": {"s3": 4.0}}
        for i in range(1, 5)]}), encoding="utf-8")
    monkeypatch.setattr(paper, "REPO", str(fake_repo))
    monkeypatch.setattr(paper, "LLM_PREDS_DIR", str(tmp_path / "preds"))
    monkeypatch.setattr(paper, "LLM_CARDS_DIR", str(cards_root))
    monkeypatch.setattr(shapes, "load_qcache", lambda d: {"x": {}})     # 非 None → 不触发 freq_snap 生成
    monkeypatch.setattr(shapes, "load_v4b_snap", lambda d: None)        # v4b 缺快照 → subprocess 被禁后降级跳过
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("test-no-subproc")))
    ledger = tmp_path / "paper_tickets.json"
    out = paper.shadow_all(DATE, out=ledger)
    # ③ v2 停用后的回归锁（2026-10-16 大哥拍板淘汰·R022）：
    #    冻结链(freeze)与装载(load_llm_preds)仍须工作——v3 自主判断轨继续复用；
    #    但 L 族不再产任何票（spec 已注释停用），此处锁死防误恢复。
    assert not any(t["spec_name"] == "llm-31格选腿" for t in paper.load_tickets(ledger)), \
        "L 族 v2 已淘汰(R022 赔率阈值筛候选池)，不应再产票"
    assert paper.load_llm_preds(DATE) is not None                  # 装载链仍可用（v3 复用）
    qc = paper.load_llm_preds(DATE)
    assert len(qc) == 4 and all("crs" in v for v in qc.values())
    # other 键三态归一仍须正确（v3 出票口径待定，但键口径是公共地基）
    oh = [k for k, _ in qc["周二001"]["crs"]]
    assert "other_h" in oh                                         # s1sh→other_h 归一未坏
