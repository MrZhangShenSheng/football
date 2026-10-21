# engine/tests/test_llm_eval.py
import sys, json, pathlib

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from llm_eval import (market_devig_on_keys, actual_key, evaluate,
                      TAIL_ODDS_FLOOR, LOGLLOSS_CLIP)


def _full_pool():
    pool = {f"{i}:{j}": (8.0 if (i + j) <= 3 else 30.0) for i in range(6) for j in range(6)}
    pool.update({"other_h": 60.0, "other_d": 60.0, "other_a": 60.0})
    return pool


# 真实体彩 31 格池形状（28 定格 + 3 兜底·与 10-14 周三008 实卡一致）：
# 3:4 等不在定格内,须由 other_* 接住
_GRID_31 = ["0:0", "0:1", "0:2", "0:3", "0:4", "0:5", "1:0", "1:1", "1:2", "1:3",
            "1:4", "1:5", "2:0", "2:1", "2:2", "2:3", "2:4", "2:5", "3:0", "3:1",
            "3:2", "3:3", "4:0", "4:1", "4:2", "5:0", "5:1", "5:2"]


def _pool31():
    pool = {k: (8.0 if sum(map(int, k.split(":"))) <= 3 else 30.0) for k in _GRID_31}
    pool.update({"other_h": 60.0, "other_d": 60.0, "other_a": 60.0})
    return pool


@pytest.fixture()
def dirs_eval(tmp_path, monkeypatch):
    """装载侧 fixture：卷/卡/赛果三件套都落 tmp（_load_day_rows 走真实路径）。"""
    import llm_eval as le
    monkeypatch.setattr(le, "PREDS_ROOT", tmp_path / "preds")
    monkeypatch.setattr(le, "CARDS_ROOT", tmp_path / "cards")
    monkeypatch.setattr(le, "close_pool", lambda d, code, card: (_pool31(), "lineupLast"))
    monkeypatch.setattr(le, "_load_results", lambda days: {"周三009": "1:1"})
    (tmp_path / "preds").mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_actual_key_grid_and_other():
    assert actual_key("2:1") == "2:1"
    assert actual_key("0:3") == "0:3"
    assert actual_key("6:0") == "other_h"
    assert actual_key("2:6") == "other_a"


def test_market_devig_keys():
    keys = ["1:1", "1:0", "0:1"]
    p = market_devig_on_keys(_full_pool(), keys)
    assert set(p) == set(keys) and abs(sum(p.values()) - 1.0) < 1e-6


def test_evaluate_verdict_machinery(tmp_path, monkeypatch):
    # 构造 2 个销售日的 preds + 赛果 fixture，验证 PASS/INSUFFICIENT 逻辑跑通
    import llm_eval as le
    pool = _full_pool()
    day_rows = []
    for i in range(35):                       # n≥30 观察门槛
        code = f"二{i:03d}"
        p = {k: (0.05 if k != "1:1" else 0.2) for k in pool}
        day_rows.append({"code": code, "cardHash": "x" * 16,
                         "p_crs": p, "pRawSum": 1.0, "rawSumWarn": False,
                         "contextStatus": "ok", "keyJudgments": [], "confidence": "medium",
                         "_marketPool": pool, "_actual": "1:1", "_score": "1:1", "_closeProxy": "lineupLast"})
    monkeypatch.setattr(le, "_load_day_rows", lambda days: day_rows)
    out = evaluate(["2026-10-14"])
    assert out["primary"]["n"] == 35
    assert out["primary"]["verdict"] in ("PASS", "FAIL", "UNDETECTED", "INSUFFICIENT")
    assert "tailChannel" in out and "descriptive" in out


