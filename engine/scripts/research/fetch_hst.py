# -*- coding: utf-8 -*-
"""v11 阶段3：fd 射正(HST)历史拉取——8 个链内 fd 联赛 × 5 季。

fd CSV 的 HST/AST（主客射正）100% 覆盖（D3 探针实证）·拉 2022~2026 五季给 rolling 预热。
入库 engine/cache/hst_{lg}.json（规范ID·date ISO·与 timeline 对齐·unmapped 计数）。
幂等可重跑全量重建。开发者 sszhang
用法：python engine/scripts/research/fetch_hst.py
"""
from __future__ import annotations

import csv
import io
import json
import sys
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import ROOT, load_aliases

CACHE = ROOT / "engine" / "cache"

# fd 代码 → 链库键（仅链内有 timeline 库的 fd 联赛）
FD_LEAGUES = {
    "E0": "england-premier", "SP1": "spain-laliga", "D1": "germany-bundesliga",
    "I1": "italy-serie-a", "F1": "france-ligue1", "N1": "netherlands-eredivisie",
    "P1": "portugal-primeira", "F2": "france-ligue2",
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
    aliases = load_aliases()
    for code, lg in sorted(FD_LEAGUES.items(), key=lambda kv: kv[1]):
        rows, dropped = [], 0
        for season in SEASONS:
            url = BASE.format(season=season, code=code)
            try:
                r = requests.get(url, timeout=30)
                r.raise_for_status()
                if len(r.text) < 500:
                    continue                     # 空季/404 页
                for m in csv.DictReader(io.StringIO(r.text)):
                    hst, ast = m.get("HST"), m.get("AST")
                    if hst in (None, "", "NA") or ast in (None, "", "NA"):
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
                        rows.append({"date": d, "home": hid, "away": aid,
                                     "hst": int(hst), "ast": int(ast)})
                    except ValueError:
                        continue
            except requests.RequestException as e:
                print(f"  [warn] {code} {season}: {type(e).__name__}")
        rows.sort(key=lambda x: x["date"])
        out = CACHE / f"hst_{lg}.json"
        out.write_text(json.dumps({"league": lg, "source": "fd CSV HST/AST 2223-2627",
                                   "rows": rows, "unmappedDropped": dropped},
                                  ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[hst] {lg}: {len(rows)} 场射正（丢映射 {dropped}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
