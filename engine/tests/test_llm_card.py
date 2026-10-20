# engine/tests/test_llm_card.py
import sys, json, pathlib, pytest
from pathlib import Path as _Path
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from llm_card import build_card, save_card, card_hash, card_paths, match_id_index, write_context


def _mini_archive(tmp_path):
    m = {"matchNumStr": "周二001", "league": "英超", "home": "阿森纳", "away": "切尔西",
         "matchTime": "2026-10-14 03:45", "sellStatus": 1,
         "had": {"h": 1.5, "d": 4.0, "a": 5.0},
         "crs": {"s01s00": 8.0, "s01s01": 7.0}, "ttg": {"s0": 3.0}, "hafu": {}, "hhad": {}}
    doc = {"fetchedAt": "2026-10-13T10:00:00", "source": "sporttery",
           "matchDays": [{"businessDate": "2026-10-13", "matches": [m]}]}
    p = tmp_path / "2026-10-13.json"
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return p


def test_build_card_layers_and_hash(tmp_path):
    card = build_card("2026-10-13",
                      {"matchNumStr": "周二001", "league": "英超", "home": "阿森纳",
                       "away": "切尔西", "matchTime": "2026-10-14 03:45",
                       "had": {"h": 1.5, "d": 4.0, "a": 5.0},
                       "crs": {"s01s00": 8.0}, "ttg": {"s0": 3.0}},
                      insight_path=None, lineups={})
    assert card["code"] == "周二001"
    # v3 厚卡七层（2026-10-16 ourModels 接入·llm-autonomy-prereg card.layers）
    assert set(card["layers"]) == {"market", "intel", "profile", "lineup", "context",
                                   "ourModels"}
    assert card["layers"]["market"]["crs"] == {"s01s00": 8.0}
    assert card["layers"]["intel"]["status"] == "未获取"          # insight_path=None 诚实降级
    assert card["layers"]["context"] == {"status": "pending", "entries": []}
    assert len(card_hash(card)) == 16
    # 同卡同 hash（createdAt 固定注入时）
    c2 = dict(card); c2["layers"] = dict(card["layers"])
    assert card_hash(c2) == card_hash(card)


def test_v3_devig_layer_and_honest_gaps():
    """v3 厚卡：去水概率算出 + 缺源诚实标注（prereg card.honestGaps·禁空值伪装）。"""
    card = build_card("2026-10-13",
                      {"matchNumStr": "周二001", "league": "英超", "home": "A", "away": "B",
                       "matchTime": "2026-10-14 03:45",
                       "had": {"h": 2.0, "d": 4.0, "a": 4.0},
                       "hhad": {"h": 1.8, "d": 3.5, "a": 4.5},
                       "crs": {"s01s00": 8.0}, "ttg": {"s0": 3.0, "s1": 3.0}},
                      insight_path=None, lineups={})
    dv = card["layers"]["market"]["devig"]
    # had 去水：1/2 : 1/4 : 1/4 → 0.5/0.25/0.25（抽水剥离后归一）
    assert dv["had"] == {"h": 0.5, "d": 0.25, "a": 0.25}
    assert abs(sum(dv["ttg"].values()) - 1.0) < 1e-6
    assert dv["crs"] is None                  # 仅 1 键 <20 → 不足以去水,诚实 null
    assert dv["hafu"] is None                 # 该池未给 → null 而非 {}
    # ourModels：无对应日期产出 → 标注未获取,不伪造
    om = card["layers"]["ourModels"]
    assert om["v3wChain"]["status"] in ("未获取", "该场未在比分卡", "读取失败")
    assert om["v4b"]["status"] in ("未获取", "该场未在 v4b", "不可算", "读取失败")


def test_save_card_idempotent_no_overwrite(tmp_path):
    card = build_card("2026-10-13",
                      {"matchNumStr": "周二001", "league": "英超", "home": "A", "away": "B",
                       "matchTime": "t", "had": {}, "crs": {}, "ttg": {}},
                      insight_path=None, lineups={})
    p1 = save_card(card, "2026-10-13", root=tmp_path)
    # 篡改后再存 → 不覆写，返回原路径
    card["layers"]["market"]["crs"] = {"HACKED": 1.0}
    p2 = save_card(card, "2026-10-13", root=tmp_path)
    assert p1 == p2
    assert "HACKED" not in json.loads(p1.read_text(encoding="utf-8"))["layers"]["market"]["crs"]


