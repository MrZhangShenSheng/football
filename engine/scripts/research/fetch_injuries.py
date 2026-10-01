# -*- coding: utf-8 -*-
"""历史伤停批量回算器（一次性·已跑完归档）——2026-09-30 伤停终判数据底座。

全量一年 4999 场经体彩 insight getInjurySuspensionV1 回算 → engine/cache/insight_injuries.json
（82% 场次有伤停；终判=方向真实但市场已完全定价，见 docs/2026-09-29-positive-ev-falsification.html §二点五）。

⚠️ 历史回算是终态快照、无采集时点，无法回测「临场时点档」——forward 积累已并入
engine/scripts/lineup_watch.py 伤停轨（2026-10-01）：全在售场节流复查+变更时间戳，
slim 字段与本脚本同构（name/pos/injury/susp/apps/starts），两数据集可直接对齐分析。
日常勿重跑本脚本；确需增量回算历史时断点续采幂等。

开发者 sszhang
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sporttery_fetch import INJURY_URL, get_json

ROOT = Path(__file__).resolve().parents[3]
HIST = ROOT / "engine/cache/hist_odds/crs_hist_2025-10-01_2026-09-28.json"
OUT = ROOT / "engine/cache/insight_injuries.json"
SINCE = "2025-10-01"
SLEEP = 0.25


def main():
    d = json.loads(HIST.read_text(encoding="utf-8"))
    targets = [m for m in d["matches"]
               if str(m.get("date")) >= SINCE and m.get("matchId")]
    print(f"目标 {len(targets)} 场（{SINCE} 之后）")

    done = {}
    if OUT.exists():
        try:
            done = json.loads(OUT.read_text(encoding="utf-8")).get("injuries") or {}
            print(f"[resume] 已有 {len(done)}，跳过")
        except Exception:
            done = {}

    ok = fail = empty = 0
    t0 = time.time()
    for i, m in enumerate(targets, 1):
        mid = str(m["matchId"])
        if mid in done:
            continue
        try:
            v = get_json(INJURY_URL + f"?sportteryMatchId={mid}").get("value") or {}
            h = (((v.get("home") or {}).get("injuriesAndSuspensionsList")) or [])
            a = (((v.get("away") or {}).get("injuriesAndSuspensionsList")) or [])
            slim = lambda lst: [{"name": x.get("personName"), "pos": x.get("playerPositionDesc"),
                                 "injury": x.get("injuryFlag"), "susp": x.get("suspensionFlag"),
                                 "apps": x.get("appearanceCnt"), "starts": x.get("startedMatchCnt")}
                                for x in lst]
            done[mid] = {"date": m.get("date"), "league": m.get("league"),
                         "home": m.get("home"), "away": m.get("away"),
                         "score": m.get("score"),
                         "inj_h": slim(h), "inj_a": slim(a)}
            if h or a:
                ok += 1
            else:
                empty += 1
        except Exception:
            fail += 1
        if i % 50 == 0 or i == len(targets):
            OUT.write_text(json.dumps(
                {"fetchedAt": date.today().isoformat(), "since": SINCE,
                 "count": len(done), "injuries": done}, ensure_ascii=False),
                encoding="utf-8")
            el = time.time() - t0
            print(f"  {i}/{len(targets)} · 有伤停 {ok} · 空 {empty} · 失败 {fail} · {el:.0f}s")
        time.sleep(SLEEP)

    OUT.write_text(json.dumps(
        {"fetchedAt": date.today().isoformat(), "since": SINCE,
         "count": len(done), "injuries": done}, ensure_ascii=False),
        encoding="utf-8")
    print(f"落盘 {OUT.name} · 共 {len(done)} 场（有伤停 {ok} · 空 {empty} · 失败 {fail}）")


if __name__ == "__main__":
    main()
