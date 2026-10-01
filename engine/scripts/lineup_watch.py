# -*- coding: utf-8 -*-
r"""lineup_watch.py —— 临场首发+伤停采集器（2026-09-30 建议 2「首发信息是否有市场尚未消化的部分」数据地基）

每次调用跑一个 tick（幂等·快速·Windows 定时任务每 10 分钟一次），只采集不交易、不碰生产流程：
  ① 开赛前 180 分钟 ~ 开赛后 10 分钟：体彩五池赔率快照（HAD/HHAD/CRS/TTG/HAFU），按场 diff 压缩
     （首条全量，其后只记变动键；池/键消失记 null）；每 tick 记 seenTicks（观测连续性，判"无变动"须有 tick 佐证）；
     已记录场不在售时记 closedDetectedAt（停售末价 = 快照链回放末态）
  ② 开赛前 120 分钟内：ESPN 查首发——soccer/all 聚合 scoreboard 按「开赛时刻 ±30 分 + 至少一侧队名命中」对场
     （唯一命中才认），summary 的 rosters[].starter 双方各 ≥10 人视为已公布（未公布时 rosters 为空）；
     首次查到记 lineupSeenAt + lineupSource + 双方首发（名字/id/号码/位置/阵型）+ lineupSeenOdds（当刻五池价）；
     lineupCheckedAt = 最近一次真正查询 ESPN summary 的时刻（与 lineupSeenAt 夹逼公布时点；未对上场恒 null）
  ③ 结算：开赛 ≥150 分钟后按体彩开奖口径（getUniformMatchResultV1）以 matchId 补全场/半场比分
  ④ 伤停轨（2026-10-01 立项·正期望证伪文档唯一残余假设「临场时点档」的 forward 积累）：
     全部在售场（开赛前任意时刻）经体彩 insight getInjurySuspensionV1 采伤停名单——伤停是日尺度演变，
     节流复查（距开赛 >180 分钟每场 ≥120 分钟一次；进终窗后每 tick 查，变更得 10 分钟级时间戳）；
     injChecks[] 记真实查询时刻（判"名单无变"须有查询佐证），名单有变才追加 injSnapshots[]（全量名单·按名排序，
     字段与历史回算器 research/fetch_injuries.py 同构，可与 engine/cache/insight_injuries.json 对齐分析）；
     采集失败计 stat.injErr 不记查询（失败不是观测）；开赛后不再查。
  落盘 data/05-trends/{北京时间开赛日}-lineups.json（matchId 为键·原子写；inj* 为增量键，旧记录缺键自动补）。
  不写 engine/cache/sporttery_matches.json 与 05-trends 的 odds/intel/livescan 文件（会话读写，防竞争）。
  映射覆盖：别名表 espn 字段（俱乐部联赛）；国家队/亚运等无 espn 别名 → lineup 恒 null，espn.reason 记原因，不硬凑。
  多机 tick（Windows 定时任务 + 预测流程手动一发）允许并发：原子写只防损坏，同秒双写最坏丢一个 tick，
  判据的连续性检查（seenTicks/injChecks）可发现缺口。

预注册判定口径（数据攒够才判，禁止事后挑指标；样本门槛：有首发的场次 ≥150 才下结论，三项须同时报告）：
  ① 调价率：lineupSeenAt 之后到停售，体彩 HAD 或 CRS 任一键相对 lineupSeenOdds 变动的场次占比
  ② 信息量：lineupSeenOdds 与停售末价两版 HAD 去水概率对赛果的 log-loss；停售更低 = 首发含信息且体彩会吸收
  ③ 可利用性：首发后赔率缩短（停售价 < lineupSeenOdds 价）的选项，按 lineupSeenOdds 价买 vs 停售价买的回收率；
     仅当体彩首条调价快照晚于 lineupSeenAt ≥1 个 tick（10 分钟）且其间 seenTicks 连续时，才算有可买窗口

预注册判定口径·伤停轨（2026-10-01 预注册·独立于首发轨；变更事件 = 同场相邻 injSnapshots 之差；
样本门槛：伤停快照 ≥300 场且其中临场变更事件 ≥30 才下结论，不足时只出描述统计不下判断）：
  ① 临场变更率：kickoff-24h 之后仍有新增 injSnapshots 的场次占比（分母 = 该窗口有 injChecks 佐证的场）
  ② 调价吸收率：变更事件后 ≤30 分钟内五池 snapshots 出现变动的占比（判"未吸收"须其间 seenTicks 连续）
  ③ 可利用性：判"未吸收"的事件，按变更后首个观测价买受益方向（Δ主力缺阵 主-客<0 买客胜 HAD'a'、
     >0 买主胜'h'、=0 剔除）的回收率，与变更前隐含概率基线对照；单事件效应弱（历史全距仅 5pp），
     本轨只验"市场慢不慢"，不验"信号强不强"

用法：
  python engine/scripts/lineup_watch.py                           # 真实时刻跑一个 tick
  python engine/scripts/lineup_watch.py --now "2026-09-30 16:30"  # 模拟时刻（默认落 scratch/lineup_watch_sim/，不污染数据）
  python engine/scripts/lineup_watch.py --out-dir <目录>           # 改落盘目录
定时任务 football-lineup-watch（pythonw 每 10 分钟）；日志 %LOCALAPPDATA%\football-lineup-watch\lineup_watch.log（仓库外）
开发者 sszhang
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from common import ROOT, load_aliases
from sporttery_fetch import DRAW_RESULT_URL, INJURY_URL, extract_odds, fetch, get_json
from trends_snapshot import atomic_write_json

TRENDS_DIR = ROOT / "data" / "05-trends"
SIM_DIR = ROOT / "engine" / "scripts" / "scratch" / "lineup_watch_sim"
LOG_PATH = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "football-lineup-watch" / "lineup_watch.log"
FILE_SUFFIX = "-lineups.json"
DOC_TYPE = "lineup-timeline"
SCHEMA_VERSION = 1
SOURCE_ESPN = "espn"
SOURCE_RESULT = "sporttery-zqsgkj"
PROTOCOL_REF = "engine/scripts/lineup_watch.py 文件头「预注册判定口径」"

ODDS_PRE_MIN = 180        # 赔率快照窗：开赛前
ODDS_POST_MIN = 10        # 赔率快照窗：开赛后
LINEUP_PRE_MIN = 120      # 首发查询窗：开赛前
MATCH_TOL_MIN = 30        # ESPN 对场开赛时刻容差
MIN_STARTERS = 10         # 单方首发人数下限（视为已公布）
SETTLE_AFTER_MIN = 150    # 开赛多久后查赛果
SETTLE_RETRY_MIN = 60     # 同场赛果重查间隔
SETTLE_LOOKBACK_DAYS = 3  # 结算回看天数
INJURY_QUERY_MIN = 120    # 伤停复查节流：距开赛 >180 分钟时同场最短复查间隔
INJURY_SLEEP = 0.25       # 伤停接口限速（历史回算器同款）
BEIJING = timezone(timedelta(hours=8))
UTC_TO_BEIJING = timedelta(hours=8)
ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer/all/{}"
ESPN_LIMIT = 500          # all 聚合 scoreboard 默认只回 100 场，500 实测回全量
HTTP_TIMEOUT = 20
POOLS = ("had", "hhad", "crs", "ttg", "hafu")
KICK_FMT = "%Y-%m-%d %H:%M"
TS_FMT = "%Y-%m-%dT%H:%M:%S"
TS_TZ = "+08:00"


# ── 时间 ──
def kickoff_of(m: dict) -> datetime | None:
    """体彩 subMatch → 北京时间开赛（naive，matchDate + matchTime）。"""
    d, t = m.get("matchDate"), m.get("matchTime")
    if not (d and t):
        return None
    try:
        return datetime.strptime(f"{d} {t[:5]}", KICK_FMT)
    except ValueError:
        return None


def espn_to_beijing(s: str | None) -> datetime | None:
    """ESPN UTC 时间串（2026-09-29T16:00Z）→ 北京时间 naive。"""
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (dt + UTC_TO_BEIJING).replace(tzinfo=None)


def _minutes(a: datetime, b: datetime) -> float:
    return (a - b).total_seconds() / 60


# ── 赔率快照 diff ──
def slim_odds(m: dict) -> dict:
    """体彩 subMatch → 五池规范赔率（extract_odds 口径：crs 键 '1:0'/'胜其他'，hhad 带 goalLine）。"""
    e = extract_odds(m)
    return {p: e[p] for p in POOLS if e.get(p)}


def diff_pools(prev: dict, cur: dict) -> dict:
    """五池 diff：只留变动键；键或整池消失记 None（replay 时删除）。"""
    out = {}
    for pool in set(prev) | set(cur):
        old, new = prev.get(pool) or {}, cur.get(pool)
        if new is None:
            out[pool] = None
            continue
        ch = {k: v for k, v in new.items() if old.get(k) != v}
        ch.update({k: None for k in old if k not in new})
        if ch:
            out[pool] = ch
    return out


def replay(snaps: list) -> dict:
    """快照链回放 → 最新五池状态。"""
    state = {}
    for s in snaps:
        for pool, ch in (s.get("odds") or {}).items():
            if ch is None:
                state.pop(pool, None)
                continue
            cur = state.setdefault(pool, {})
            for k, v in ch.items():
                if v is None:
                    cur.pop(k, None)
                else:
                    cur[k] = v
    return state


# ── ESPN 首发 ──
def espn_scoreboard(day: str) -> list:
    """ESPN 全联赛聚合 scoreboard（dates=YYYYMMDD）。勿加 UA 头：浏览器 UA 会 403。"""
    r = requests.get(ESPN_BASE.format("scoreboard"), params={"dates": day, "limit": ESPN_LIMIT},
                     timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json().get("events") or []


def espn_summary(event_id: str) -> dict:
    """ESPN 单场 summary（all 口径按 event id 直查，无需联赛 slug）。"""
    r = requests.get(ESPN_BASE.format("summary"), params={"event": event_id}, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()


def zh_to_espn() -> dict:
    """体彩中文队名 → ESPN 队名（别名表 zh/variants/altZh → espn 字段；无 espn 的队不收录）。"""
    out = {}
    for srcs in load_aliases().values():
        espn = srcs.get("espn")
        if not espn:
            continue
        for zh in [srcs.get("zh"), *(srcs.get("variants") or []), *(srcs.get("altZh") or [])]:
            if zh:
                out.setdefault(zh, espn)
    return out


def _team_names(team: dict) -> set:
    return {team.get(k) for k in ("displayName", "shortDisplayName", "name", "location")} - {None, ""}


def match_event(kickoff: datetime, home_zh: str, away_zh: str, events: list, zh_map: dict) -> tuple:
    """体彩场 → ESPN event：开赛时刻 ±30 分内且至少一侧队名命中，唯一命中才认。
    返回 (eventId, 'both'|'home'|'away') 或 (None, 'no_alias'|'no_event'|'ambiguous')。"""
    he, ae = zh_map.get(home_zh), zh_map.get(away_zh)
    if not (he or ae):
        return None, "no_alias"
    hits = []
    for ev in events:
        k = espn_to_beijing(ev.get("date"))
        if k is None or abs(_minutes(k, kickoff)) > MATCH_TOL_MIN:
            continue
        comps = (ev.get("competitions") or [{}])[0].get("competitors") or []
        side = {c.get("homeAway"): _team_names(c.get("team") or {}) for c in comps}
        h_ok = bool(he) and he in side.get("home", set())
        a_ok = bool(ae) and ae in side.get("away", set())
        if h_ok or a_ok:
            hits.append((ev.get("id"), "both" if h_ok and a_ok else ("home" if h_ok else "away")))
    if len(hits) == 1:
        return hits[0]
    return None, "ambiguous" if hits else "no_event"


def parse_lineup(summary: dict) -> dict | None:
    """ESPN summary.rosters → 双方首发；任一方首发 <10 人视为未公布（未开赛场 rosters 为空）。"""
    out = {}
    for ros in summary.get("rosters") or []:
        side = ros.get("homeAway")
        if side not in ("home", "away"):
            continue
        starters = [p for p in ros.get("roster") or [] if p.get("starter")]
        out[side] = {
            "team": (ros.get("team") or {}).get("displayName"),
            "formation": ros.get("formation"),
            "starters": [{"name": (p.get("athlete") or {}).get("displayName"),
                          "id": (p.get("athlete") or {}).get("id"),
                          "jersey": p.get("jersey"),
                          "pos": (p.get("position") or {}).get("abbreviation")} for p in starters],
        }
    ok = all(len((out.get(s) or {}).get("starters") or []) >= MIN_STARTERS for s in ("home", "away"))
    return out if ok else None


# ── 赛果 ──
def sporttery_results(d1: str, d2: str) -> dict:
    """体彩开奖口径赛果（zqsgkj·matchPage=2 区间全量）→ {matchId: {score, halfScore}}，只收已完赛。"""
    d = get_json(DRAW_RESULT_URL, {"matchBeginDate": d1, "matchEndDate": d2, "leagueId": "", "pageSize": 30,
                                   "pageNo": 1, "isFix": 0, "matchPage": 2, "pcOrWap": 1})
    out = {}
    for m in (d.get("value") or {}).get("matchResult") or []:
        if m.get("matchId") and m.get("sectionsNo999"):
            out[str(m["matchId"])] = {"score": m["sectionsNo999"], "halfScore": m.get("sectionsNo1") or None}
    return out


# ── 伤停 ──
def parse_injuries(value: dict) -> dict:
    """insight getInjurySuspensionV1 的 value → {"h": [slim], "a": [slim]}；按名排序保证顺序稳定（变更有可比性）。

    slim 字段与 research/fetch_injuries.py 历史回算器同构：name/pos/injury/susp/apps/starts。
    """
    out = {}
    for side, src in (("h", value.get("home") or {}), ("a", value.get("away") or {})):
        slim = [{"name": x.get("personName"), "pos": x.get("playerPositionDesc"),
                 "injury": x.get("injuryFlag"), "susp": x.get("suspensionFlag"),
                 "apps": x.get("appearanceCnt"), "starts": x.get("startedMatchCnt")}
                for x in (src.get("injuriesAndSuspensionsList") or [])]
        slim.sort(key=lambda p: (p["name"] or "", p["pos"] or ""))
        out[side] = slim
    return out


def insight_injuries(mid: str) -> dict:
    """体彩 insight 伤停名单（单场）。限速在真实采集器内（测试注入假源不睡）。"""
    try:
        return parse_injuries(get_json(f"{INJURY_URL}?sportteryMatchId={mid}").get("value") or {})
    finally:
        time.sleep(INJURY_SLEEP)


# ── 落盘 ──
def doc_path(out_dir: Path, day: str) -> Path:
    return out_dir / f"{day}{FILE_SUFFIX}"


def load_doc(out_dir: Path, day: str) -> dict:
    """当日文件 → dict；缺失建新；损坏先备份为 .corrupt-{时间戳} 再重建（不静默清账）。"""
    p = doc_path(out_dir, day)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            p.replace(p.with_name(p.name + f".corrupt-{datetime.now():%Y%m%d%H%M%S}"))
    return {"date": day, "type": DOC_TYPE, "schemaVersion": SCHEMA_VERSION,
            "protocol": PROTOCOL_REF, "matches": {}}


def _new_record(mid, m: dict, kickoff: datetime) -> dict:
    return {"matchId": mid, "code": m.get("matchNumStr"), "league": m.get("leagueAbbName"),
            "home": m.get("homeTeamAbbName"), "away": m.get("awayTeamAbbName"),
            "kickoff": kickoff.strftime(KICK_FMT), "espn": None,
            "lineupSeenAt": None, "lineupCheckedAt": None, "lineupSource": None,
            "lineup": None, "lineupSeenOdds": None, "snapshots": [], "seenTicks": [],
            "injSnapshots": [], "injChecks": [],
            "closedDetectedAt": None, "result": None, "resultCheckedAt": None}


# ── tick ──
def tick(now: datetime, calc: dict, out_dir: Path, *, scoreboard=espn_scoreboard, summary=espn_summary,
         results=sporttery_results, injury=insight_injuries, zh_map: dict | None = None) -> dict:
    """一个采集 tick：赔率快照 → 伤停快照 → 停售检测 → 首发查询 → 赛果结算 → 原子落盘。返回计数。"""
    ts = now.strftime(TS_FMT) + TS_TZ
    docs, dirty = {}, set()
    stat = {"feed": 0, "inWindow": 0, "snap": 0, "lineupNew": 0, "closed": 0, "settled": 0, "espnErr": 0,
            "inj": 0, "injNew": 0, "injChg": 0, "injErr": 0}

    def doc(day):
        if day not in docs:
            docs[day] = load_doc(out_dir, day)
        return docs[day]

    def existing(day):
        return day in docs or doc_path(out_dir, day).exists()

    feed_ids, cur_odds, need = set(), {}, []
    for block in (calc.get("value") or {}).get("matchInfoList") or []:
        for m in block.get("subMatchList") or []:
            stat["feed"] += 1
            k, mid = kickoff_of(m), m.get("matchId")
            if k is None or mid is None:
                continue
            delta = _minutes(now, k)
            odds_win = -ODDS_PRE_MIN <= delta <= ODDS_POST_MIN
            inj_win = delta <= 0
            if not (odds_win or inj_win):
                continue
            key, day = str(mid), k.date().isoformat()
            feed_ids.add(key)
            if odds_win:
                stat["inWindow"] += 1
            rec = doc(day)["matches"].setdefault(key, _new_record(mid, m, k))
            rec.setdefault("injSnapshots", [])                      # 旧记录（伤停轨上线前）缺键补齐
            rec.setdefault("injChecks", [])
            rec["closedDetectedAt"] = None                          # 曾判停售又回到清单 → 自愈
            if odds_win:
                odds = slim_odds(m)
                cur_odds[key] = odds
                ch = diff_pools(replay(rec["snapshots"]), odds)
                if ch:
                    rec["snapshots"].append({"at": ts, "odds": ch})
                    stat["snap"] += 1
                rec["seenTicks"].append(ts)
                dirty.add(day)
                if rec["lineup"] is None and -LINEUP_PRE_MIN <= delta <= 0:
                    need.append((key, k, rec))
            if inj_win:
                checks = rec["injChecks"]
                due = not checks or delta >= -ODDS_PRE_MIN          # 终窗每 tick；节流窗到期复查
                if not due:
                    last = datetime.strptime(checks[-1][:19], TS_FMT)
                    due = _minutes(now, last) >= INJURY_QUERY_MIN
                if due:
                    try:
                        cur = injury(key)
                    except (requests.RequestException, ValueError):
                        stat["injErr"] += 1
                    else:
                        checks.append(ts)
                        stat["inj"] += 1
                        prev = rec["injSnapshots"][-1]["inj"] if rec["injSnapshots"] else None
                        if cur != prev:
                            rec["injSnapshots"].append({"at": ts, "inj": cur})
                            stat["injChg" if prev is not None else "injNew"] += 1
                        dirty.add(day)

    # 停售检测（清单为空视为接口抖动，不判；出清单=真停售，含开赛自然出窗）
    if stat["feed"]:
        for off in (-1, 0, 1):
            day = (now + timedelta(days=off)).date().isoformat()
            if not existing(day):
                continue
            for key, rec in doc(day)["matches"].items():
                if key not in feed_ids and not rec.get("closedDetectedAt"):
                    rec["closedDetectedAt"] = ts
                    stat["closed"] += 1
                    dirty.add(day)

    # 首发查询
    if need:
        zh_map = zh_to_espn() if zh_map is None else zh_map
        boards = {}

        def events_around(k):
            base = (k - UTC_TO_BEIJING).date()
            evs, seen = [], set()
            for off in (-1, 0, 1):
                d = (base + timedelta(days=off)).strftime("%Y%m%d")
                if d not in boards:
                    boards[d] = scoreboard(d)
                for ev in boards[d]:
                    if ev.get("id") not in seen:
                        seen.add(ev.get("id"))
                        evs.append(ev)
            return evs

        for key, k, rec in need:
            try:
                if not (rec.get("espn") or {}).get("eventId"):
                    if not (zh_map.get(rec["home"]) or zh_map.get(rec["away"])):
                        rec["espn"] = {"eventId": None, "reason": "no_alias"}
                        continue
                    eid, how = match_event(k, rec["home"], rec["away"], events_around(k), zh_map)
                    rec["espn"] = {"eventId": eid, "match": how} if eid else {"eventId": None, "reason": how}
                eid = rec["espn"].get("eventId")
                if eid:
                    rec["lineupCheckedAt"] = ts              # 只记真正查过 summary 的时刻（夹逼公布时点用）
                    lu = parse_lineup(summary(eid))
                    rec["espn"].pop("lastError", None)
                    if lu:
                        rec.update(lineup=lu, lineupSeenAt=ts, lineupSource=SOURCE_ESPN,
                                   lineupSeenOdds=cur_odds.get(key))
                        stat["lineupNew"] += 1
            except (requests.RequestException, ValueError) as e:
                stat["espnErr"] += 1
                rec["espn"] = {**(rec.get("espn") or {}), "lastError": f"{type(e).__name__}: {e}"[:200]}

    # 赛果结算
    pending = []
    for off in range(-SETTLE_LOOKBACK_DAYS, 1):
        day = (now + timedelta(days=off)).date().isoformat()
        if not existing(day):
            continue
        for key, rec in doc(day)["matches"].items():
            if rec.get("result"):
                continue
            k = datetime.strptime(rec["kickoff"], KICK_FMT)
            if _minutes(now, k) < SETTLE_AFTER_MIN:
                continue
            last = rec.get("resultCheckedAt")
            if last and _minutes(now, datetime.strptime(last[:19], TS_FMT)) < SETTLE_RETRY_MIN:
                continue
            pending.append((day, key, rec, k))
    if pending:
        ks = [p[3].date() for p in pending]
        try:
            res = results((min(ks) - timedelta(days=1)).isoformat(), (max(ks) + timedelta(days=1)).isoformat())
        except (requests.RequestException, ValueError) as e:
            res = None
            stat["settleErr"] = type(e).__name__
        if res is not None:
            for day, key, rec, _ in pending:
                rec["resultCheckedAt"] = ts
                if key in res:
                    rec["result"] = {**res[key], "source": SOURCE_RESULT}
                    stat["settled"] += 1
                dirty.add(day)

    if dirty:
        out_dir.mkdir(parents=True, exist_ok=True)
        for day in dirty:
            atomic_write_json(doc_path(out_dir, day), docs[day])
    return stat


def log_line(msg: str) -> None:
    """stdout + 仓库外日志文件（pythonw 无控制台时 print 静默，文件为唯一痕迹）。"""
    line = f"{datetime.now(BEIJING):%Y-%m-%d %H:%M:%S} {msg}"
    print(line)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description="临场首发采集 tick")
    ap.add_argument("--now", help='模拟时刻 "YYYY-MM-DD HH:MM"（北京时间；默认落 scratch 模拟目录）')
    ap.add_argument("--out-dir", help="落盘目录（默认 data/05-trends；--now 时默认 scratch/lineup_watch_sim）")
    args = ap.parse_args()
    now = (datetime.strptime(args.now, KICK_FMT) if args.now
           else datetime.now(BEIJING).replace(tzinfo=None, microsecond=0))
    out_dir = Path(args.out_dir) if args.out_dir else (SIM_DIR if args.now else TRENDS_DIR)
    try:
        calc = fetch()
    except (requests.RequestException, ValueError) as e:
        log_line(f"[lineup_watch] 体彩拉取失败，本轮跳过: {type(e).__name__}: {e}")
        return 0
    stat = tick(now, calc, out_dir)
    log_line(f"[lineup_watch] now={now:%Y-%m-%d %H:%M} out={out_dir} {json.dumps(stat, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