def test_match_id_index_reverse_lookup(tmp_path):
    # 迷你 sporttery_matches.json：每场含 matchId+code，code=matchNumStr 口径
    doc = {"fetchedAt": "2026-10-13T10:00:00", "source": "sporttery",
           "matches": [{"matchId": 2041921, "code": "周一001", "home": "A", "away": "B"},
                       {"matchId": 2041922, "code": "周一002", "home": "C", "away": "D"},
                       {"code": "周一003"},                       # 无 matchId → 跳过
                       {"matchId": 2041924}]}                     # 无 code → 跳过
    f = tmp_path / "sporttery_matches.json"
    f.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    assert match_id_index(f) == {"周一001": 2041921, "周一002": 2041922}
    # 文件缺失 → 空 dict（gen 里 mid=None → intel 层诚实降级"未获取"）
    assert match_id_index(tmp_path / "nope.json") == {}


def _mk_card(tmp_path):
    card = build_card("2026-10-13",
                      {"matchNumStr": "周二001", "league": "英超", "home": "A", "away": "B",
                       # 真实时刻（原为占位 "t"：时间闸以 kickoff 为界后须可解析）
                       "matchTime": "2026-10-14 03:45", "had": {}, "crs": {}, "ttg": {}},
                      insight_path=None, lineups={}, now="2026-10-13T18:00:00")
    save_card(card, "2026-10-13", root=tmp_path)
    return card


def test_context_filters_post_match(tmp_path):
    """时间闸以开赛时刻为界（2026-10-16 修）：赛前消息一律收,不论是否晚于建卡。

    卡 createdAt=2026-10-13T18:00:00、kickoff=2026-10-14 03:45。
    旧口径按 createdAt 卡,会把 19:00 发布的赛前情报误丢——而伤停/首发消息多在赛前数小时
    才出,那正是最有价值的部分（实测 10-16 十一场 context 全 pending 的成因之一）。"""
    _mk_card(tmp_path)
    entries = [
        {"title": "Team news: key midfielder doubts", "url": "u1",
         "publishedAt": "2026-10-13T12:00:00", "excerpt": "preview of rotation plans"},   # 收
        {"title": "FT: Arsenal 3-0 Chelsea", "url": "u2",
         "publishedAt": "2026-10-13T12:00:00", "excerpt": "full-time report"},           # 赛后词→丢
        {"title": "Late team news", "url": "u3",
         "publishedAt": "2026-10-13T19:00:00", "excerpt": "fine"},                 # 晚于建卡但赛前→收
        {"title": "Lineups confirmed", "url": "u4",
         "publishedAt": "2026-10-14T03:50:00", "excerpt": "fine"},                 # 开赛后→丢(泄漏)
    ]
    r = write_context("2026-10-13", "周二001", entries, root=tmp_path)
    assert r["accepted"] == 2 and len(r["dropped"]) == 2
    urls = {e["url"] for e in json.loads(
        (tmp_path / "2026-10-13" / "周二001.json").read_text(encoding="utf-8")
    )["layers"]["context"]["entries"]}
    assert urls == {"u1", "u3"}                       # 赛前两条收,赛后词+开赛后各丢一条
    card = json.loads((tmp_path / "2026-10-13" / "周二001.json").read_text(encoding="utf-8"))
    assert card["layers"]["context"]["status"] == "ok"


