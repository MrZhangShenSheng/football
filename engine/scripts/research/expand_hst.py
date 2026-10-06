# -*- coding: utf-8 -*-
"""v11 HST 覆盖扩展：fd 16 联赛 → 比分+射正一次拉齐建库。

已有 8 联赛的 HST（fetch_hst.py）→ 本脚本扩展剩余 8 个：
  england-championship / germany-bundesliga2 / italy-serie-b / spain-liga2 /
  belgium-first-a / turkey-super-lig / greece-super / SC0
同时为**所有 16 个联赛**建立 timeline（fd CSV 的 FTHG/FTAG → league/{lg}_matches.json），
解决"有 HST 但无 timeline"的库缺失问题。
产出：
  1. data/02-results/league/{lg}_matches.json（新联赛 timeline·fd CSV 比分）
  2. engine/cache/hst_{lg}.json（全 16 联赛射正·与已有 8 联赛合并）
开发者 sszhang
用法：python engine/scripts/research/expand_hst.py [--leagues E1,D2,...] [--seasons 2223,...]
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import ROOT, load_aliases

LEAGUE_DIR = ROOT / "data" / "02-results" / "league"
CACHE = ROOT / "engine" / "cache"

FD_MAP = {
    "E0": "england-premier", "E1": "england-championship",
    "D1": "germany-bundesliga", "D2": "germany-bundesliga2",
    "SP1": "spain-laliga", "SP2": "spain-liga2",
    "I1": "italy-serie-a", "I2": "italy-serie-b",
    "F1": "france-ligue1", "F2": "france-ligue2",
    "N1": "netherlands-eredivisie",
    "P1": "portugal-primeira", "B1": "belgium-first-a",
    "T1": "turkey-super-lig", "G1": "greece-super", "SC0": "SC0",
}
SEASONS = ("2223", "2324", "2425", "2526", "2627")
BASE = "https://www.football-data.co.uk/mmz4281/{season}/{code}.csv"


def _norm(name: str, aliases: dict) -> str | None:
    if name in aliases:
        return name
    kebab = name.lower().replace(" ", "-").replace("'", "")
    if kebab in aliases:
        return kebab
    for tid, srcs in aliases.items():
        if srcs.get("espn") and srcs["espn"].lower() == name.lower():
            return tid
    for tid, srcs in aliases.items():
        if srcs.get("fd") and srcs["fd"].lower() == name.lower():
            return tid
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--leagues", default=",".join(FD_MAP.keys()))
    ap.add_argument("--seasons", default=",".join(SEASONS))
    args = ap.parse_args()
    codes = [c.strip() for c in args.leagues.split(",") if c.strip()]
    seasons = tuple(s.strip() for s in args.seasons.split(",") if s.strip())
    aliases = load_aliases()

    for code in codes:
        lg = FD_MAP.get(code)
        if not lg:
            print(f"[skip] {code}: 无映射")
            continue
        tl_rows, hst_rows, dropped = [], [], 0
        for season in seasons:
            url = BASE.format(season=season, code=code)
            try:
                r = requests.get(url, timeout=30)
                r.raise_for_status()
                if len(r.text) < 500:
                    continue
                for m in csv.DictReader(io.StringIO(r.text)):
                    fthg, ftag = m.get("FTHG"), m.get("FTAG")
                    if fthg in (None, "", "NA") or ftag in (None, "", "NA"):
                        continue
                    try:
                        d = datetime.strptime(m.get("Date", ""), "%d/%m/%Y").date().isoformat()
                    except ValueError:
                        try:
                            d = datetime.strptime(m.get("Date", ""), "%d/%m/%y").date().isoformat()
                        except ValueError:
                            continue
                    hid = _norm(m.get("HomeTeam") or "", aliases)
                    aid = _norm(m.get("AwayTeam") or "", aliases)
                    if not hid or not aid:
                        dropped += 1
                        continue
                    try:
                        hg, ag = int(fthg), int(ftag)
                        tl_rows.append({"date": d, "home": hid, "away": aid, "hg": hg, "ag": ag})
                    except ValueError:
                        continue
                    hst, ast = m.get("HST"), m.get("AST")
                    if hst not in (None, "", "NA") and ast not in (None, "", "NA"):
                        try:
                            hst_rows.append({"date": d, "home": hid, "away": aid,
                                             "hst": int(hst), "ast": int(ast)})
                        except ValueError:
                            pass
            except requests.RequestException as e:
                print(f"  [warn] {code} {season}: {type(e).__name__}")

        tl_rows.sort(key=lambda x: x["date"])
        hst_rows.sort(key=lambda x: x["date"])

        # timeline：新联赛建库·已有联赛追加（幂等去重）
        tl_path = LEAGUE_DIR / f"{lg}_matches.json"
        existing = set()
        if tl_path.exists():
            old = json.loads(tl_path.read_text(encoding="utf-8"))
            old_rows = old.get("matches", old) if isinstance(old, dict) else old
            existing = {(r["date"], r["home"], r["away"]) for r in old_rows}
        new_tl = [r for r in tl_rows if (r["date"], r["home"], r["away"]) not in existing]
        all_tl = sorted((tl_path.exists() and (json.loads(tl_path.read_text(encoding="utf-8")).get("matches", []) or [])) or tl_rows,
                        key=lambda x: x["date"]) if tl_path.exists() else tl_rows
        if new_tl:
            all_tl.extend(new_tl)
            all_tl.sort(key=lambda x: x["date"])
        tl_path.write_text(json.dumps(
            {"league": lg, "source": "fd CSV FTHG/FTAG", "matches": all_tl},
            ensure_ascii=False, indent=1), encoding="utf-8")

        # HST：新联赛建文件·已有联赛合并
        hst_path = CACHE / f"hst_{lg}.json"
        if hst_path.exists():
            old_h = json.loads(hst_path.read_text(encoding="utf-8"))
            old_set = {(r["date"], r["home"], r["away"]) for r in old_h.get("rows", [])}
            hst_rows = old_h.get("rows", []) + [r for r in hst_rows if (r["date"], r["home"], r["away"]) not in old_set]
            hst_rows.sort(key=lambda x: x["date"])
        hst_path.write_text(json.dumps(
            {"league": lg, "source": f"fd CSV HST/AST {seasons[0]}-{seasons[-1]}",
             "rows": hst_rows, "unmappedDropped": dropped},
            ensure_ascii=False, indent=1), encoding="utf-8")

        print(f"[expand] {lg}: timeline {len(all_tl)} 场（新增 {len(new_tl)}）· HST {len(hst_rows)} 场 · 丢映射 {dropped}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