def test_kj_cohort_split(monkeypatch):
    """v3.4：判断口径分层留痕——v1 压缩卷（<3条）与 v2 拆分卷（≥3条）分开报,
    旧卷是凭证不改写,只在评测侧标注。非判据,descriptive 用。"""
    import llm_eval as le
    pool = _full_pool()
    rows = []
    for i in range(35):
        p = {k: (0.05 if k != "1:1" else 0.2) for k in pool}
        cohort = "v1-compressed" if i < 20 else "v2-split"
        rows.append({"code": f"二{i:03d}", "cardHash": "x" * 16, "p_crs": p,
                     "pRawSum": 1.0, "rawSumWarn": False, "contextStatus": "ok",
                     "keyJudgments": [], "confidence": "medium",
                     "_marketPool": pool, "_actual": "1:1", "_score": "1:1",
                     "_closeProxy": "lineupLast", "_kjCohort": cohort})
    monkeypatch.setattr(le, "_load_day_rows", lambda days: rows)
    out = evaluate(["2026-10-14"])
    ks = out["kjCohortSplit"]
    assert ks["v1-compressed"]["n"] == 20 and ks["v2-split"]["n"] == 15
    assert "diffMean" in ks["v1-compressed"]
    # 行缺口径标 → unknown 兜底（不炸）
    for r in rows:
        r.pop("_kjCohort")
    assert evaluate(["2026-10-14"])["kjCohortSplit"]["unknown"]["n"] == 35


def test_gen_cohort_split(monkeypatch):
    """v3.6：生成口径分层——手填卷(freehand)与结构化生成卷(v3-structured)分开报。
    两代同期并存时若混算,带内继承的改善会被手填卷的 003 型拖累掩盖。"""
    import llm_eval as le
    pool = _full_pool()
    rows = []
    for i in range(35):
        p = {k: (0.05 if k != "1:1" else 0.2) for k in pool}
        rows.append({"code": f"三{i:03d}", "cardHash": "x" * 16, "p_crs": p,
                     "pRawSum": 1.0, "rawSumWarn": False, "contextStatus": "ok",
                     "keyJudgments": [], "confidence": "medium",
                     "_marketPool": pool, "_actual": "1:1", "_score": "1:1",
                     "_closeProxy": "lineupLast",
                     "_genCohort": "freehand" if i < 20 else "v3-structured"})
    monkeypatch.setattr(le, "_load_day_rows", lambda days: rows)
    gs = evaluate(["2026-10-14"])["genCohortSplit"]
    assert gs["freehand"]["n"] == 20 and gs["v3-structured"]["n"] == 15
    assert "diffMean" in gs["v3-structured"]
    for r in rows:                                  # 行缺标 → freehand 兜底（旧卷默认）
        r.pop("_genCohort")
    assert evaluate(["2026-10-14"])["genCohortSplit"]["freehand"]["n"] == 35


