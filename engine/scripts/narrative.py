#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""轨道N叙事彩票模型(设计§十一/十二): 球队强弱/状态/缺阵/风格/剧本五层叙事链
推导比赛走向与比分→CRS/HAFU/混串玩法映射. 不算EV不参考市场定价(分歧度仅排序用).
开发者 sszhang"""
import json
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
            "track": "N"}

def main() -> None:
    data = json.loads(MATCHES_CACHE.read_text(encoding="utf-8"))
    card = build_narrative(data.get("matches", []), {}, {}, seq=1)
    NARRATIVE_DIR.mkdir(parents=True, exist_ok=True)
    out = NARRATIVE_DIR / f"{date.today()}-narrative.json"
    out.write_text(json.dumps(card, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[narrative] → {out}")

if __name__ == "__main__":
    main()