def test_kickoff_at_from_business_date(tmp_path):
    """kickoffAt=businessDate+matchTime 拼接（2026-10-16）。

    体彩 matchTime 只有时分秒（实测 '17:00:00'/'00:30:00'），日期在 matchDays[].businessDate。
    不拼就拿纯时间当边界比：'2026-10-16T12:00:00' vs '17:00:00' 裸比 '2'>'1' 恒真,
    时间闸把整轮赛前情报误判成赛后全丢（比旧口径更糟的静默错误）。"""
    from llm_card import build_card as bc
    # 凌晨场：售 10-16、开赛 10-17 00:30（businessDate 才是开赛日,saleDate 不是）
    c = bc("2026-10-16",
           {"matchNumStr": "周五002", "league": "德乙", "home": "科特布斯", "away": "纽伦堡",
            "matchTime": "00:30:00", "had": {}, "crs": {}, "ttg": {}},
           insight_path=None, lineups={}, now="2026-10-16T11:00:00",
           business_date="2026-10-17")
    assert c["kickoffAt"] == "2026-10-17 00:30:00"
    assert c["kickoff"] == "00:30:00"                 # 原字段保留（下游消费者不破）
    # 两源皆无（虚构 code 查不到 sporttery + 无 businessDate）→ None,闸退回 createdAt
    c2 = bc("2026-10-16",
            {"matchNumStr": "周X999", "league": "德乙", "home": "A", "away": "B",
             "matchTime": "00:30:00", "had": {}, "crs": {}, "ttg": {}},
            insight_path=None, lineups={}, now="2026-10-16T11:00:00")
    assert c2["kickoffAt"] is None and c2["kickoffSrc"] == "none"
    # 仅 businessDate（退化近似）→ 拼得出但标注来源,凌晨场可能差一天
    c3 = bc("2026-10-16",
            {"matchNumStr": "周X999", "league": "德乙", "home": "A", "away": "B",
             "matchTime": "00:30:00", "had": {}, "crs": {}, "ttg": {}},
            insight_path=None, lineups={}, now="2026-10-16T11:00:00",
            business_date="2026-10-16")
    assert c3["kickoffAt"] == "2026-10-16 00:30:00" and c3["kickoffSrc"] == "businessDate"
    # 时间闸实测：赛前 1 小时发布的情报必须收（旧口径会丢）
    save_card(c, "2026-10-16", root=tmp_path)
    r = write_context("2026-10-16", "周五002",
                      [{"title": "late team news", "url": "u", "excerpt": "ok",
                        "publishedAt": "2026-10-16T23:30:00"},        # 赛前1h → 收
                       {"title": "post", "url": "u2", "excerpt": "ok",
                        "publishedAt": "2026-10-17T00:35:00"}],       # 开赛后 → 丢
                      root=tmp_path)
    assert r["accepted"] == 1 and len(r["dropped"]) == 1


def test_business_date_is_sale_period_not_kickoff_day():
    """回归锁：businessDate 是销售期不是开赛日（2026-10-16 实测踩坑）。

    10-16 存档 45 场里 17 场两者不一致——周五002 挂 businessDate=10-16 而真实开赛
    10-17 00:30。权威源须是 sporttery_matches.matchDate；且不可按「小时<4 则+1 天」
    硬判：周五011 开赛 07:00 同样跨日（清晨场）。"""
    from llm_card import _kickoff_dt
    idx = {"周五002": "2026-10-17", "周五011": "2026-10-17"}
    # sporttery 优先于 businessDate
    assert _kickoff_dt({"matchNumStr": "周五002", "matchTime": "00:30:00"},
                       "2026-10-16", date_index=idx) == "2026-10-17 00:30:00"
    # 清晨场 07:00 同样跨日 → 证明小时阈值硬判是错的
    assert _kickoff_dt({"matchNumStr": "周五011", "matchTime": "07:00:00"},
                       "2026-10-16", date_index=idx) == "2026-10-17 07:00:00"
    # 不在索引 → 退 businessDate（近似,凌晨场会差一天,故须标 kickoffSrc）
    assert _kickoff_dt({"matchNumStr": "周五001", "matchTime": "17:00:00"},
                       "2026-10-16", date_index=idx) == "2026-10-16 17:00:00"
    # 已是完整时刻 → 原样规范化（兼容未来上游改格式）
    assert _kickoff_dt({"matchNumStr": "x", "matchTime": "2026-10-17T00:30:00"},
                       None, date_index={}) == "2026-10-17 00:30:00"