def test_gen_cohort_read_from_volume(dirs_eval):
    """装载侧行级口径：direction 键在即 v3（卷级标记不参与,合卷不误涂）。"""
    import llm_eval as le
    for name, want in (("2026-11-01", "v3-structured"), ("2026-11-02", "freehand")):
        p = {k: 1.0 / len(_pool31()) for k in _pool31()}
        row = {"code": "周三009", "cardHash": "x" * 16, "p_crs": p,
               "pRawSum": 1.0, "rawSumWarn": False, "contextStatus": "ok",
               "keyJudgments": [], "confidence": "medium"}
        if want == "v3-structured":
            row["direction"] = {"h": .3, "d": .3, "a": .4}
        doc = {"chain": "llm-31grid-v3" if want == "v3-structured" else "llm-31grid-v2",
               "genCohort": want, "frozenAt": f"{name}T10:00:00", "preds": [row]}
        (le.PREDS_ROOT / f"{name}.json").write_text(
            json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        cd = le.CARDS_ROOT / name
        cd.mkdir(parents=True, exist_ok=True)
        (cd / "周三009.json").write_text(json.dumps(
            {"code": "周三009", "cardHash": "x" * 16,
             "layers": {"market": {"crs": _pool31()}, "context": {"status": "ok"}}},
            ensure_ascii=False), encoding="utf-8")
        rows = le._load_day_rows([name])
        assert rows and rows[0]["_genCohort"] == want, (name, rows[:1])


def test_results_scoped_per_sale_day_and_per_row_cohort(tmp_path, monkeypatch):
    """2026-10-21 双修复回归：
    ①赛果按销售日分桶——「周三001」每周复用,10-20 合卷的周三场若拿 10-15 那周
      的同编号赛果计分即毒化（实测 13 场假分,primary diffMean −0.885 全假）;
    ②genCohort 按行判——合卷里 v2 行不得被卷级 v3 标记误涂。"""
    import llm_eval as le
    res_root = tmp_path / "results"
    res_root.mkdir()
    monkeypatch.setattr(le, "PREDS_ROOT", tmp_path / "preds")
    monkeypatch.setattr(le, "CARDS_ROOT", tmp_path / "cards")
    monkeypatch.setattr(le, "RESULTS_ROOT", res_root)
    monkeypatch.setattr(le, "close_pool", lambda d, code, card: (_pool31(), "lineupLast"))
    (tmp_path / "preds").mkdir()
    (tmp_path / "cards").mkdir()

    def put_results(day, matches):
        (res_root / f"{day}.json").write_text(json.dumps(
            {"matches": matches}, ensure_ascii=False), encoding="utf-8")

    put_results("2026-10-15", [{"code": "周三001", "score": "3:3"}])      # 上周同编号
    put_results("2026-10-20", [{"code": "周二001", "score": "2:2"},
                               {"code": "周二002", "score": "1:2"}])
    monkeypatch.setattr(le, "RESULTS_ROOT", res_root)

    def put_card(day, code):
        d = le.CARDS_ROOT / day
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{code}.json").write_text(json.dumps(
            {"code": code, "cardHash": "x" * 16,
             "layers": {"market": {"crs": _pool31()}, "context": {"status": "ok"}}},
            ensure_ascii=False), encoding="utf-8")

    def put_pred(day, name, doc):
        (le.PREDS_ROOT / name).write_text(json.dumps(doc, ensure_ascii=False),
                                          encoding="utf-8")

    def pred(code, v3):
        p = {"code": code, "cardHash": "x" * 16, "pRawSum": 1.0, "rawSumWarn": False,
             "contextStatus": "ok", "keyJudgments": [], "confidence": "medium",
             "p_crs": {k: 1.0 / len(_pool31()) for k in _pool31()}}
        if v3:
            p["direction"] = {"h": .3, "d": .3, "a": .4}
        return p

    put_pred("2026-10-15", "2026-10-15.json",
             {"chain": "llm-31grid-v2", "preds": [pred("周三001", False)]})
    put_pred("2026-10-20", "2026-10-20-r1.json",
             {"chain": "llm-31grid-v2+llm-31grid-v3", "genCohort": "v3-structured",
              "preds": [pred("周二001", False), pred("周二002", True),
                        pred("周三001", True)]})       # 本周周三场·无赛果
    for day, codes in (("2026-10-15", ["周三001"]),
                       ("2026-10-20", ["周二001", "周二002", "周三001"])):
        for c in codes:
            put_card(day, c)

    rows = le._load_day_rows(["2026-10-15", "2026-10-20"])
    # ①本周周三001 不得吃到上周 3:3：全表只许一行周三001,且是 10-15 那行（v2 手填）
    wed = [r for r in rows if r["code"] == "周三001"]
    assert len(wed) == 1 and wed[0]["_score"] == "3:3"
    assert wed[0]["_genCohort"] == "freehand"
    # ②合卷行级 cohort：无 direction 的 v2 行=freehand,有 direction 的=v3
    r201 = next(r for r in rows if r["code"] == "周二001")
    r202 = next(r for r in rows if r["code"] == "周二002")
    assert r201["_genCohort"] == "freehand"
    assert r202["_genCohort"] == "v3-structured"
    assert r201["_score"] == "2:2" and r202["_score"] == "1:2"


