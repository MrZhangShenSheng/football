#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""轨道N叙事彩票模型(设计§十一/十二): 球队强弱/状态/缺阵/风格/剧本五层叙事链
推导比赛走向与比分→CRS/HAFU/混串玩法映射. 不算EV不参考市场定价(分歧度仅排序用).
开发者 sszhang"""
import json
import sys
from datetime import date
from pathlib import Path
from common import ROOT

SCRIPT_UNIVERSE = {"1:0", "2:0", "2:1", "0:0", "1:1", "0:1", "1:2", "2:2"}   # 小比分剧本域
HAFU_KEYS = ("hh", "hd", "ha", "dh", "dd", "da", "ah", "ad", "aa")
NARRATIVE_DIR = ROOT / "data/03-predictions"
MATCHES_CACHE = ROOT / "engine/cache/sporttery_matches.json"
FALLBACK_SCORE = "1:0"          # 风格模板缺位时的兜底剧本(∈SCRIPT_UNIVERSE)
DEFAULT_HAD_ODDS = 3.0          # had.h 缺失时的兜底主胜赔率
SCRIPT_PROB_EST = 0.25          # 剧本概率粗估(v0 骨架, 接dc_predict后细化)
FLAT_P_MKT = 0.33               # 市场价缺失时的平先验隐含概率
FACTOR_STAR_THRESHOLD = 0.9     # 层因子≥此值计一星(硬判定, v0 全默认1.0)
STAR_BASE_BOOST = 2             # 星级基线加成(粗估公式: 4硬层全过+2=5星)
STAR_FLOOR, STAR_CEIL = 1, 5    # 星域 1-5
PLAY_TYPE_CRS_2X1 = "N-CRS-2x1"  # N-前缀=轨道N票面标记(影子层spec命名同规)
TRACK_N = "N"                   # 轨道N标记(影子层track分轨·设计§七兼容面④)
SHADOW_DIR = Path(__file__).parent.parent / "shadow"   # 影子层脚本目录(2026-09-07 迁 engine/shadow 纳管 git)
CRS_POOL_KEY_LEN = 6            # 体彩 crs 池键 's01s00' 定长
SHADOW_MULT_DEFAULT = 1         # v0 mult=None→1倍(shapes.settle 的 BET_UNIT*mult 需数值)
SHADOW_BET_UNIT = 2             # 单注 2 元(与影子层 shapes.BET_UNIT 同源)

def _strength_layer(m, profile):
    st = profile.get("standings") or []
    pos = {row.get("team"): row.get("pos") for row in st}
    return {"layer": "strength", "homePos": pos.get(m["home"]), "awayPos": pos.get(m["away"]),
            "drawRate": profile.get("drawRate"), "upsetRate": profile.get("upsetRate")}

def _form_layer(m, teams):
    h = (teams.get(m["home"]) or {}).get("formSummary") or {}
    a = (teams.get(m["away"]) or {}).get("formSummary") or {}
    return {"layer": "form", "homeLast10": h.get("last10"), "awayLast10": a.get("last10"),
            "homeGoalAvg": h.get("goalAvg"), "awayGoalAvg": a.get("goalAvg")}

def _absence_layer(m):
    # 伤停差值因子: 现有insight链已在02-results, 此处读取最近结果文件做近似
    # (ESPN injury端点接入是批次4后续任务, 先以现有资产跑通——设计§十一数据边界)
    return {"layer": "absence", "factor": 1.0, "source": "placeholder-until-espn"}

def _style_layer(m, profile):
    top = profile.get("scoreTop") or {}
    return {"layer": "style", "topScores": top}

def _script_layer(m, style):
    # 风格模板最高频比分→剧本(λ推导接dc_predict是P1.5, 先模板版跑通链路)
    # scoreTop键为短横线格式("1-0", 联赛画像/测试夹具同口径)→归一化冒号并限定剧本域
    top = style.get("topScores") or {}
    in_domain = {k.replace("-", ":"): v for k, v in top.items()
                 if k.replace("-", ":") in SCRIPT_UNIVERSE}
    score = max(in_domain, key=in_domain.get) if in_domain else FALLBACK_SCORE
    return {"layer": "script", "score": score}

def build_candidate(m: dict, profile: dict, teams: dict) -> dict:
    layers = [_strength_layer(m, profile), _form_layer(m, teams),
              _absence_layer(m), _style_layer(m, profile)]
    script_l = _script_layer(m, layers[3])
    layers.append(script_l)
    h, a = script_l["score"].split(":")
    hi, ai = int(h), int(a)
    half = "h" if hi > ai else "d" if hi == ai else "a"
    hafu = half + half          # v0: 全场=半场方向同构, HAFU细分待P1.5
    star = sum(1 for l in layers if l.get("factor", 1.0) >= FACTOR_STAR_THRESHOLD
               and l.get("layer") != "script")
    had = m.get("had") or {}
    p_mkt = 1.0 / float(had.get("h") or DEFAULT_HAD_ODDS) if had.get("h") else FLAT_P_MKT
    divergence = SCRIPT_PROB_EST - p_mkt   # 剧本概率粗估-市场隐含(排序用, 非EV)
    return {"code": m.get("matchNumStr") or m.get("code"), "match": f'{m["home"]}-{m["away"]}',
            "layers": layers, "script": {"score": script_l["score"], "hafu": hafu,
            "dir": "主胜" if hi > ai else "平" if hi == ai else "客胜"},
            "star": min(STAR_CEIL, max(STAR_FLOOR, star + STAR_BASE_BOOST)),
            "divergence": round(divergence, 4)}

def build_narrative(matches, profiles, teams, seq):
    cands = [build_candidate(m, profiles.get(m.get("league"), {}), teams) for m in matches]
    cands.sort(key=lambda c: -c["divergence"])          # 剧本分歧度选场(设计§十一)
    top = cands[:3]
    plays = ([{"name": "N-甲", "playType": PLAY_TYPE_CRS_2X1,
               "legs": [top[0], top[1]] if len(top) >= 2 else top,
               "mult": None, "star": top[0]["star"]}] if top else [])
    return {"date": str(date.today()), "seq": seq, "candidates": cands, "plays": plays,
            "track": TRACK_N}


# ── 轨道N影子票最小桥(final-fix I-2): 落卡→paper.register 登记 track="N" ──
def _crs_odds(match_by_code: dict, code: str, score: str):
    """自体彩缓存 crs 池取比分赔率('s01s00'→'1:0' 口径, 与 paper.shadow_all 同规);
    缺场/缺价/非数值 → None. 开发者 sszhang"""
    pool = (match_by_code.get(code) or {}).get("crs") or {}
    for k, v in pool.items():
        if (k.startswith("s") and len(k) == CRS_POOL_KEY_LEN and k[3] == "s"
                and k[1:3].isdigit() and k[4:6].isdigit()
                and f"{int(k[1:3])}:{int(k[4:6])}" == score):
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
    return None


def _load_paper():
    """导入影子层 paper 模块(engine/shadow·2026-09-07 迁址纳管 git；shapes 已改懒加载,
    缺失时 register_shadow 内部 RuntimeError, 由 _shadow_bridge 兜底)"""
    sys.path.insert(0, str(SHADOW_DIR))
    import paper
    return paper


def register_shadow(card: dict, match_by_code: dict, paper_mod=None) -> list:
    """叙事卡 → 轨道N影子票登记(最小桥): N-CRS-2x1 每腿 crs 价自体彩当刻池冻结,
    spec_name = f"{playType}-{date}"(playType 自带 N- 前缀·与 EV 轨 spec 不撞,
    (spec_name,date) 为幂等键——register 自身不查重, 由本桥自查), track="N"。
    腿 crs 池缺价 → 整票跳过(不登记结算时 odds[key] 取键会 KeyError 的残票)。
    paper_mod 注入点供测试替身(不写真账本). 开发者 sszhang"""
    if paper_mod is None:
        paper_mod = _load_paper()
    done = {(t.get("spec_name"), t.get("date")) for t in paper_mod.load_tickets()}
    registered = []
    for play in card.get("plays", []):
        if play.get("playType") != PLAY_TYPE_CRS_2X1:   # v0 唯一桥接玩法, HAFU/MIX 待 P1.5
            print(f"[narrative] 影子跳过(玩法未桥接): {play.get('playType')}")
            continue
        spec_name = f"{play['playType']}-{card['date']}"
        if (spec_name, card["date"]) in done:
            continue                                  # 幂等: 同卡重复跑不产生重复影子票
        legs, pairs, ok = [], [], True
        for i, cand in enumerate(play["legs"]):
            score = cand["script"]["score"]
            odds = _crs_odds(match_by_code, cand["code"], score)
            if odds is None:
                print(f"[narrative] 影子跳过({spec_name}): {cand['code']} crs 池缺 {score} 价")
                ok = False
                break
            legs.append({"code": cand["code"], "match": cand["match"],
                         "market": "crs", "pick": [score], "odds": {score: odds}})
            pairs.append([i, score])                   # [腿序号, 选项] pair(账本冻结形态)
        if not ok:
            continue
        bets = [{"legs": pairs}]                      # CRS 2串1 = 1 注
        mult = play.get("mult") or SHADOW_MULT_DEFAULT
        paper_mod.register(spec_name=spec_name, date=card["date"], legs=legs, bets=bets,
                           mult=mult, cost=SHADOW_BET_UNIT * mult * len(bets),
                           track=card.get("track", TRACK_N))
        registered.append(spec_name)
    return registered


def _shadow_bridge(card: dict, matches: list) -> list:
    """main 落卡成功后调用: 登记 track="N" 影子票; scratch 影子层不可用(缺 shapes 等
    ImportError)时 print 警告跳过, 不炸 CLI. 开发者 sszhang"""
    try:
        paper_mod = _load_paper()
    except ImportError as e:
        print(f"[narrative] 影子登记跳过(scratch 影子层不可用): {e}")
        return []
    by_code = {m.get("matchNumStr") or m.get("code"): m for m in matches}
    registered = register_shadow(card, by_code, paper_mod=paper_mod)
    for name in registered:
        print(f"[narrative] 影子票已登记: {name}")
    return registered


def main() -> None:
    data = json.loads(MATCHES_CACHE.read_text(encoding="utf-8"))
    matches = data.get("matches", [])
    card = build_narrative(matches, {}, {}, seq=1)
    NARRATIVE_DIR.mkdir(parents=True, exist_ok=True)
    out = NARRATIVE_DIR / f"{date.today()}-narrative.json"
    out.write_text(json.dumps(card, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[narrative] → {out}")
    _shadow_bridge(card, matches)

if __name__ == "__main__":
    main()