def test_rebuild_gate_and_preservation(tmp_path):
    """重建闸：判断已冻结→硬拒；未冻结→允许加层但保全 createdAt 与语境层终态。"""
    from llm_card import save_card as sc, preds_exist, build_card as bc
    mk = lambda **kw: bc("2026-10-16",                                      # noqa: E731
                         {"matchNumStr": "周五002", "league": "德乙", "home": "A",
                          "away": "B", "matchTime": "00:30:00",
                          "had": {}, "crs": {}, "ttg": {}},
                         insight_path=None, lineups={}, business_date="2026-10-17", **kw)
    p = sc(mk(now="2026-10-16T11:00:00"), "2026-10-16", root=tmp_path)
    write_context("2026-10-16", "周五002",
                  [{"title": "t", "url": "u", "excerpt": "ok",
                    "publishedAt": "2026-10-16T20:00:00"}], root=tmp_path)
    h0 = json.loads(p.read_text(encoding="utf-8"))["cardHash"]

    # 默认幂等：不重建 → hash 不变（凭证纪律）
    sc(mk(now="2026-10-16T23:00:00"), "2026-10-16", root=tmp_path)
    assert json.loads(p.read_text(encoding="utf-8"))["cardHash"] == h0

    # rebuild=True：createdAt 沿用原值（时间闸基准不可改）+ 语境层终态保留
    sc(mk(now="2026-10-16T23:00:00"), "2026-10-16", root=tmp_path, rebuild=True)
    card = json.loads(p.read_text(encoding="utf-8"))
    assert card["createdAt"] == "2026-10-16T11:00:00"          # 非 23:00
    assert card["layers"]["context"]["status"] == "ok"         # 未被重置为 pending
    assert card["layers"]["context"]["entries"][0]["url"] == "u"
    assert card["rebuiltFrom"] == h0 and card["cardHash"] != h0

    # 判断已冻结 → preds_exist 为真（gen --rebuild 据此硬拒）
    preds = tmp_path / "preds"; preds.mkdir()
    assert not preds_exist("2026-10-16", root=preds)
    (preds / "2026-10-16.json").write_text("{}", encoding="utf-8")
    assert preds_exist("2026-10-16", root=preds)


def test_season_tag_on_history():
    """历史行赛季标（大哥 2026-10-19：历史日期需增加赛季）——H2H 与近期明细逐行。"""
    from llm_card import _season_of
    # 欧洲赛季 7 月分界：2026-09 → 2627；2025-05 → 2425；2023-09 → 2324
    assert _season_of("2026-09-02") == "2627"
    assert _season_of("2025-05-14") == "2425"
    assert _season_of("2023-09-17") == "2324"
    assert _season_of("2026-07-01") == "2627"
    assert _season_of("2026-06-30") == "2526"
    assert _season_of("") is None and _season_of(None) is None
    # 卡内 H2H 逐行注入（真 insight 文件读仓）
    card = build_card("2026-10-16",
                      {"matchNumStr": "周X001", "league": "德乙", "home": "A", "away": "B",
                       "matchTime": "03:00:00", "had": {}, "crs": {}, "ttg": {}},
                      insight_path=_Path("engine/cache/sporttery_insight_2042008.json"),
                      lineups={}, now="2026-10-16T11:00:00")
    h2h = card["layers"]["intel"]["h2h"]["matches"]
    assert h2h and all("season" in x for x in h2h)
    assert h2h[0]["season"] == "2627"


def _card007():
    import json as _j
    arch = _j.loads(_Path("engine/cache/score_odds/2026-10-16.json").read_text(encoding="utf-8"))
    day = next(d for d in arch["matchDays"]
               if any(m["matchNumStr"] == "周五007" for m in d["matches"]))
    row = next(m for m in day["matches"] if m["matchNumStr"] == "周五007")
    return build_card("2026-10-16", row,
                      insight_path=_Path("engine/cache/sporttery_insight_2042008.json"),
                      lineups={}, business_date=day.get("businessDate"))


def test_v34_market_slimmed():
    """v3.4 分岔一A 市场正身化：v3wChain 出掉市场复印件,lambda 标衍生物,单 tick 缩行。"""
    card = _card007()
    om = card["layers"]["ourModels"]
    v3 = om["v3wChain"]
    assert "crsTop5" not in v3 and "lam" not in v3          # 市场复印件已出卡
    assert {"src", "water", "ttgBoom", "independentOfMarket"} <= set(v3)
    assert om["marketLambda"]["role"] == "market-derived"    # 衍生物标注
    # devig.crs 全量保留——阅卷器 power devig 基线依赖,砍了会静默退化
    assert len(card["layers"]["market"]["devig"]["crs"]) > 20
    dr = card["layers"]["market"]["drift"]
    assert dr["status"].startswith("单调无对照") and "ticks" not in dr


