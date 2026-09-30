# -*- coding: utf-8 -*-
r"""predictor_v4b_daily.py —— v4b 比分预测模型日卡（B：接入帮我预测对照 + C：影子盲测快照源）

职责（2026-09-30 B+C 立项落地）：
  1. snap(date)：当日在售场次 → zh 别名映射 → TeamStats（league_timeline 已赛口径，天然无泄漏）
     → PredictorV4b → 每场 CRS top2（仅保留体彩池内有价比分）
     → 快照 engine/cache/v4b_snap/{date}.json（与 qcache 同构 {code:{crs:[(pick,signal),...]}}，
       供 shadow 层 v4b-双选2串1 spec 消费；纳管 git——赛前冻结凭证链，禁 scratch）
  2. card(date)：对照卡 data/03-predictions/{date}-v4b.json —— 每场 v4b top2(信号×体彩真实赔率)
     vs 市场 CRS 最热 vs 当日 crs_fusion 族 top1（boldplay 卡若有）——B 的对照出口，只对照不出票
  3. 跳过原因逐场打印非静默（铁律 8 空结果追查）；映射失败/历史不足/无有价比分 → skipReason 落快照 meta

口径：
  - TeamStats 构建与 predict_today.py / v4b 回测同源（league_timeline 全量 + hist_odds 已完赛体彩场 date<预测日）
  - 快照 data 部分与 shapes.load_qcache 返回结构同构，shapes.build_ticket v4b 分支直接消费
  - 赔率冻结：体彩在售 CRS 池当刻价（sXXsYY → 'h:a' 换算，与影子层 freeze 口径一致）

用法：
  python engine/scripts/predictor_v4b_daily.py [date]           # snap+card（date 缺省=今日）
  python engine/scripts/predictor_v4b_daily.py 2026-10-01 --snap-only
开发者 sszhang
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date as _date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent / "research"))

from engine.predictor_v4b import PredictorV4b, team_stats_to_team_data  # noqa: E402
import score_family_model as sfm  # noqa: E402

SNAP_DIR = ROOT / "engine" / "cache" / "v4b_snap"
CARD_DIR = ROOT / "data" / "03-predictions"
UNIT = 2.0  # 与 v4b 文档第五章策略同源（2串1 双选 4注×2元）


def crs_key_to_pretty(k: str) -> str:
    """体彩 CRS 原始键 s01s01 → '1:1'（'胜其他'等方向键原样保留）。"""
    if k.startswith("s") and len(k) >= 6 and k[3] == "s":
        try:
            return f"{int(k[1:3])}:{int(k[4:6])}"
        except ValueError:
            return k
    return k


def load_hist_scores(before: str) -> list:
    """体彩历史已完赛场（带赛果）→ [(date, home_zh, away_zh, hg, ag)]，date < before。"""
    out, seen = [], set()
    for p in sorted((ROOT / "engine/cache/hist_odds").glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for m in d.get("matches", []):
            sc = str(m.get("score") or "")
            if ":" not in sc:
                continue
            day = str(m.get("date") or "")[:10]
            if day >= before:
                continue
            try:
                h, a = (int(x) for x in sc.split(":")[:2])
            except ValueError:
                continue
            key = (day, m.get("home"), m.get("away"))
            if key in seen:
                continue
            seen.add(key)
            out.append((day, m.get("home"), m.get("away"), h, a))
    out.sort(key=lambda r: r[0])
    return out


def build_stats(until: str) -> dict:
    """截至 until 的 TeamStats（league_timeline 已赛 + 体彩已完赛补充，v4b 回测同口径）。"""
    zh = {}
    for tid, srcs in sfm.load_aliases().items():
        if srcs.get("zh"):
            zh[srcs["zh"]] = tid
    merged = [("L", d, h, a, hg, ag) for d, h, a, hg, ag in sfm.league_timeline()]
    merged += [("B", d, zh.get(hm), zh.get(am), hg, ag)
               for d, hm, am, hg, ag in load_hist_scores(until)
               if zh.get(hm) and zh.get(am)]
    merged.sort(key=lambda r: (r[1], 0 if r[0] == "L" else 1))
    stats = defaultdict(sfm.TeamStats)
    for _, d, h, a, hg, ag in merged:
        stats[h].add(hg, ag, True, a)
        stats[a].add(ag, hg, False, h)
    return {"stats": stats, "zh": zh}


def snap(day: str, force: bool = False) -> dict:
    """生成 v4b 快照（幂等：文件在即返回，force 才重写——冻结纪律与 qcache 一致）。"""
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    path = SNAP_DIR / f"{day}.json"
    if path.exists() and not force:
        return json.loads(path.read_text(encoding="utf-8"))

    sm = json.loads((ROOT / "engine/cache/sporttery_matches.json").read_text(encoding="utf-8"))
    matches = [m for m in sm.get("matches", []) if m.get("matchDate") == day]
    ctx = build_stats(day)
    stats, zh = ctx["stats"], ctx["zh"]
    predictor = PredictorV4b()

    data, meta = {}, []
    for m in matches:
        code, hm, am = m.get("code"), m.get("home"), m.get("away")
        hid, aid = zh.get(hm), zh.get(am)
        why = None
        if not (hid and aid):
            why = "别名映射失败（国家队/亚运/未收录队——模型不可算）"
        elif stats[hid].n < sfm.MIN_HIST or stats[aid].n < sfm.MIN_HIST:
            why = f"历史不足（MIN_HIST={sfm.MIN_HIST}, 主{stats[hid].n}/客{stats[aid].n}场）"
        if why:
            meta.append({"code": code, "match": f"{hm} vs {am}", "skipReason": why})
            print(f"[v4b] 跳过 {code} {hm} vs {am}: {why}")
            continue

        pred = predictor.predict(team_stats_to_team_data(stats[hid]),
                                 team_stats_to_team_data(stats[aid]))
        crs_raw = m.get("crs") or {}
        pool = {crs_key_to_pretty(k): float(v) for k, v in crs_raw.items() if v}
        ranked = [(f"{h}:{a}", float(sig)) for (h, a), sig in pred if f"{h}:{a}" in pool]
        top2 = ranked[:2]
        if not top2:
            meta.append({"code": code, "match": f"{hm} vs {am}",
                         "skipReason": "v4b top 比分均无体彩池价（不可买）"})
            print(f"[v4b] 跳过 {code} {hm} vs {am}: top 比分均无池价")
            continue
        data[code] = {"crs": top2}
        print(f"[v4b] {code} {hm} vs {am} → " +
              " / ".join(f"{p}@{pool[p]} 信号{s:.4f}" for p, s in top2))

    doc = {"date": day, "model": "predictor_v4b", "frozenAt": _date.today().isoformat(),
           "data": data, "meta": {"nCalc": len(data), "nSkip": len(meta), "skips": meta}}
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[v4b] 快照 {len(data)} 场可算 / {len(meta)} 场跳过 → {path}")
    return doc


def card(day: str) -> dict:
    """对照卡：v4b top2 vs 市场 CRS 最热 vs 当日 crs_fusion（boldplay 卡若有）。"""
    doc = snap(day)
    sm = json.loads((ROOT / "engine/cache/sporttery_matches.json").read_text(encoding="utf-8"))
    matches = {m.get("code"): m for m in sm.get("matches", []) if m.get("matchDate") == day}

    # crs_fusion 族候选（当日 boldplay 三池卡 candidates 中 pool=='crs' 首项——对照现行生产链）
    fusion_top = {}
    bp_path = CARD_DIR / f"{day}-boldplay.json"
    if bp_path.exists():
        bp = json.loads(bp_path.read_text(encoding="utf-8"))
        for c in (bp.get("cards") or []):
            crs_cands = [k for k in (c.get("candidates") or []) if k.get("pool") == "crs"]
            fusion_top[c.get("code")] = crs_cands[0] if crs_cands else None

    rows = []
    for code, pools in doc["data"].items():
        m = matches.get(code) or {}
        pool = {crs_key_to_pretty(k): float(v) for k, v in (m.get("crs") or {}).items() if v}
        mkt_hot = max(pool, key=pool.get) if pool else None
        rows.append({
            "code": code, "league": m.get("league"), "match": f"{m.get('home')} vs {m.get('away')}",
            "v4bTop2": [{"pick": p, "signal": round(s, 4), "odds": pool.get(p)} for p, s in pools["crs"]],
            "marketHot": {"pick": mkt_hot, "odds": pool.get(mkt_hot)} if mkt_hot else None,
            "fusionTop": fusion_top.get(code),
            "agreeWithMarket": bool(pools["crs"] and mkt_hot == pools["crs"][0][0]),
        })
    card_doc = {"date": day, "model": "predictor_v4b", "nCalc": len(rows),
                "nSkip": doc["meta"]["nSkip"], "skips": doc["meta"]["skips"],
                "status": "样本内回测未过盲测——对照源非生产概率源（docs/predictor_v4b.md 收编标注）",
                "matches": rows}
    CARD_DIR.mkdir(parents=True, exist_ok=True)
    out = CARD_DIR / f"{day}-v4b.json"
    out.write_text(json.dumps(card_doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[v4b] 对照卡 {len(rows)} 场 → {out}")
    return card_doc


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("date", nargs="?", default=_date.today().isoformat())
    ap.add_argument("--snap-only", action="store_true")
    ap.add_argument("--force", action="store_true", help="快照存在时强制重写（默认冻结不重写）")
    args = ap.parse_args()
    if args.snap_only:
        snap(args.date, force=args.force)
    else:
        card(args.date)