def test_resolution_minpos_and_zerocells(monkeypatch):
    """v3.4：分布分辨率双口径。主看 minPos——实测周三/周四 minPos 卡 0.01 带 13~16
    零格、周五 0.00054 零格 0,zeroCells 只显示'14→0'看不出 20 倍细化幅度。"""
    import llm_eval as le
    pool = _full_pool()
    keys = list(pool)
    rows = []
    # 粗卷 10 场：末 14 格填 0,其余均分（刻度 1%）
    for i in range(10):
        p = {k: 0.0 for k in keys}
        for k in keys[:len(keys) - 14]:
            p[k] = 1.0 / (len(keys) - 14)
        rows.append({"code": f"三{i:03d}", "cardHash": "x" * 16, "p_crs": p,
                     "pRawSum": 1.0, "rawSumWarn": False, "contextStatus": "ok",
                     "keyJudgments": [], "confidence": "low", "_marketPool": pool,
                     "_actual": keys[0], "_score": "1:1", "_closeProxy": "lineupLast"})
    # 细卷 10 场：全格有正值,尾巴压到 5e-4
    for i in range(10):
        p = {k: 5e-4 for k in keys}
        p[keys[0]] = 1.0 - 5e-4 * (len(keys) - 1)
        rows.append({"code": f"五{i:03d}", "cardHash": "x" * 16, "p_crs": p,
                     "pRawSum": 1.0, "rawSumWarn": False, "contextStatus": "ok",
                     "keyJudgments": [], "confidence": "low", "_marketPool": pool,
                     "_actual": keys[0], "_score": "1:1", "_closeProxy": "lineupLast"})
    monkeypatch.setattr(le, "_load_day_rows", lambda days: rows)
    out = evaluate(["2026-10-14"])
    res = out["resolution"]
    assert res["n"] == 20 and res["zeroFreeScenes"] == 10
    assert res["zeroCellsMax"] == 14 and res["missingKeysTotal"] == 0
    three, five = res["byDay"]["三0"], res["byDay"]["五0"]
    assert three["zeroCellsMean"] == 14.0 and five["zeroCellsMean"] == 0.0
    # minPos 分辨出 20 倍差（zeroCells 看不出的那部分）
    assert five["minPosMedian"] < three["minPosMedian"] / 10
    assert three["zeroMktMassMean"] > 0        # 零格在市场眼里有真实概率质量


def test_pool_outside_score_goes_other(monkeypatch):
    """v3.4 修误杀：池外比分（3:4）本该由 other_a 接住并正常计分,不该整场跳。
    原 bug=守卫查裸键 actual_key(sc)、计分用池感知键 actual_key(sc,pool),两键不同源
    → 周三008 被误杀,n 悄悄 38→37。"""
    import llm_eval as le
    pool = _pool31()                                    # 真实 31 格池（无 3:4）
    assert "3:4" not in pool and "other_a" in pool      # 前提：池无此格,有客胜兜底
    p = {k: 1.0 / len(pool) for k in pool}
    row = {"code": "三008", "cardHash": "x" * 16, "p_crs": p, "pRawSum": 1.0,
           "rawSumWarn": False, "contextStatus": "ok", "keyJudgments": [],
           "confidence": "low", "_marketPool": pool,
           "_actual": "3:4", "_score": "3:4", "_closeProxy": "lineupLast"}
    monkeypatch.setattr(le, "_load_day_rows", lambda days: [row])
    out = evaluate(["2026-10-14"])
    assert out["primary"]["n"] == 1 and out["skipped"]["n"] == 0