def test_v34_h2h_agg_and_standing_slim():
    """v3.4 分岔二/三A：H2H 赛季聚合（主队视角·从比分算）+ standing 瘦身。"""
    card = _card007()
    h = card["layers"]["intel"]["h2h"]
    # 弗洛西诺(主)视角实测：2627 1平 · 2425 1胜1负 · 2324 1胜1负 · 1819/1516 各2场
    # （聚合从比分算,不读 winner 字段——实测 winner 与比分有矛盾行）
    assert "主队(弗洛西诺)视角" in h["seasonAgg"]
    assert "2627(1) 平1" in h["seasonAgg"] and "2425(2) 胜1负1" in h["seasonAgg"]
    assert len(h["recent2"]) == 2 and h["recent2"][0]["season"] == "2627"
    assert h["matches"]                                      # 全文仍在（审计回查）
    st = card["layers"]["intel"]["standing"]
    assert set(st["home"]) == {"ranking", "home", "away"}    # 原始 20 键已瘦
    # 体彩计数为字符串型,原样透传不强转（下游只读不算）
    assert int(st["home"]["home"]["W"]) == 2 and int(st["away"]["away"]["W"]) == 0


def test_v34_opp_pos_and_gf_ga_and_absences():
    """v3.4 分岔四/六A：对手排位 join + 主客进球分 + 伤停×射手交叉。"""
    card = _card007()
    tr = card["layers"]["ourModels"]["teamRecent"]
    rec = tr["home"]["recent"]
    assert all("oppPos" in r for r in rec)                   # 逐行带 oppPos
    nap = next(r for r in rec if r["opp"] == "那不勒斯")
    assert isinstance(nap["oppPos"], int)                    # 那不勒斯在榜,有位次
    assert tr["home"]["homeGfGa"] and len(tr["home"]["homeGfGa"]) == 3
    assert tr["away"]["awayGfGa"]
    # 萨索洛 Odenthal 2 球且伤 → 必须被交叉出来
    abs_away = card["layers"]["intel"]["keyAbsences"]["away"]
    assert any("Odenthal" in (a["name"] or "") for a in abs_away)
    assert all({"rank", "name", "goals"} == set(a) for a in abs_away)
    assert len(card["layers"]["intel"]["scorers"]["away"]["top5"]) <= 5


def test_v34_competition_type():
    """v3.4 修正❶：赛制标判据=画像存在性（杯赛键不得误标 league）。"""
    from llm_card import _competition_type
    assert _competition_type("意甲")["type"] == "league"
    assert _competition_type("欧罗巴")["type"] == "cup"      # 映射命中但无画像
    assert _competition_type("子虚乌有杯")["type"] == "cup"
    assert "轮换高风险" in _competition_type("欧罗巴")["note"]


def test_v34_view_order(tmp_path, monkeypatch):
    """v3.4 修正❹：view 渲染定序——市场在前,判断材料居中,缺口置尾。"""
    import llm_card as lc
    monkeypatch.setattr(lc, "CARDS_ROOT", tmp_path)
    card = _card007()
    d = lc.card_paths("2026-10-16")
    d.mkdir(parents=True, exist_ok=True)
    (d / "周五007.json").write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    txt = lc._view("2026-10-16", "周五007")
    i_mk, i_jd, i_gap = txt.find("市场正身"), txt.find("判断材料"), txt.find("缺口")
    assert 0 < i_mk < i_jd < i_gap                           # 定序落地
    assert "基线,非答案" in txt and "伤停×射手" in txt
    assert "λ[市场衍生]" in txt


