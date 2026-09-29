# -*- coding: utf-8 -*-
"""体彩历史赔率采集：开奖场次 → getFixedBonusV1 全池历史赔率 + 赛果。

动机（2026-09-29 大哥指令"体彩网站有历史赔率数据，去拉一下"）：crs_sim_invest
只有 29 天 score_odds 存档（542 腿 / ≥34% 集中度档仅 97 腿），样本不足以判定
集中度闸门是否真有效。本脚本把样本扩到年级别。

数据源（2026-09-29 实测确认）：
- getUniformMatchResultV1.qry → 开奖场次列表（matchId / 赛果 sectionsNo999 / HAD 赔率）
- getFixedBonusV1.qry?matchId= → oddsHistory.{crsList,hadList,ttgList,hafuList}
  · crsList 为赔率变动快照序列（含 updateDate/updateTime），实测 3-11 条/场
  · 键位格式 s01s01=1:1、s-1sh/s-1sd/s-1sa=其他主胜/平/客胜
  · 带 f 后缀的同名键是涨跌标记（0/1/-1）非赔率，必须剔除
  · 实测可回溯至少 3 年（2023-09 仍有数据）

纪律：
- 快照取"开赛前最后一条"（updateDate+updateTime 排序）——盲测口径，不用赛后快照
- 断点续采：已落盘的 matchId 跳过，中断可重跑
- 限速：默认 0.35s/请求，避免打爆官方接口

用法：
  python engine/scripts/sporttery_hist_odds.py --from 2025-09-01 --to 2026-09-28
  python engine/scripts/sporttery_hist_odds.py --from 2025-09-01 --to 2026-09-28 --resume

开发者 sszhang
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from sporttery_fetch import API_BASE, DRAW_RESULT_URL, get_json

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "engine" / "cache" / "hist_odds"
BONUS_URL = API_BASE + "/getFixedBonusV1.qry"
SEG_DAYS = 3          # 开奖列表分段（pageSize=30 上限，每日 10-80 场故取 3 天）
SLEEP = 0.35


def parse_crs_snapshot(snap: dict) -> dict:
    """s01s01 → "1:1" 赔率字典。剔除 f 涨跌标记键与非赔率字段。"""
    out = {}
    for k, v in snap.items():
        if not k.startswith("s") or k.endswith("f"):
            continue
        body = k[1:]
        if body.startswith("-1s"):
            # s-1sh/s-1sd/s-1sa = 其他主胜/平/客胜（聚合项，非具体比分）
            out[f"other_{body[3:]}"] = _f(v)
            continue
        if "s" not in body:
            continue
        a, b = body.split("s", 1)
        try:
            out[f"{int(a)}:{int(b)}"] = _f(v)
        except ValueError:
            continue
    return {k: v for k, v in out.items() if v}


def _f(v):
    try:
        f = float(v)
        return f if f > 1.0 else None
    except (TypeError, ValueError):
        return None


def pick_prelive(snaps: list[dict]) -> dict | None:
    """取开赛前最后一条快照（盲测口径）。按 updateDate+updateTime 排序取末条。"""
    if not snaps:
        return None
    def key(s):
        return (str(s.get("updateDate") or ""), str(s.get("updateTime") or ""))
    return sorted(snaps, key=key)[-1]


def list_draws(d0: date, d1: date) -> list[dict]:
    """区间开奖场次。"""
    out = []
    cur = d0
    while cur <= d1:
        end = min(cur + timedelta(days=SEG_DAYS - 1), d1)
        try:
            d = get_json(DRAW_RESULT_URL,
                         {"matchBeginDate": cur.isoformat(), "matchEndDate": end.isoformat(),
                          "leagueId": "", "pageSize": 30, "pageNo": 1, "isFix": 0,
                          "matchPage": 2, "pcOrWap": 1})
            for m in (d.get("value", {}).get("matchResult") or []):
                if not m.get("matchId") or not m.get("sectionsNo999"):
                    continue
                out.append({"matchId": str(m["matchId"]),
                            "code": m.get("matchNumStr"),
                            "date": m.get("matchDate"),
                            "league": m.get("leagueNameAbbr"),
                            "home": m.get("homeTeam"), "away": m.get("awayTeam"),
                            "score": m.get("sectionsNo999"),
                            "halfScore": m.get("sectionsNo1"),
                            "had": {"h": _f(m.get("h")), "d": _f(m.get("d")),
                                    "a": _f(m.get("a"))},
                            "single": m.get("bettingSingle")})
        except Exception as e:
            print(f"  [warn] 列表 {cur}~{end} 失败 {type(e).__name__}", file=sys.stderr)
        cur = end + timedelta(days=1)
        time.sleep(SLEEP)
    return out


def fetch_odds(match_id: str) -> dict | None:
    """单场历史赔率：返回开赛前快照的 CRS/TTG 赔率。"""
    try:
        b = get_json(BONUS_URL, {"matchId": match_id})
    except Exception:
        return None
    oh = (b.get("value") or {}).get("oddsHistory") or {}
    crs_snaps = oh.get("crsList") or []
    ttg_snaps = oh.get("ttgList") or []
    crs = pick_prelive(crs_snaps)
    ttg = pick_prelive(ttg_snaps)
    if not crs:
        return None
    # crsOpen = 最早快照（开盘）。封盘/开盘之比 = 赔率移动方向，反映资金流
    # 而非公开信息，是采集库里唯一非"公开信息"类特征。
    first = sorted(crs_snaps, key=lambda s: (str(s.get("updateDate") or ""),
                                             str(s.get("updateTime") or "")))[0]
    return {"crs": parse_crs_snapshot(crs),
            "crsOpen": parse_crs_snapshot(first),
            "crsSnapshots": len(crs_snaps),
            "crsUpdate": f'{crs.get("updateDate")} {crs.get("updateTime")}',
            "crsOpenTime": f'{first.get("updateDate")} {first.get("updateTime")}',
            "ttg": {k[1:]: _f(v) for k, v in (ttg or {}).items()
                    if k.startswith("s") and not k.endswith("f") and _f(v)}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d0", required=True)
    ap.add_argument("--to", dest="d1", required=True)
    ap.add_argument("--resume", action="store_true", help="跳过已落盘 matchId")
    ap.add_argument("--limit", type=int, default=0, help="最多采几场（0=不限）")
    a = ap.parse_args()

    d0 = date.fromisoformat(a.d0)
    d1 = date.fromisoformat(a.d1)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"crs_hist_{a.d0}_{a.d1}.json"

    done = {}
    if a.resume and out_path.exists():
        try:
            done = {m["matchId"]: m for m in
                    json.loads(out_path.read_text(encoding="utf-8")).get("matches", [])}
            print(f"[resume] 已有 {len(done)} 场，跳过重采")
        except Exception:
            done = {}

    print(f"[1/2] 拉开奖场次 {d0} ~ {d1} …")
    draws = list_draws(d0, d1)
    print(f"      得 {len(draws)} 场已开奖")

    todo = [m for m in draws if m["matchId"] not in done]
    if a.limit:
        todo = todo[: a.limit]
    print(f"[2/2] 逐场取历史赔率（待采 {len(todo)} 场，限速 {SLEEP}s）…")

    got = dict(done)
    ok = fail = 0
    for i, m in enumerate(todo, 1):
        o = fetch_odds(m["matchId"])
        if o and len(o["crs"]) >= 20:
            got[m["matchId"]] = {**m, **o}
            ok += 1
        else:
            fail += 1
        if i % 50 == 0 or i == len(todo):
            print(f"      {i}/{len(todo)} · 成功 {ok} 失败 {fail}")
            out_path.write_text(json.dumps(
                {"range": [a.d0, a.d1], "count": len(got),
                 "matches": list(got.values())}, ensure_ascii=False, indent=1),
                encoding="utf-8")
        time.sleep(SLEEP)

    out_path.write_text(json.dumps(
        {"range": [a.d0, a.d1], "count": len(got),
         "matches": list(got.values())}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n落盘 {out_path.relative_to(ROOT)} · 共 {len(got)} 场"
          f"（本次新增 {ok}，失败 {fail}）")


if __name__ == "__main__":
    main()
