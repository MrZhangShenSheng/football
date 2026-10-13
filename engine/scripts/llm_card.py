# engine/scripts/llm_card.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""llm_card.py — LLM 预测数据卡生成器（spec docs/2026-10-13-llm-predict-design.html）。

五层：market(体彩存档)/intel(insight CLI)/profile(本地画像)/lineup(首发时序)/context(联网检索·会话回写)。
防泄漏三道过滤在 write_context（Task 3）；卡幂等只读（凭证纪律·预测链地基）。
判据预注册 engine/cache/llm-predict-prereg.json。开发者 sszhang"""
import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tail_dataset import TAIL_LEAGUE_MAP          # league 中文名→库 key 复用

CARDS_ROOT = ROOT / "engine" / "cache" / "llm_cards"
ARCHIVE_ROOT = ROOT / "engine" / "cache" / "score_odds"
INSIGHT_TIMEOUT = 30


def card_paths(sale_date: str, *, root: Path | None = None) -> Path:
    return (root or CARDS_ROOT) / sale_date


def card_hash(card: dict) -> str:
    """规范化 sha256 前 16 位（不含 cardHash 自身字段）。"""
    c = {k: v for k, v in card.items() if k != "cardHash"}
    blob = json.dumps(c, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _profile_layer(league_zh: str) -> dict:
    lg = TAIL_LEAGUE_MAP.get(league_zh)
    if not lg:
        return {"status": "未映射", "league": league_zh}
    f = ROOT / "data" / "00-leagues" / f"{lg}.json"
    if not f.exists():
        return {"status": "未获取", "league": league_zh}
    doc = json.loads(f.read_text(encoding="utf-8"))
    keep = {k: doc.get(k) for k in ("leagueCn", "avgGoals", "homeWinRate", "upsetRate", "topScores")
            if doc.get(k) is not None}
    return {"status": "ok", "league": lg, **keep}


def _lineup_layer(code: str, lineups: dict) -> dict:
    # 字段名对齐 lineup_watch.py 实际落盘（data/05-trends/{day}-lineups.json）：
    # record={matchId, code(=matchNumStr), lineup(None=未公布|{home/away:{starters}}), lineupSeenAt, ...}
    for mid, e in (lineups or {}).items():
        if not isinstance(e, dict):
            continue
        if e.get("code") == code:
            lu = e.get("lineup") or {}
            return {"status": "ok", "lineupPublished": e.get("lineup") is not None,
                    "homeStarters": (lu.get("home") or {}).get("starters"),
                    "awayStarters": (lu.get("away") or {}).get("starters"),
                    "lineupSeenAt": e.get("lineupSeenAt"),
                    "matchId": mid}
    return {"status": "未获取"}


def build_card(sale_date: str, match: dict, *, insight_path: Path | None,
               lineups: dict, now: str | None = None) -> dict:
    """match=score_odds 存档行 → 五层数据卡。开发者 sszhang"""
    intel = {"status": "未获取"}
    if insight_path and Path(insight_path).exists():
        doc = json.loads(Path(insight_path).read_text(encoding="utf-8"))
        intel = {"status": "ok", **{k: doc.get(k) for k in ("injury", "recent", "standing", "h2h", "scorers")
                                    if doc.get(k) is not None}}
    card = {
        "code": match["matchNumStr"], "match": f'{match["home"]} vs {match["away"]}',
        "league": match["league"], "kickoff": match.get("matchTime"), "saleDate": sale_date,
        "layers": {
            "market": {"had": match.get("had") or {}, "crs": match.get("crs") or {},
                       "ttg": match.get("ttg") or {}, "oddsUpdatedAt": match.get("oddsUpdatedAt")},
            "intel": intel,
            "profile": _profile_layer(match["league"]),
            "lineup": _lineup_layer(match["matchNumStr"], lineups),
            "context": {"status": "pending", "entries": []},
        },
        "createdAt": now or datetime.now().isoformat(timespec="seconds"),
    }
    card["cardHash"] = card_hash(card)
    return card


def save_card(card: dict, sale_date: str, *, root: Path | None = None) -> Path:
    """幂等只读：已存在直接返回（凭证纪律·预测链防事后改）。"""
    d = card_paths(sale_date, root=root)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f'{card["code"]}.json'
    if p.exists():
        return p
    p.write_text(json.dumps(card, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def gen(sale_date: str) -> int:
    """CLI 主流程：当日存档→逐场 intel→存卡→待检索清单。"""
    arc = ARCHIVE_ROOT / f"{sale_date}.json"
    if not arc.exists():
        raise SystemExit(f"[llm_card] 缺当日存档 {arc}（先跑 run.py update）")
    doc = json.loads(arc.read_text(encoding="utf-8"))
    lineups_f = ROOT / "data" / "05-trends" / f"{sale_date}-lineups.json"
    # lineup_watch 落盘顶层={date,type,...,matches:{matchId:record}}，record 挂 matches 键下
    lineups = {}
    if lineups_f.exists():
        ldoc = json.loads(lineups_f.read_text(encoding="utf-8"))
        lineups = (ldoc.get("matches") or {}) if isinstance(ldoc, dict) else {}
    for day in doc.get("matchDays") or []:
        for m in day.get("matches") or []:
            if m.get("sellStatus") not in (None, "ON_SALE", "AUTO"):
                continue
            mid = m.get("matchId") or m.get("uniformMatchId")
            insight_path = None
            if mid:
                ip = ROOT / "engine" / "cache" / f"sporttery_insight_{mid}.json"
                if not ip.exists():
                    try:
                        subprocess.run([sys.executable, str(ROOT / "engine" / "scripts" / "sporttery_fetch.py"),
                                        "insight", str(mid)], timeout=INSIGHT_TIMEOUT, check=False)
                    except subprocess.TimeoutExpired:
                        pass
                insight_path = ip if ip.exists() else None
            card = build_card(sale_date, m, insight_path=insight_path, lineups=lineups)
            save_card(card, sale_date)
            print(f'[card] {card["code"]} {card["match"]} | 建议检索: "{card["match"]} injury news preview"')
    return 0


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="LLM 预测数据卡生成器")
    ap.add_argument("cmd", choices=["gen"])
    ap.add_argument("sale_date")
    args = ap.parse_args()
    return gen(args.sale_date)


if __name__ == "__main__":
    raise SystemExit(main())