def test_skipped_not_silent(monkeypatch):
    """v3.4：真跳过（市场池不足）禁静默,逐场留痕使 n 的缺口可追。"""
    import llm_eval as le
    pool = _full_pool()
    p = {k: 1.0 / len(pool) for k in pool}
    good = {"code": "三001", "cardHash": "x" * 16, "p_crs": p, "pRawSum": 1.0,
            "rawSumWarn": False, "contextStatus": "ok", "keyJudgments": [],
            "confidence": "low", "_marketPool": pool, "_actual": list(pool)[0],
            "_score": "1:1", "_closeProxy": "lineupLast"}
    thin = {k: pool[k] for k in list(pool)[:3]}         # 池不足（< min_pool_items）
    bad = dict(good, code="三009", _marketPool=thin, p_crs={k: 1 / 3 for k in thin})
    monkeypatch.setattr(le, "_load_day_rows", lambda days: [good, bad])
    out = evaluate(["2026-10-14"])
    assert out["primary"]["n"] == 1                  # 只计分 1 场
    assert out["skipped"]["n"] == 1
    s = out["skipped"]["rows"][0]
    assert s["code"] == "三009" and "池" in s["reason"]
    assert out["resolution"]["n"] == 2               # 分辨率含跳过场（与计分无关）


def test_verdict_gates_locked():
    """review fix：n<formal 一律 INSUFFICIENT 不判定；tail PASS 锁 n≥150（边界四例）。"""
    import llm_eval as le
    orig = le._boot_ci_diff
    le._boot_ci_diff = lambda d, seed=0, n_boot=0: [1.0, 2.0]   # CI 下限恒>0（诱 PASS）
    try:
        rows99 = [0.01] * 99
        r99 = le.evaluate.__wrapped__(["99场"]) if hasattr(le.evaluate, "__wrapped__") else None
        # 直接构造 verdict 语义（经 evaluate 内嵌函数不可达，用等价逻辑验证闸门函数行为）
        # —— 通过 evaluate 的 monkeypatch 装载器复现四边界
        def rows_of(n):
            return [{"code": f"c{i}", "cardHash": "x" * 16,
                     "p_crs": {"1:1": 0.5}, "pRawSum": 1.0, "rawSumWarn": False,
                     "contextStatus": "ok", "keyJudgments": [], "confidence": "medium",
                     "_marketPool": {"1:1": 3.0, "1:0": 3.0, "0:1": 3.0, "0:0": 3.0,
                                     **{f"{i}:{j}": 30.0 for i in range(6) for j in range(6)
                                        if not (i <= 1 and j <= 1)},
                                     "胜其他": 60.0, "平其他": 60.0, "负其他": 60.0},
                     "_actual": "1:1", "_closeProxy": "lineupLast", "_score": "1:1"}
                    for i in range(n)]
        le._load_day_rows = lambda days: rows_of(99)
        out99 = le.evaluate(["x"])
        assert out99["primary"]["verdict"] == "INSUFFICIENT"      # n=99 <100 不判定
        assert "diffMean" in out99["primary"]                     # 观察数字照出
        le._load_day_rows = lambda days: rows_of(100)
        out100 = le.evaluate(["x"])
        assert out100["primary"]["verdict"] == "PASS"             # n=100 CI 下限>0 → PASS
        le._load_day_rows = lambda days: rows_of(149)
        out149 = le.evaluate(["x"])
        assert out149["tailChannel"]["verdict"] == "INSUFFICIENT"  # tail n=149 <150 锁
        le._load_day_rows = lambda days: rows_of(150)
        out150 = le.evaluate(["x"])
        assert out150["tailChannel"]["verdict"] == "PASS"          # n=150 开闸
    finally:
        le._boot_ci_diff = orig


def test_actual_key_pool_aware():
    """终审 fix：未定格键（4:3/4:4/5:3...真实31格不含）按方向落 other_*，不再静默丢行。"""
    import llm_eval as le
    pool = {"1:1": 7.0, "4:3": 0}                     # 4:3 无价（真实存档不含此格）
    assert le.actual_key("1:1", pool) == "1:1"
    assert le.actual_key("4:3", pool) == "other_h"    # 有比分无价→落 other
    assert le.actual_key("4:4", pool) == "other_d"
    assert le.actual_key("3:4", pool) == "other_a"
    assert le.actual_key("6:0", pool) == "other_h"
    assert le.actual_key("4:3") == "4:3"              # pool=None 旧行为保留