def test_enrichment_lambda_recent_drift(tmp_path, monkeypatch):
    """v3.1 加厚三件套：λ 反解健康 / 近期明细去自身+近邻去重 / 漂移层。"""
    import llm_card as lc
    # λ：真实形态 TTG（3.55@3球最热）→ λsum≈3.3 且主客均为正
    m = {"ttg": {"s0": 22.0, "s1": 7.0, "s2": 4.25, "s3": 3.55, "s4": 4.5,
                 "s5": 6.5, "s6": 10.5, "s7": 13.0},
         "had": {"h": 2.35, "d": 3.48, "a": 2.40}}
    lam = lc._market_lambda_layer(m)
    assert lam["status"] == "ok" and 2.5 < lam["lamSum"] < 4.5
    assert lam["lamH"] > 0 and lam["lamA"] > 0
    # 键喂错形态（s0..s7 直接进）也不再出 λsum=0 假成功
    bad = lc._market_lambda_layer({"ttg": {"s0": 22.0}, "had": {}})
    assert bad["status"] in ("未获取", "反解失败")

    # 近期明细：tmp 仓库扫 02-results——自身排除 + 3 日内同对阵同比分去重
    repo = tmp_path / "repo"
    res = repo / "data" / "02-results"
    res.mkdir(parents=True)
    rows = [
        {"match": "弗洛西诺 vs 萨索洛", "result": "4-2"},          # 本场自身→必须排除
        {"match": "弗洛西诺 vs 那不勒斯", "result": "0-3"},        # 10-11 与体彩缓存 10-12 重复→去重
        {"match": "萨索洛 vs AC米兰", "result": "1-1"},
        {"match": "萨索洛 vs 尤文图斯", "result": "0-1"},
    ]
    (res / "2026-10-11.json").write_text(
        json.dumps({"date": "2026-10-11", "matches": rows}, ensure_ascii=False),
        encoding="utf-8")
    cache = repo / "engine" / "cache"
    cache.mkdir(parents=True)
    (cache / "sporttery_results_x.json").write_text(json.dumps({"matches": [
        {"home": "弗洛西诺", "away": "那不勒斯", "matchDate": "2026-10-12",
         "score": "0:3"},                                        # 同对阵同比分 3 日内→去重
        {"home": "萨索洛", "away": "弗洛西诺", "matchDate": "2026-10-16",
         "score": "2:4"},                                        # 本场另一侧写法→排除
    ]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(lc, "ROOT", repo)
    monkeypatch.setattr(lc, "_RECENT_CACHE", {})
    deep = lc._team_deep_layer("2026-10-16", "周五007", "意甲", "弗洛西诺", "萨索洛", None)
    fr = deep["home"]["recent"]
    assert all(not (r["opp"] == "萨索洛") for r in fr)         # 自身两向均不入
    assert sum(1 for r in fr if r["opp"] == "那不勒斯") == 1    # 跨源重复只剩一条
    assert deep["away"]["recent"][-1]["opp"] == "尤文图斯"      # AC米兰 1-1 已去重

    # drift：快照链 first→last
    tr = repo / "data" / "05-trends"
    tr.mkdir(parents=True)
    (tr / "2026-10-16-odds.json").write_text(json.dumps({"snapshots": [
        {"at": "t1", "matches": [{"code": "周五007",
                                  "had": {"h": 2.6, "d": 3.4, "a": 2.3}}]},
        {"at": "t2", "matches": [{"code": "周五007",
                                  "had": {"h": 2.35, "d": 3.48, "a": 2.4}}]},
    ]}), encoding="utf-8")
    d = lc._drift_layer("2026-10-16", "周五007")
    assert d["ticks"] == 2 and d["had"]["h"]["pct"] == round((2.35 - 2.6) / 2.6 * 100, 1)
    assert lc._drift_layer("1999-01-01", "x")["status"] == "未获取"


def test_norm_ts_mixed_formats():
    """时刻串规范化：kickoff 空格分隔 vs publishedAt 带 'T',裸比会误判（0x54 > 0x20）。"""
    from llm_card import _norm_ts
    assert _norm_ts("2026-10-14 03:45") == "2026-10-14 03:45:00"
    assert _norm_ts("2026-10-13T19:00:00") == "2026-10-13 19:00:00"
    assert _norm_ts("2026-10-13T19:00:00+08:00") == "2026-10-13 19:00:00"
    assert _norm_ts("2026-10-13T19:00:00Z") == "2026-10-13 19:00:00"
    assert _norm_ts("2026-10-13T19:00:00") < _norm_ts("2026-10-14 03:45")
    # 反证：同日跨格式裸比会错——'T'(0x54) > ' '(0x20) 让 12:00 发布显得晚于 20:30 开赛
    assert "2026-10-13T12:00:00" > "2026-10-13 20:30"          # 裸比：错
    assert _norm_ts("2026-10-13T12:00:00") < _norm_ts("2026-10-13 20:30")   # 规范化：对


def test_context_frozen_after_written(tmp_path):
    _mk_card(tmp_path)
    write_context("2026-10-13", "周二001", [{"title": "t", "url": "u",
                                             "publishedAt": "2026-10-13T10:00:00", "excerpt": "x"}],
                  root=tmp_path)
    with pytest.raises(RuntimeError):
        write_context("2026-10-13", "周二001", [{"title": "t2", "url": "u2",
                                                 "publishedAt": "2026-10-13T10:00:00", "excerpt": "x"}],
                      root=tmp_path)   # 终态禁二次改写


def test_context_frozen_when_all_filtered(tmp_path):
    """全被滤落『未获取』也是终态——二次改写必须抛（review fix·凭证纪律）。"""
    _mk_card(tmp_path)
    r = write_context("2026-10-13", "周二001", [{"title": "FT: A 1-0 B", "url": "u",
                                                 "publishedAt": "2026-10-13T10:00:00", "excerpt": "x"}],
                      root=tmp_path)
    assert r["accepted"] == 0
    with pytest.raises(RuntimeError):
        write_context("2026-10-13", "周二001", [{"title": "ok piece", "url": "u",
                                                 "publishedAt": "2026-10-13T10:00:00", "excerpt": "x"}],
                      root=tmp_path)


# ── v3.5 动刀三件（2026-10-20 大哥拍板「先动刀」）──────────────────────────
def test_v35_xg_side_debt_alert():
    """xG 欠账警报（002 教训的解毒剂）：零进球但 xG 不低 → 禁线性外推。"""
    from llm_card import _xg_side
    ms = []
    # 卡尔马形态：近 4 场 0 进球但 xG 场均 ~1.2（产出欠账）
    for i in range(4):
        ms.append({"date": f"0{i+1}/09/2026", "home": "Opp", "away": "Kalmar",
                   "hid": "opp", "aid": "kalmar-ff", "fthg": 1, "ftag": 0,
                   "hxg": 1.5, "axg": 1.3})
    s = _xg_side("kalmar-ff", ms, "2026-10-01")
    assert s and s["n"] == 4 and s["gf"] == 0.0 and s["xgf"] >= 1.0
    assert "欠账" in s["alert"] and "禁按" in s["alert"]
    # 正常兑现（1 球/场 vs xG 1.0）→ 无警报
    ok = [{"date": "01/09/2026", "home": "Kalmar", "away": "Opp",
           "hid": "kalmar-ff", "aid": "opp", "fthg": 1, "ftag": 1, "hxg": 1.0, "axg": 1.1}] * 4
    s2 = _xg_side("kalmar-ff", ok, "2026-10-01")
    assert "alert" not in s2 and s2["luckG"] == 0.0


def test_v35_xg_layer_real_and_no_data():
    """xG 层真仓验证：意甲 fd 覆盖 → ok；瑞超 fd 不覆盖 → 诚实 no_data（禁伪造）。"""
    from llm_card import _xg_layer
    xg = _xg_layer("意甲", "蒙扎", "卡利亚里", "2026-10-20")
    assert xg["status"] == "ok" and xg["source"] == "fd"
    for side in ("home", "away"):
        s = xg[side]
        assert s and {"n", "xgf", "xga", "gf", "ga", "luckG", "luckA"} <= set(s)
    assert _xg_layer("瑞超", "代格福什", "卡尔马", "2026-10-20")["status"].startswith("no_data")
    assert "luckG" in xg["note"]                          # 折价提示随层携带


def test_v35_view_downgrade_and_summary(tmp_path, monkeypatch):
    """v3.5 渲染三处：⚠→△待核验（降视觉权重）+ 近况摘要行前置 + xG 行。"""
    import llm_card as lc
    monkeypatch.setattr(lc, "CARDS_ROOT", tmp_path)
    card = _card007()
    d = lc.card_paths("2026-10-16")
    d.mkdir(parents=True, exist_ok=True)
    (d / "周五007.json").write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    txt = lc._view("2026-10-16", "周五007")
    # 动刀②：⚠ 降级为 △待核验 + 禁单独叫板提示（001/005 教训：名单级推断方向两反）
    assert "△" in txt and "待核验" in txt and "禁单独作叫板依据" in txt
    assert not any(line.strip().startswith("⚠") for line in txt.splitlines())
    # 动刀③：摘要行前置（近N场 W-D-L 进失 + 对手均位）+ 切片证据标注
    assert "摘要 近" in txt and "切片证据·叫板须对 xG 折价" in txt
    # 动刀①：xG 行（意甲 fd 覆盖场必有；缺层旧卡容错为 no_data 行亦算）
    assert "xG[fd]" in txt
