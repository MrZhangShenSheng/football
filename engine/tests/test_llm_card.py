# engine/tests/test_llm_card.py
import sys, json, pathlib, pytest
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
