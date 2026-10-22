# engine/scripts/llm_card.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""llm_card.py — LLM 预测数据卡生成器（spec docs/2026-10-13-llm-predict-design.html）。

五层：market(体彩存档)/intel(insight CLI)/profile(本地画像)/lineup(首发时序)/context(联网检索·会话回写)。
防泄漏三道过滤在 write_context（Task 3）；卡幂等只读（凭证纪律·预测链地基）。
判据预注册 engine/cache/llm-predict-prereg.json。开发者 sszhang"""
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, date, timedelta
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tail_dataset import TAIL_LEAGUE_MAP          # league 中文名→库 key 复用

CARDS_ROOT = ROOT / "engine" / "cache" / "llm_cards"
ARCHIVE_ROOT = ROOT / "engine" / "cache" / "score_odds"
INSIGHT_TIMEOUT = 30


def card_paths(sale_date: str, *, root: Path | None = None) -> Path:
    return (root or CARDS_ROOT) / sale_date


def match_id_index(matches_file: Path) -> dict:
    """sporttery_matches.json 在售清单 → {code: matchId} 反查表（code=matchNumStr 口径）。
    文件缺失/残行（无 code 或无 matchId）诚实跳过，返回空表则 intel 层降级未获取。"""
    f = Path(matches_file)
    if not f.exists():
        return {}
    doc = json.loads(f.read_text(encoding="utf-8"))
    return {m.get("code"): m.get("matchId") for m in doc.get("matches") or []
            if m.get("code") and m.get("matchId") is not None}


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


def _devig_layer(match: dict) -> dict:
    """五池去水概率（llm-autonomy-prereg ourModels §2）。

    市场原始赔率含抽水,直接读易误判「市场认为多少概率」——去水后才是可比的市场概率。
    had/hhad 用 1/o 归一;crs 用 power devig(market_lambda 同口径,与生产链一致)。
    任一池缺失或解析失败→该池 null 留痕(禁伪造)。开发者 sszhang"""
    out = {}
    for pool in ("had", "hhad"):
        o = match.get(pool) or {}
        try:
            vals = {k: float(v) for k, v in o.items() if float(v) > 1.0}
            s = sum(1.0 / v for v in vals.values())
            out[pool] = {k: round(1.0 / v / s, 4) for k, v in vals.items()} if s else None
        except (TypeError, ValueError):
            out[pool] = None
    try:
        import market_lambda as mkl
        crs = {k: float(v) for k, v in (match.get("crs") or {}).items()
               if _isnum(v) and float(v) > 1.0}
        out["crs"] = ({k: round(v, 4) for k, v in mkl.crs_power_devig(crs).items()}
                      if len(crs) >= 20 else None)
    except Exception:                                      # noqa: BLE001 —— 去水失败不拖垮装卡
        out["crs"] = None
    for pool in ("ttg", "hafu"):
        o = match.get(pool) or {}
        try:
            vals = {k: float(v) for k, v in o.items() if _isnum(v) and float(v) > 1.0}
            s = sum(1.0 / v for v in vals.values())
            out[pool] = {k: round(1.0 / v / s, 4) for k, v in vals.items()} if s else None
        except (TypeError, ValueError):
            out[pool] = None
    return out


def _isnum(v) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def _season_of(date_str) -> str | None:
    """日期 → 欧洲赛季码（repo 口径 2526/2627；7 月起算新赛季）。

    H2H 横跨多赛季（不同阵容不同级别）,近期明细跨 90 天也可能跨赛季——无赛季标的
    历史行没法掂量权重（大哥 2026-10-19：历史比赛日期需增加赛季）。开发者 sszhang"""
    m = re.match(r"^(\d{4})-(\d{2})", str(date_str or ""))
    if not m:
        return None
    y, mo = int(m.group(1)), int(m.group(2))
    sy = y if mo >= 7 else y - 1
    return f"{sy % 100:02d}{(sy + 1) % 100:02d}"


def _our_models_layer(sale_date: str, code: str) -> dict:
    """本地已算结论层（大哥 2026-10-16：上下文须含「我们自己算出来的」）。

    装 V3W 实力链比分卡（生产比分端·三层降级来源标记 src + water 闸 + ttgBoom）与 v4b 快照。
    缺失/不可算一律标 status 留痕——诚实缺口优于空值伪装（prereg card.honestGaps）。
    开发者 sszhang"""
    out = {}
    p = ROOT / "data" / "03-predictions" / f"{sale_date}-crs.json"
    if p.exists():
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            row = next((m for m in doc.get("matches") or [] if m.get("code") == code), None)
            # ⚠️ src 必读（2026-10-16 实测教训）：生产比分端是三层降级，src=market_crs 时
            # crsTop5 本质就是市场去水本身（实测 10-16 十一场全 market_crs，与市场 TOP5
            # 差 <0.005）——此时它**不是独立参考**，拿它当第二意见等于重复看赔率。
            # 该层真正的独立信号是 water（三水闸）/ttgBoom（爆量）/src（来源自白）。
            independent = row.get("src") not in ("market_crs", None) if row else None
            # v3.4 市场正身化：crsTop5/lam 是市场复印件（src=market_crs 时与去水全同），
            # 出卡——独立信号只留 water/ttgBoom/src
            out["v3wChain"] = ({"status": "ok",
                                "src": row.get("src"),
                                "water": row.get("water"),
                                "ttgBoom": row.get("ttgBoom"),
                                "leagueWarn": row.get("leagueWarn"),
                                "independentOfMarket": independent,
                                "useNote": ("src=market_crs → 本信号≈市场去水，勿当独立第二"
                                            "意见；独立信号看 water/ttgBoom"
                                            if independent is False else
                                            "src 非 market_crs → 含模型独立成分")}
                               if row else {"status": "该场未在比分卡"})
        except (OSError, json.JSONDecodeError, KeyError):
            out["v3wChain"] = {"status": "读取失败"}
    else:
        out["v3wChain"] = {"status": "未获取"}

    p = ROOT / "data" / "03-predictions" / f"{sale_date}-v4b.json"
    if p.exists():
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            row = next((m for m in doc.get("matches") or [] if m.get("code") == code), None)
            if row:
                out["v4b"] = {"status": "ok", **{k: row.get(k) for k in
                                                 ("pick", "p", "scenario") if k in row}}
            else:
                skip = next((s for s in doc.get("skips") or [] if s.get("code") == code), None)
                out["v4b"] = {"status": "不可算", "reason": skip.get("skipReason")} if skip \
                    else {"status": "该场未在 v4b"}
        except (OSError, json.JSONDecodeError):
            out["v4b"] = {"status": "读取失败"}
    else:
        out["v4b"] = {"status": "未获取"}
    return out


@lru_cache(maxsize=1)
def _match_date_index() -> dict:
    """code → 真实开赛日（sporttery_matches.matchDate）。开发者 sszhang"""
    p = ROOT / "engine" / "cache" / "sporttery_matches.json"
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {m["code"]: str(m.get("matchDate") or "")[:10]
            for m in doc.get("matches") or [] if m.get("code")}


def _kickoff_dt(match: dict, business_date: str | None,
                date_index: dict | None = None) -> str | None:
    """开赛完整时刻 'YYYY-MM-DD HH:MM:SS'（2026-10-16 接入）。

    两个坑叠在一起,都会造成静默错误：
      ① 体彩存档 matchTime **只有时分秒**（实测 '17:00:00'/'00:30:00'）。不拼日期就拿
         纯时间当边界比——'2026-10-16T12:00' vs '17:00:00' 裸比 '2'>'1' 恒真,时间闸会把
         整轮赛前情报误判成「开赛后」全丢。
      ② matchDays[].businessDate 是**销售期**不是开赛日。实测 10-16 存档 45 场里 17 场
         不一致（周五002 挂 businessDate=10-16 而真实开赛 10-17 00:30；周五011 07:00
         同样跨日——故**不能**按「小时<4 则+1 天」硬判,清晨场也跨）。
    权威开赛日取 sporttery_matches.matchDate；缺则退 businessDate（注明近似）；
    两者皆无→None,时间闸退回 createdAt 保守口径（不猜日期）。开发者 sszhang"""
    t = str(match.get("matchTime") or "").strip()
    if not t:
        return None
    if len(t) >= 16 and ("-" in t[:10]):                   # 已是完整时刻（全格式兼容）
        return _norm_ts(t)
    idx = date_index if date_index is not None else _match_date_index()
    d = idx.get(match.get("matchNumStr") or "") or business_date
    return _norm_ts(f"{d} {t}") if d else None


_RECENT_CACHE: dict = {}          # (root, horizon, league) → ID 空间赛果池（一次 gen 只扫一遍）


@lru_cache(maxsize=1)
def _alias_maps() -> tuple[dict, dict]:
    """(zh→id, id→zh) 单一来源=strength_loaders.load_aliases。开发者 sszhang"""
    try:
        import strength_loaders as sl
        al = sl.load_aliases()
    except Exception:                                      # noqa: BLE001
        return {}, {}
    zh2: dict = {}
    id2: dict = {}
    for tid, srcs in al.items():
        if srcs.get("zh"):
            zh2.setdefault(srcs["zh"], tid)
            id2.setdefault(tid, srcs["zh"])
    return zh2, id2


def _norm_to_id(name: str) -> str | None:
    """任意名（fd id/ESPN 显示名/zh/zh变体）→ 规范 id；不可映射 None。

    zh 匹配须含 variants（与 sl.zh_to_id 同口径——『弗洛西诺』非 canonical zh,漏了
    variants 整队近期明细会静默清空）。开发者 sszhang"""
    try:
        import strength_loaders as sl
        al = sl.load_aliases()
    except Exception:                                      # noqa: BLE001
        return None
    if name in al:
        return name
    kebab = str(name).lower().replace(" ", "-").replace("'", "")
    if kebab in al:
        return kebab
    for tid, srcs in al.items():
        e = srcs.get("espn")
        if e and str(e).lower() == str(name).lower():
            return tid
        for zh in [srcs.get("zh"), *(srcs.get("variants") or [])]:
            if zh and zh == name:
                return tid
    return None


def _parse_results_pool(horizon_date: str, days: int, league_zh: str,
                        root: Path) -> list[dict]:
    """近期赛果池（ID 空间统一）：每日回填库+体彩缓存+联赛库 三源合一。

    ① 每日回填库/体彩缓存：zh 队名 → _norm_to_id 归一,映射不了的行丢弃（该队近期
       会以「队名不可映射」如实标注,不造场）；
    ② 联赛库（espn history 刷新·本次起重新纳入——v3.1 前因 fd 断粮止于 09-20 被排除,
       现由 update 流水线对在售联赛增量刷新,是 fd/espn 覆盖联赛最全的近期货源）；
    ③ 精确 (date,hid,aid) 去重 + 近邻去重（3 日内同对阵同比分=双源日期口径差）。
    开发者 sszhang"""
    d0 = date.fromisoformat(horizon_date)
    lo = (d0 - timedelta(days=days)).isoformat()
    hi = (d0 - timedelta(days=1)).isoformat()              # 窗口=开赛日前 1~days 天
    out: dict[tuple, dict] = {}

    def put(day, h, a, hg, ag, src):
        hid, aid = _norm_to_id(h), _norm_to_id(a)
        if not hid or not aid or not (lo <= day <= hi):
            return
        out.setdefault((day, hid, aid),
                       {"date": day, "hid": hid, "aid": aid, "hg": hg, "ag": ag,
                        "src": src})

    for day in [(d0 - timedelta(days=i)).isoformat() for i in range(1, days + 1)]:
        p = root / "data" / "02-results" / f"{day}.json"
        if not p.exists():
            continue
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for m in doc.get("matches") or []:
            parts = str(m.get("match") or "").split(" vs ")
            mm = re.match(r"^\s*(\d+)\s*[-:]\s*(\d+)\s*$", str(m.get("result") or ""))
            if len(parts) == 2 and mm:
                put(day, parts[0], parts[1], int(mm.group(1)), int(mm.group(2)), "daily")
    for f in (root / "engine" / "cache").glob("sporttery_results_*.json"):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for m in doc.get("matches") or []:
            mm = re.match(r"^\s*(\d+)\s*:\s*(\d+)\s*$", str(m.get("score") or ""))
            if mm and str(m.get("matchDate") or "")[:10]:
                put(str(m["matchDate"])[:10], m.get("home"), m.get("away"),
                    int(mm.group(1)), int(mm.group(2)), "cache")
    lg_key = (TAIL_LEAGUE_MAP or {}).get(league_zh)
    if lg_key:
        lp = root / "data" / "02-results" / "league" / f"{lg_key}_matches.json"
        if lp.exists():
            try:
                rows = json.loads(lp.read_text(encoding="utf-8")).get("matches") or []
            except (OSError, json.JSONDecodeError):
                rows = []
            for r in rows:
                put(str(r.get("date"))[:10], r.get("home"), r.get("away"),
                    r.get("hg"), r.get("ag"), "lib")
    rows = sorted(out.values(), key=lambda r: r["date"], reverse=True)
    dedup, seen = [], {}
    for r in rows:
        # 朝向不变键：双源对同一场的主客写法可相反（cache 按体彩主客,lib 按 espn 主客）,
        # 含朝向的键会把同一场当两场双计
        key = (tuple(sorted([r["hid"], r["aid"]])), tuple(sorted([r["hg"], r["ag"]])))
        prev = seen.get(key)
        if prev is not None and abs((date.fromisoformat(prev) -
                                     date.fromisoformat(r["date"])).days) <= 3:
            continue
        seen[key] = r["date"]
        dedup.append(r)
    return dedup


def _pos_map(league_zh: str) -> dict:
    """联赛 espn 表 {对手id: 当前排位}（近期明细 oppPos join 用；无表=空 map）。"""
    lg_key = (TAIL_LEAGUE_MAP or {}).get(league_zh)
    if not lg_key:
        return {}
    p = ROOT / "data" / "00-leagues" / f"{lg_key}.json"
    if not p.exists():
        return {}
    try:
        rows = json.loads(p.read_text(encoding="utf-8")).get("standings") or []
    except (OSError, json.JSONDecodeError):
        return {}
    out = {}
    for r in rows:
        tid = _norm_to_id(r.get("team") or "")
        if tid:
            out[tid] = r.get("pos")
    return out


def _team_recent(team_id: str | None, horizon_date: str, rows: list[dict],
                 n: int = 8) -> dict:
    """单队近期明细（ID 匹配·对手回显 zh 优先）：last N + restDays + 14 天场数
    + 主客场进球产出分（v3.4 分岔六A·联赛库 hg/ag 按主客身份聚合）。"""
    _, id2zh = _alias_maps()
    recent = []
    hg_h = ga_h = hg_a = ga_a = 0
    nh = na = 0
    for r in rows:
        if r["hid"] == team_id or r["aid"] == team_id:
            home = r["hid"] == team_id
            gf, ga_ = (r["hg"], r["ag"]) if home else (r["ag"], r["hg"])
            if home:
                hg_h, ga_h, nh = hg_h + r["hg"], ga_h + r["ag"], nh + 1
            else:
                hg_a, ga_a, na = hg_a + gf, ga_a + ga_, na + 1
            opp = r["aid"] if home else r["hid"]
            recent.append({"date": r["date"], "season": _season_of(r["date"]),
                           "opp": id2zh.get(opp, opp),
                           "ha": "主" if home else "客",
                           "score": f"{gf}-{ga_}",
                           "res": "胜" if gf > ga_ else "平" if gf == ga_ else "负",
                           "src": r["src"]})
            if len(recent) >= n:
                break
    out: dict = {"recent": recent}
    out["homeGfGa"] = ([round(hg_h / nh, 2), round(ga_h / nh, 2), nh] if nh else None)
    out["awayGfGa"] = ([round(hg_a / na, 2), round(ga_a / na, 2), na] if na else None)
    if recent:
        hz = date.fromisoformat(horizon_date)
        out["restDays"] = (hz - date.fromisoformat(recent[0]["date"])).days
        out["matches14d"] = sum(1 for r in recent
                                if (hz - date.fromisoformat(r["date"])).days <= 14)
    else:
        out["restDays"] = None
        out["matches14d"] = None
    return out


def _elo_of(team: str) -> float | None:
    """zh 队名 → clubelo rating（data/01-teams/**；文件缺=未获取,不造数）。"""
    tid = _norm_to_id(team)
    if not tid:
        return None
    hits = sorted((ROOT / "data" / "01-teams").glob(f"*/{tid}.json"))
    if not hits:
        return None
    try:
        t = json.loads(hits[0].read_text(encoding="utf-8"))
        return (t.get("elo") or {}).get("rating")
    except (OSError, json.JSONDecodeError):
        return None


# ── xG 层（v3.5 2026-10-20 动刀①·大哥拍板「先动刀」）──────────────────────
XG_RECENT_N = 6
# espn 库键 → fd 存档名的断裂兜底（2026-10-20 沙箱重跑发现：葡超画像键
# portugal-primeira 而 fd 存档叫 portugal-liga——全量排查唯此一处断裂,
# 其余无存档皆 fd 真不覆盖）。开发者 sszhang
_FD_FILE_FALLBACK = {"portugal-primeira": "portugal-liga"}


def _fd_name_index() -> dict[str, str]:
    """fd 英文名 → 规范 id 倒排（fd 键优先,espn/clubelo/zh/variants 全兜）。
    fd 存档队名如 'Inter'/'Monza',与别名行 fd 键同源（odds_fetch 维护）。开发者 sszhang"""
    try:
        import strength_loaders as sl
        al = sl.load_aliases()
    except Exception:                                      # noqa: BLE001
        return {}
    idx: dict[str, str] = {}
    for tid, srcs in al.items():
        for k in ("fd", "espn", "clubelo", "understat", "zh"):
            v = srcs.get(k)
            if v and v not in idx:                        # 先到先得=fd 键最优先
                idx[v] = tid
        for v in srcs.get("variants") or []:
            if v and v not in idx:
                idx[v] = tid
    return idx


def _xg_side(tid: str | None, matches: list[dict], horizon: str) -> dict | None:
    """单队近 N 场 xG/实进对照（日期倒序·<horizon·排除本场两队行）。"""
    if not tid:
        return None
    rows = []
    for m in matches:
        try:
            d = datetime.strptime(str(m.get("date")), "%d/%m/%Y").date().isoformat()
        except ValueError:
            continue
        if d >= horizon:
            continue
        if _isnum(m.get("hxg")) and _isnum(m.get("axg")):
            rows.append((d, m))
    rows.sort(key=lambda x: x[0], reverse=True)
    xgf = xga = gf = ga = 0.0
    n = 0
    for _, m in rows:
        if m["hid"] == tid:
            xgf, xga = xgf + float(m["hxg"]), xga + float(m["axg"])
            gf, ga = gf + float(m.get("fthg") or 0), ga + float(m.get("ftag") or 0)
        elif m["aid"] == tid:
            xgf, xga = xgf + float(m["axg"]), xga + float(m["hxg"])
            gf, ga = gf + float(m.get("ftag") or 0), ga + float(m.get("fthg") or 0)
        else:
            continue
        n += 1
        if n >= XG_RECENT_N:
            break
    if n < 3:
        return None                                       # 样本太薄不如不给
    out = {"n": n,
           "xgf": round(xgf / n, 2), "xga": round(xga / n, 2),
           "gf": round(gf / n, 2), "ga": round(ga / n, 2),
           "luckG": round((gf - xgf) / n, 2),             # 正=实进超xG(终结热),负=欠账
           "luckA": round((ga - xga) / n, 2)}             # 正=实失超xGA(门将超水平/背运)
    if gf / n < 0.5 and xgf / n >= 1.0:
        out["alert"] = "实进远低于xG:产出欠账,回归在即——禁按『零进球』线性外推"
    return out


def _xg_layer(league_zh: str, home: str, away: str, horizon: str) -> dict:
    """fd xG 层（v3.5·状态切片的解毒剂）：近 N 场 xG/xGA vs 实进/实失。
    002 教训:「卡尔马近5场零进球」若有 xG 可见欠账（零进球但 xG 不低=运气,非能力）,
    臣仍按进攻瘫痪压客 9pt 被反杀。fd 不覆盖联赛（瑞超/巴甲/欧冠等）诚实 no_data。
    开发者 sszhang"""
    lg_key = (TAIL_LEAGUE_MAP or {}).get(league_zh)
    if not lg_key:
        return {"status": "联赛未映射"}
    matches: list[dict] = []
    fd_key = _FD_FILE_FALLBACK.get(lg_key, lg_key)        # 葡超键名断裂兜底
    for season in ("2627", "2526"):                       # 当季优先,季初回捞上季
        p = ROOT / "engine" / "cache" / f"odds_{fd_key}_{season}.json"
        if not p.exists():
            continue
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        ms = doc.get("matches") or []
        matches.extend(ms)
    if not matches:
        return {"status": "no_data（fd 不覆盖或存档缺·odds_fetch 先跑）"}
    idx = _fd_name_index()
    hid = idx.get(home) or _norm_to_id(home)
    aid = idx.get(away) or _norm_to_id(away)
    for m in matches:
        m["hid"] = idx.get(str(m.get("home") or ""))
        m["aid"] = idx.get(str(m.get("away") or ""))
    hs, as_ = _xg_side(hid, matches, horizon), _xg_side(aid, matches, horizon)
    if not hs and not as_:
        return {"status": "no_data（队名对不上或样本<3）"}
    return {"status": "ok", "source": "fd",
            "home": hs, "away": as_,
            "note": "luckG正=实进超xG(终结热),负=欠账待回归——状态切片须先对 xG 折价"}


def _standings_layer(league_zh: str, home: str, away: str) -> dict:
    """联赛积分榜整表（espn·update 流水线刷新）+ 双队行——大哥点名的『当前联赛球队排名』。"""
    lg_key = (TAIL_LEAGUE_MAP or {}).get(league_zh)
    if not lg_key:
        return {"status": "联赛未映射"}
    p = ROOT / "data" / "00-leagues" / f"{lg_key}.json"
    if not p.exists():
        return {"status": "未获取"}
    try:
        rows = json.loads(p.read_text(encoding="utf-8")).get("standings") or []
    except (OSError, json.JSONDecodeError):
        return {"status": "读取失败"}
    if not rows:
        return {"status": "未获取（先跑 espn_fetch standings）"}
    hid, aid = _norm_to_id(home), _norm_to_id(away)
    try:
        import strength_loaders as sl
        al = sl.load_aliases()
    except Exception:                                      # noqa: BLE001
        al = {}

    def row_of(tid):
        if not tid:
            return None
        for r in rows:                                     # espn 显示名 → id → 别名行
            if _norm_to_id(r.get("team") or "") == tid:
                return r
        return None

    return {"status": "ok", "source": "espn", "nTeams": len(rows),
            "tableTop": rows[:6], "home": row_of(hid), "away": row_of(aid),
            "note": "全表见 data/00-leagues/{lg}.json standings（pos/played/W-D-L/GF-GA/pts）"}


def _team_deep_layer(sale_date: str, code: str, league_zh: str,
                     home: str, away: str, business_date: str | None) -> dict:
    """近期明细层：两队 last8+restDays+14天场数+Elo（大哥 2026-10-19『数据不够详细』）。

    horizon=真实开赛日（sporttery matchDate 优先,businessDate 退化）；21 天窗口,三源
    合一（回填库/体彩缓存/联赛库）ID 空间统一去重。开发者 sszhang"""
    horizon = _match_date_index().get(code) or business_date or sale_date
    try:
        horizon = date.fromisoformat(str(horizon)[:10]).isoformat()
    except ValueError:
        return {"status": "horizon 不可解析", "horizon": horizon}
    cache_key = (str(ROOT), horizon, league_zh)
    rows = _RECENT_CACHE.get(cache_key)
    if rows is None:
        rows = _parse_results_pool(horizon, 90, league_zh, ROOT)   # 90 天:21 天够不着
        _RECENT_CACHE[cache_key] = rows                            # 季初轮次,近10场会缺
    hid, aid = _norm_to_id(home), _norm_to_id(away)
    # 排除本场自身：回填库若已含本场赛果（赛后重建卡的场景）,决不能混进「近期」——
    # 那是把答案喂进预测输入。
    if hid and aid:
        rows = [r for r in rows if {r["hid"], r["aid"]} != {hid, aid}]
    deep_home = _team_recent(hid, horizon, rows)
    deep_away = _team_recent(aid, horizon, rows)
    # 对手排位 join（v3.4 分岔四A）：近期形态从输赢序列升级为对手加权形态；
    # 杯赛对手跨联赛查不到 → null 不伪造
    if hid and aid:
        smap = _pos_map(league_zh)
        for t in (deep_home, deep_away):
            for r in t["recent"]:
                r["oppPos"] = smap.get(_norm_to_id(r["opp"]))
    for t, nm in ((deep_home, home), (deep_away, away)):
        if _norm_to_id(nm) is None:
            t["note"] = "队名不可映射（aliases 无此队）"
        elif 0 < len(t["recent"]) < 3:
            # 覆盖稀疏时 restDays 只是上界（真实最近一场可能未入库）——须让读者知情
            t["sparseNote"] = "三源窗口内该队场次<3,restDays 为上界"
    return {
        "horizon": horizon, "windowDays": 90, "poolMatches": len(rows),
        "source": "每日回填库 + 体彩缓存 + 联赛库（espn history 刷新·ID 空间统一去重）",
        "home": {**deep_home, "elo": _elo_of(home)},
        "away": {**deep_away, "elo": _elo_of(away)},
    }


def _drift_layer(sale_date: str, code: str) -> dict:
    """赔率漂移：05-trends 快照链 first→last（R006 的输入面——价涨=市场定价更难中）。"""
    p = ROOT / "data" / "05-trends" / f"{sale_date}-odds.json"
    if not p.exists():
        return {"status": "未获取"}
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "读取失败"}
    seen: dict[str, list[dict]] = {}
    snaps = doc.get("snapshots") or []
    for s in snaps:
        for m in s.get("matches") or []:
            if m.get("code") != code:
                continue
            for pool in ("had", "hhad"):
                o = m.get(pool) or {}
                vals = {k: float(o[k]) for k in ("h", "d", "a")
                        if _isnum(o.get(k)) and float(o[k]) > 1.0}
                if vals:
                    seen.setdefault(pool, []).append(vals)
    if not seen:
        return {"status": "快照无该场"}
    ticks = max(len(v) for v in seen.values())
    if ticks <= 1:
        # 单快照无对照可言——五个 0.0% 的假漂移占位不如一行实话（v3.4 评审修正）
        return {"status": "单调无对照(ticks=1)", "snapshotsAt": [s.get("at") for s in snaps]}
    out: dict = {"ticks": ticks,
                 "snapshotsAt": [s.get("at") for s in snaps if s.get("matches")]}
    for pool, hist in seen.items():
        first, last = hist[0], hist[-1]
        out[pool] = {k: {"first": first[k], "last": last[k],
                         "pct": round((last[k] - first[k]) / first[k] * 100, 1)}
                     for k in first if k in last}
    return out


def _market_lambda_layer(match: dict) -> dict:
    """市场隐含进球期望（层2 反解·ρ=0）——进球带判断的直接参照。

    ttg 总球分布反解 λsum,HAD 去水主胜份额定 λ分配。缺任一池→未获取。开发者 sszhang"""
    try:
        import market_lambda as mkl
        # 键须归一为 "0".."7"（TTG_KEYS 收数字串;裸喂 s0..s7 会被整表滤空,λsum=0 出负 λ）
        ttg = {str(k)[1:]: float(v) for k, v in (match.get("ttg") or {}).items()
               if str(k).startswith("s") and _isnum(v) and float(v) > 1.0}
        had = match.get("had") or {}
        if len(ttg) < 4 or not all(_isnum(had.get(k)) for k in ("h", "d", "a")):
            return {"status": "未获取"}
        lam_sum = mkl.lam_sum_from_ttg(ttg)
        if lam_sum <= 0.1:
            return {"status": "反解失败"}
        p3 = mkl.had_devig(float(had["h"]), float(had["d"]), float(had["a"]))
        lh = mkl.solve_lamh(lam_sum, p3[0])
        lam_a = lam_sum - lh
        if lh <= 0 or lam_a <= 0:
            return {"status": "反解失败"}                  # 负期望进球=输入病,宁可不给
        return {"status": "ok", "role": "market-derived",
                "lamH": round(lh, 2), "lamA": round(lam_a, 2),
                "lamSum": round(lam_sum, 2), "rho": 0.0,
                "note": "市场隐含主客期望进球（去水后反解）——进球带判断的市场参照,非独立意见"}
    except Exception:                                      # noqa: BLE001 —— 缺料不拖垮装卡
        return {"status": "反解失败"}


def _competition_type(league_zh: str) -> dict:
    """赛制性质：画像存在=正牌联赛（带 roundsPlayed）,否则杯赛/次级赛轮（轮换高风险）。"""
    lg_key = (TAIL_LEAGUE_MAP or {}).get(league_zh)
    prof = ROOT / "data" / "00-leagues" / f"{lg_key}.json" if lg_key else None
    if lg_key and prof.exists():
        try:
            doc = json.loads(prof.read_text(encoding="utf-8"))
            rp = doc.get("roundsPlayed") or max(
                (s.get("played") or 0 for s in doc.get("standings") or []), default=None)
        except (OSError, json.JSONDecodeError):
            rp = None
        return {"type": "league", "roundsPlayed": rp}
    return {"type": "cup", "roundsPlayed": None,
            "note": "杯赛/未映射轮——轮换高风险（英锦标赛教训:小组赛大轮换曾零覆盖）"}


def build_card(sale_date: str, match: dict, *, insight_path: Path | None,
               lineups: dict, now: str | None = None,
               business_date: str | None = None) -> dict:
    """match=score_odds 存档行 → 七层数据卡（v3 厚卡·2026-10-16 ourModels+devig 接入）。

    v3 相对 v2 的两处加厚（prereg designPrinciple「充分上下文进」）：
      ① ourModels 层——V3W 实力链/v4b 等本地已算结论（大哥：上下文须含我们自己算出来的）
      ② market 层补齐 hhad/hafu 两池 + devig 去水概率（原仅 had/crs/ttg 原始赔率）
    开发者 sszhang"""
    intel = {"status": "未获取"}
    if insight_path and Path(insight_path).exists():
        doc = json.loads(Path(insight_path).read_text(encoding="utf-8"))
        intel = {"status": "ok", **{k: doc.get(k) for k in ("injuries", "form", "standing", "h2h", "scorers")
                                    if doc.get(k) is not None}}
        # H2H 逐行补赛季标 + 赛季聚合（v3.4 分岔二A:一行分解+近2场;比分解说,不读
        # winner 字段——实测 winner 与比分矛盾(西悉尼0:2悉尼FC winner=home)）
        h2h_rows = ((intel.get("h2h") or {}).get("matches") or [])
        for x in h2h_rows:
            x["season"] = _season_of(x.get("date"))
        home_nm, away_nm = match["home"], match["away"]
        agg: dict[str, dict[str, int]] = {}
        for x in h2h_rows:
            mm = re.match(r"^(\d+)\s*:\s*(\d+)$", str(x.get("score") or ""))
            if not mm:
                continue
            hg, ag = int(mm.group(1)), int(mm.group(2))
            # 视角=当前主队：该行主队==当前主队 → 主场比分即其进失
            is_home = x.get("home") == home_nm
            res = "平" if hg == ag else ("胜" if (hg > ag) == is_home else "负")
            s = agg.setdefault(x.get("season") or "?", {"胜": 0, "平": 0, "负": 0})
            s[res] += 1
        agg_txt = " · ".join(
            f"{s}({sum(v.values())}) " + "".join(f"{k}{n}" for k, n in v.items() if n)
            for s, v in agg.items()) or "无交手"
        if isinstance(intel.get("h2h"), dict):
            intel["h2h"]["seasonAgg"] = f"主队({home_nm})视角 {agg_txt}"
            intel["h2h"]["recent2"] = h2h_rows[:2]
        # standing 瘦身（v3.4 分岔三A）：留 ranking+主客分拆,删与 espn 表重复的原始键
        st_raw = intel.get("standing")

        def _split(side_raw):
            return {"home": {"W": side_raw.get("sHomeWinGoalMatchCnt"),
                             "D": side_raw.get("sHomeDrawMatchCnt"),
                             "L": side_raw.get("sHomeLossGoalMatchCnt")},
                    "away": {"W": side_raw.get("sAwayWinGoalMatchCnt"),
                             "D": side_raw.get("sAwayDrawMatchCnt"),
                             "L": side_raw.get("sAwayLossGoalMatchCnt")}}

        if isinstance(st_raw, dict) and st_raw.get("home"):
            intel["standing"] = {
                "home": {"ranking": st_raw["home"].get("ranking"),
                         **_split(st_raw["home"])},
                "away": {"ranking": st_raw["away"].get("ranking"),
                         **_split(st_raw["away"])}}
        # 伤停×射手交叉 + 射手榜 top5（v3.4 分岔六A）
        key_abs: dict = {}
        for side in ("home", "away"):
            pl = ((intel.get("scorers") or {}).get(side) or {}).get("playerList") or []
            ranked = sorted(pl, key=lambda p: -(p.get("goalCnt") or 0))
            inj_names = {x.get("name") for x in (intel.get("injuries") or {}).get(side) or []}
            key_abs[side] = [{"rank": i, "name": p.get("personName"),
                              "goals": p.get("goalCnt")}
                             for i, p in enumerate(ranked, 1) if p.get("personName") in inj_names]
            if isinstance(intel.get("scorers"), dict) and (intel["scorers"].get(side) or {}):
                intel["scorers"][side]["top5"] = ranked[:5]
        intel["keyAbsences"] = key_abs
    card = {
        "code": match["matchNumStr"], "match": f'{match["home"]} vs {match["away"]}',
        "league": match["league"], "kickoff": match.get("matchTime"), "saleDate": sale_date,
        # 赛制性质标（v3.4 评审修正❶）：判据=画像文件存在性而非映射命中——
        # TAIL_LEAGUE_MAP 收着欧冠/意大利杯等杯赛键,按命中判会把欧战轮标成联赛,
        # 恰恰漏掉轮换风险最高的赛事（英锦标赛教训）
        "competitionType": _competition_type(match["league"]),
        # kickoffAt=完整开赛时刻（matchTime 仅时分秒 + businessDate 日期拼成）；
        # kickoff 原字段保留不动（下游已有消费者）。时间闸只认 kickoffAt。
        "kickoffAt": _kickoff_dt(match, business_date), "businessDate": business_date,
        # 开赛日取自何处：sporttery=权威 matchDate / businessDate=退化近似（销售期,凌晨场
        # 会差一天）/ none=无从得知。时间闸据此判可信度,覆盖报告据此分层。
        "kickoffSrc": ("sporttery" if _match_date_index().get(match["matchNumStr"])
                       else ("businessDate" if business_date else "none")),
        "layers": {
            "market": {"had": match.get("had") or {}, "crs": match.get("crs") or {},
                       "ttg": match.get("ttg") or {}, "hhad": match.get("hhad") or {},
                       "hafu": match.get("hafu") or {},
                       "devig": _devig_layer(match),
                       "drift": _drift_layer(sale_date, match["matchNumStr"]),
                       "oddsUpdatedAt": match.get("oddsUpdatedAt")},
            "intel": intel,
            "profile": {**_profile_layer(match["league"]),
                        "standings": _standings_layer(match["league"],
                                                      match["home"], match["away"])},
            "lineup": _lineup_layer(match["matchNumStr"], lineups),
            "context": {"status": "pending", "entries": []},
            "ourModels": {**_our_models_layer(sale_date, match["matchNumStr"]),
                          "marketLambda": _market_lambda_layer(match),
                          "teamRecent": _team_deep_layer(
                              sale_date, match["matchNumStr"], match["league"],
                              match["home"], match["away"], business_date),
                          # xG 层（v3.5 2026-10-20 动刀①）：状态切片的解毒剂——
                          # 002 教训「零进球」未对 xG 折价即外推,压客 9pt 被反杀
                          "xg": _xg_layer(match["league"], match["home"], match["away"],
                                          str(_match_date_index().get(match["matchNumStr"])
                                              or business_date or sale_date)[:10])},
        },
        "createdAt": now or datetime.now().isoformat(timespec="seconds"),
    }
    card["cardHash"] = card_hash(card)
    return card


def preds_exist(sale_date: str, *, root: Path | None = None) -> bool:
    """该销售日是否已有冻结判断（卡是否已被消费）。

    卡幂等只读是凭证纪律,但「卡已建、判断未出」时卡还不是凭证——此时加厚卡层（如
    2026-10-16 接入 ourModels/kickoffAt）须允许重建,否则新层永远进不去存量卡。
    判断一旦冻结,卡即凭证链一环,任何重建都是篡改。开发者 sszhang"""
    base = Path(root) if root else (ROOT / "engine" / "cache" / "llm_preds")
    return (base / f"{sale_date}.json").exists() or \
           any(base.glob(f"{sale_date}-r*.json")) if base.exists() else False


def save_card(card: dict, sale_date: str, *, root: Path | None = None,
              rebuild: bool = False) -> Path:
    """幂等只读：已存在直接返回（凭证纪律·预测链防事后改）。

    rebuild=True 仅限「判断未冻结」时的加层重建,调用方须先过 preds_exist 校验
    （gen --rebuild 已内置该闸）。开发者 sszhang"""
    d = card_paths(sale_date, root=root)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f'{card["code"]}.json'
    if p.exists() and not rebuild:
        return p
    if p.exists() and rebuild:
        # 重建保全：原卡 createdAt 沿用（建卡时刻是时间闸基准,改它等于改历史）
        try:
            old = json.loads(p.read_text(encoding="utf-8"))
            card["createdAt"] = old.get("createdAt") or card["createdAt"]
            oc = (old.get("layers") or {}).get("context") or {}
            if oc.get("status") in ("ok", "未获取"):      # 语境层终态保留,不重置为 pending
                card["layers"]["context"] = oc
            card["rebuiltAt"] = datetime.now().isoformat(timespec="seconds")
            card["rebuiltFrom"] = old.get("cardHash")
            card["cardHash"] = card_hash(card)
        except (OSError, json.JSONDecodeError):
            pass
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(card, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)         # 原子写：replace 而非 rename——Windows 下 rename 不覆盖已存在
    return p                # 目标(WinError 183)，重建路径必走 replace（write_context 同）


def gen(sale_date: str, *, rebuild: bool = False) -> int:
    """CLI 主流程：当日存档→逐场 intel→存卡→待检索清单。

    rebuild=True：存量卡加层重建（仅判断未冻结时允许,否则硬拒——卡已被消费即凭证）。"""
    if rebuild and preds_exist(sale_date):
        raise SystemExit(
            f"[llm_card] 拒绝重建 {sale_date}：该日判断已冻结"
            f"（engine/cache/llm_preds/{sale_date}.json 存在）——卡已是凭证链一环,"
            f"重建等于篡改。新层只对此后新建的卡生效。")
    # v3.4 分岔四A：先 best-effort 跑一发 lineup_watch——晚场轮首发或已公布,
    # 卡能吃到就吃；失败/超时不熔断出卡
    try:
        subprocess.run([sys.executable, str(ROOT / "engine" / "scripts" / "lineup_watch.py")],
                       timeout=300, check=False)
    except Exception:                                      # noqa: BLE001 —— fail-open
        print("[llm_card] lineup tick 跳过（超时/失败,首发层将如实标注）")
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
    mid_index = match_id_index(ROOT / "engine" / "cache" / "sporttery_matches.json")
    for day in doc.get("matchDays") or []:
        for m in day.get("matches") or []:
            # sellStatus 不过滤：当日存档即当日清单（2026-10-13 review 裁定·真实值域 int 1/2 与字符串臆造不符）
            mid = mid_index.get(m["matchNumStr"])
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
            card = build_card(sale_date, m, insight_path=insight_path, lineups=lineups,
                              business_date=day.get("businessDate"))
            save_card(card, sale_date, rebuild=rebuild)
            print(f'[card] {card["code"]} {card["match"]} | 建议检索: "{card["match"]} injury news preview"')
    return 0


# ── 语境层回写 + 防赛后泄漏三道过滤（spec review R1）───────────────────────
import re

POST_MATCH_PATTERNS = [
    r"\bFT\b", r"full[\s-]?time", r"final\s+score", r"match\s+report",
    r"终场", r"完场", r"全场结束", r"\d+\s*[-:]\s*\d+\s*(完|结束)",
]
QUERY_WORDS = ("injury news", "team news", "preview", "predicted lineup")   # 检索建议词（gen 已用）


def _norm_ts(s: str) -> str:
    """时刻串规范化到 'YYYY-MM-DD HH:MM:SS' 以便字典序==时序。

    两种来源混用: kickoff='2026-10-17 00:30'(空格·无秒) / publishedAt ISO 带 'T'。
    'T'(0x54) 字典序大于空格(0x20),裸比会把同日赛前消息误判成晚于开赛。开发者 sszhang"""
    t = str(s or "").strip().replace("T", " ")
    t = t.split("+")[0].split("Z")[0].strip()              # 丢时区后缀（本地口径统一）
    if len(t) == 16:                                       # 'YYYY-MM-DD HH:MM' → 补秒
        t += ":00"
    return t


def _is_post_match(entry: dict) -> bool:
    text = f'{entry.get("title") or ""} {entry.get("excerpt") or ""}'
    return any(re.search(p, text, re.IGNORECASE) for p in POST_MATCH_PATTERNS)


def write_context(sale_date: str, code: str, entries: list[dict], *,
                  root: Path | None = None) -> dict:
    """三道过滤：赛后词丢弃 / 发布时间晚于卡 createdAt 丢弃 / 缺 url 或 publishedAt 丢弃。
    写入后 context=终态（status=ok）禁止二次改写，cardHash 重算。开发者 sszhang"""
    p = card_paths(sale_date, root=root) / f"{code}.json"
    card = json.loads(p.read_text(encoding="utf-8"))
    ctx = card["layers"]["context"]
    if ctx.get("status") in ("ok", "未获取"):
        raise RuntimeError(f"[write_context] {code} 语境层已终态（ok/未获取两态均终态），禁止改写（凭证纪律）")
    created = card.get("createdAt") or ""
    accepted, dropped = [], []
    for e in entries:
        if not (e.get("url") and e.get("publishedAt")):
            dropped.append({"reason": "缺url/publishedAt", "title": e.get("title")}); continue
        # 时间闸（2026-10-16 修·原口径挡错了东西）：
        #   旧=发布晚于卡 createdAt 即丢。但要防的是**赛后**信息,不是**建卡后**信息——
        #   卡建于上午、伤停/首发消息多在赛前数小时发,旧口径把最有价值的临场情报全挡掉
        #   （实测 10-16 十一场 context 全 pending,此闸是成因之一）。
        #   新=以开赛时刻为界: 发布 < kickoff 即收（赛前信息无泄漏风险,晚于建卡无妨）；
        #   发布 ≥ kickoff 丢（真泄漏风险）。kickoff 缺失退回 createdAt 旧口径（保守）。
        # 只认 kickoffAt（完整时刻）；旧卡无此字段或拼接失败 → 退回 createdAt 保守口径。
        # 禁用 kickoff 原字段：它仅时分秒（'17:00:00'），裸比会让整轮情报误判为赛后全丢。
        ko = str(card.get("kickoffAt") or "")
        bound, bname = (ko, "开赛时刻") if len(ko) >= 16 else (created, "卡createdAt")
        # 时刻串规范化：kickoff="2026-10-17 00:30"(空格分隔) vs publishedAt ISO 带 'T',
        # 裸字符串比较会因 'T'(0x54) > ' '(0x20) 误判——统一去 T/补秒再比
        if _norm_ts(e["publishedAt"]) >= _norm_ts(bound):
            dropped.append({"reason": f"发布不早于{bname}（泄漏风险）",
                            "title": e.get("title"), "publishedAt": e["publishedAt"],
                            "bound": bound}); continue
        if _is_post_match(e):
            dropped.append({"reason": "赛后特征词", "title": e.get("title")}); continue
        accepted.append({"title": e.get("title"), "url": e["url"],
                         "publishedAt": e["publishedAt"],
                         "fetchedAt": datetime.now().isoformat(timespec="seconds"),
                         "excerpt": (e.get("excerpt") or "")[:600]})
    ctx["status"] = "ok" if accepted else "未获取"
    ctx["entries"] = accepted
    card["cardHash"] = card_hash(card)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(card, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)   # 原子写：Windows 下 rename 不覆盖已存在目标(FileExistsError)，replace 才跨平台
    return {"accepted": len(accepted), "dropped": dropped}


def _en_hint(card: dict) -> str:
    """英文检索提示：卡只有中文队名,给联赛英文名辅助会话构造检索词。

    不做队名翻译（本地无可靠中英队名表,硬猜会检索到错队）——给联赛英文名 + 中文队名,
    由会话自行判断。联赛表缺则留空。开发者 sszhang"""
    return (TAIL_LEAGUE_MAP or {}).get(card.get("league") or "") or ""


def context_todo(sale_date: str, *, root: Path | None = None) -> list[dict]:
    """语境层待检索清单（闭环第一环·2026-10-16）。

    gen 只在 stdout 打印建议词,过后无从得知哪些场还没查——本函数扫卡给出结构化待办,
    并区分三态: pending=未查 / ok=有收录 / 未获取=查过无货(终态,不重复查)。
    开发者 sszhang"""
    d = card_paths(sale_date, root=root)
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.json")):
        try:
            card = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        ctx = card.get("layers", {}).get("context") or {}
        out.append({
            "code": card.get("code"), "match": card.get("match"),
            "league": card.get("league"), "leagueEn": _en_hint(card),
            "kickoff": card.get("kickoff"), "kickoffAt": card.get("kickoffAt"),
            "createdAt": card.get("createdAt"),
            # 旧卡（2026-10-16 前）无 kickoffAt → 时间闸退回 createdAt 口径,赛前晚发的
            # 临场情报会被丢;此标记让覆盖率报告能区分「真无货」与「闸太严」
            "timeGate": "kickoffAt" if card.get("kickoffAt") else "createdAt(旧卡降级)",
            "status": ctx.get("status"), "nEntries": len(ctx.get("entries") or []),
            "queries": [f'{card.get("match")} {w}' for w in QUERY_WORDS],
            "todo": ctx.get("status") == "pending",
        })
    return out


def context_status(sale_date: str, *, root: Path | None = None) -> dict:
    """语境层覆盖报告 + 七层缺口汇总（prereg card.honestGaps 的可见化）。

    刻意统计「因发布晚于卡 createdAt 被丢弃」——该过滤项对早建卡的轮次会吃掉大量赛前
    临场情报(卡建于上午、伤停消息多在赛前数小时发),覆盖率低时须看此项是真无货还是被滤掉。
    开发者 sszhang"""
    rows = context_todo(sale_date, root=root)
    d = card_paths(sale_date, root=root)
    gaps = {}
    for p in sorted(d.glob("*.json")) if d.exists() else []:
        try:
            card = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        ly = card.get("layers") or {}
        for name, probe in (
                ("intel", lambda l: (l.get("intel") or {}).get("status") == "ok"),
                ("lineup", lambda l: (l.get("lineup") or {}).get("status") == "ok"),
                ("context", lambda l: (l.get("context") or {}).get("status") == "ok"),
                ("v3wChain", lambda l: (l.get("ourModels") or {})
                 .get("v3wChain", {}).get("status") == "ok"),
                ("v4b", lambda l: (l.get("ourModels") or {})
                 .get("v4b", {}).get("status") == "ok"),
                ("devigCrs", lambda l: bool((l.get("market") or {})
                                            .get("devig", {}).get("crs"))),
        ):
            g = gaps.setdefault(name, {"ok": 0, "miss": 0})
            g["ok" if probe(ly) else "miss"] += 1
    return {
        "saleDate": sale_date, "nCards": len(rows),
        "context": {s: sum(1 for r in rows if r["status"] == s)
                    for s in ("pending", "ok", "未获取")},
        "layerCoverage": gaps,
        "note": "context=pending 即从未检索；未获取=查过无货(终态)。"
                "覆盖率低先查 write_context dropped 的『发布晚于卡createdAt』项",
    }


def _attribution_block(card: dict) -> list[str]:
    """归因数据节（v3.7·设计§四）：自动注入三样+按需菜单。只读查询·卡零写入。开发者 sszhang"""
    import attribution_query as aq
    L = ["── 归因数据（自动注入 + 按需菜单）──────"]
    league = card.get("league") or ""
    r = aq.run_query(["formats", league])
    if r.get("ok"):
        f = r["data"]
        notes = f.get("notes") or ""
        seg = (f"  赛制: {league}={f.get('type', '?')}·"
               f"{'有升降级' if (f.get('relegation') or {}).get('count') else '无升降级'}")
        L.append(seg + f"·{notes}" if notes else seg)
    else:
        L.append(f"  赛制: {league} 档案缺（可查 formats 确认）")
    home_nm, away_nm = (card.get("match") or " vs ").split(" vs ")[:2]
    rd = aq.run_query(["derby", home_nm, away_nm])
    if rd.get("ok"):
        d = rd["data"]
        L.append(f"  德比: {'是·尾格系数' + str(d.get('tailScale')) if d.get('isDerby') else '否'}")
    for nm in (home_nm, away_nm):
        rs = aq.run_query(["script", nm, "10"])
        if rs.get("ok"):
            s = rs["data"]
            dist = " ".join(f"{k}×{v}" for k, v in
                            sorted(s["dist"].items(), key=lambda kv: -kv[1])[:4])
            L.append(f"  剧本指纹: {nm}近{s['n']}: {dist}")
        else:
            L.append(f"  剧本指纹: {nm} 无账本覆盖（fd 外联赛或缺档）")
    L.append("  ─ 可查（判断中需要时让会话代查）────")
    L.append("  formats <联赛> | derby <A> <B> | freq <t1-t6> |")
    L.append("  script <队> [N] | h2h-dist <A> <B> | bill <日期> <码> | dim <E1..E5>")
    return L


def _view(sale_date: str, code: str) -> str:
    """阅读版渲染（v3.4 评审修正❹·卡面定序落地）：市场正身→判断材料→缺口置尾。"""
    p = card_paths(sale_date) / f"{code}.json"
    card = json.loads(p.read_text(encoding="utf-8"))
    ly = card["layers"]
    mk, om = ly["market"], ly["ourModels"]
    L = [f"◆ {card['code']} {card['match']} {card['league']} "
         f"开赛{card.get('kickoffAt') or card.get('kickoff')} "
         f"[{(card.get('competitionType') or {}).get('type', '?')}]"]
    L.append("── 市场正身（基线,非答案）────────")
    L.append(f"  HAD {mk['had']}  去水 {(mk['devig'] or {}).get('had')}")
    L.append(f"  TTG去水 {(mk['devig'] or {}).get('ttg')}")
    dcrs = sorted(((mk['devig'] or {}).get('crs') or {}).items(), key=lambda kv: -kv[1])[:8]
    L.append("  CRS去水TOP8: " + " ".join(f"{k}={v}" for k, v in dcrs))
    lam = om.get("marketLambda") or {}
    if lam.get("status") == "ok":
        L.append(f"  λ[市场衍生] 主{lam['lamH']} 客{lam['lamA']}")
    dr = mk.get("drift") or {}
    if dr.get("ticks") and dr.get("ticks") > 1:
        seg = " ".join(f"{k} {v['first']}→{v['last']}({v['pct']:+}%)"
                       for k, v in (dr.get("had") or {}).items())
        L.append(f"  漂移(ticks={dr['ticks']}): {seg}")
    else:
        L.append(f"  漂移: {dr.get('status', '未获取')}")
    v3 = om.get("v3wChain") or {}
    boom = v3.get("ttgBoom")
    L.append(f"  独立信号: water={v3.get('water')} "
             f"ttgBoom={round(boom, 4) if isinstance(boom, (int, float)) else boom} "
             f"src={v3.get('src')}")
    L.append("── 判断材料 ────────────────")
    it = ly["intel"]
    if it.get("status") == "ok":
        # △待核验（v3.5 动刀②·降视觉权重）：⚠→△+措辞。001/005 教训——名单级伤停
        # 无伤位/对位上下文,单看名单两场推断方向皆反（防线残对哑火锋线≠破）。
        # 降权非禁用:真伤停仍须陈述,但禁单独作叫板依据（须配对位分析）
        for side, zh in (("home", "主"), ("away", "客")):
            for a in (it.get("keyAbsences") or {}).get(side) or []:
                L.append(f"  △{zh}伤停×射手(待核验·名单级,伤位/对位未知,"
                         f"禁单独作叫板依据): {a['name']}(射手榜第{a['rank']}·{a['goals']}球)")
        h = it.get("h2h") or {}
        L.append(f"  H2H: {h.get('seasonAgg')}")
        L.append("  近2: " + "；".join(
            f"{x.get('date')}({x.get('season')}) {x.get('home')}{x.get('score')}{x.get('away')}"
            for x in h.get("recent2") or []))
        st = it.get("standing") or {}
        for side, zh in (("home", "主"), ("away", "客")):
            s = st.get(side) or {}
            if s:
                L.append(f"  {zh}排名{s.get('ranking')} 赛季主客分 "
                         f"主{s.get('home')} 客{s.get('away')}")
        ps = ((ly.get("profile") or {}).get("standings") or {})
        for side, zh in (("home", "主"), ("away", "客")):
            r = ps.get(side)
            if r:
                L.append(f"  {zh}榜行: 第{r['pos']} {r['played']}赛 {r['won']}-{r['drawn']}"
                         f"-{r['lost']} 进{r['gf']}失{r['ga']} {r['pts']}分")
        tr = om.get("teamRecent") or {}
        home_nm, away_nm = card["match"].split(" vs ")
        for side, name in (("home", home_nm), ("away", away_nm)):
            t = tr.get(side) or {}
            L.append(f"  {name} rest={t.get('restDays')} "
                     f"主均进失{t.get('homeGfGa')} 客均进失{t.get('awayGfGa')} elo={t.get('elo')}")
            rec = t.get("recent") or []
            # 摘要行前置（v3.5 动刀③·状态切片降版面）：001/002 教训——逐场 8 行占卡面
            # 半壁,引用又最顺手,成了事实上的主材料（0/5 战绩）。摘要+对手均位前置,
            # 逐场退为查证层。结构证据（主客分拆/rest/H2H同季）才是 4/5 的真金
            if rec:
                w = sum(1 for r in rec if r.get("res") == "胜")
                d_ = sum(1 for r in rec if r.get("res") == "平")
                l_ = sum(1 for r in rec if r.get("res") == "负")
                opps = [r.get("oppPos") for r in rec if isinstance(r.get("oppPos"), int)]
                gf_all = ga_all = 0
                for r in rec:
                    try:
                        # score 即该队视角「进-失」（_team_recent 已折算）,勿再按 ha 翻转
                        a_, b_ = (int(x) for x in str(r.get("score")).split("-"))
                        gf_all, ga_all = gf_all + a_, ga_all + b_
                    except ValueError:
                        pass
                opp_avg = f" 对手均位{round(sum(opps) / len(opps), 1)}" if opps else ""
                L.append(f"    摘要 近{len(rec)}场 {w}胜{d_}平{l_}负 进{gf_all}失{ga_all}"
                         f"{opp_avg}（切片证据·叫板须对 xG 折价）")
            for r in rec:
                pos = r.get("oppPos")
                L.append(f"    {r['date']}({r.get('season')}) {r['ha']}vs {r['opp']}"
                         f"[第{pos if pos else '?'}名] {r['score']} {r['res']}")
        # xG 层（v3.5 动刀①）：状态切片的解毒剂——零进球≠零能力,欠账会回归
        xg = om.get("xg") or {}
        if xg.get("status") == "ok":
            for side, zh in (("home", "主"), ("away", "客")):
                s = xg.get(side)
                if s:
                    alert = f" ← {s['alert']}" if s.get("alert") else ""
                    L.append(f"  xG[fd]{zh} 近{s['n']}场 xG进{s['xgf']}/失{s['xga']}"
                             f" 实进{s['gf']}/失{s['ga']} luckG{s['luckG']:+}{alert}")
        else:
            L.append(f"  xG[fd]: {xg.get('status')}")
    L.extend(_attribution_block(card))
    L.append("── 缺口（如实）────────────")
    L.append(f"  lineup={(ly.get('lineup') or {}).get('status')}  "
             f"context={(ly.get('context') or {}).get('status')}  "
             f"intel={it.get('status')}  "
             f"v4b={(om.get('v4b') or {}).get('status')}  "
             f"devigCrs={'有' if (mk.get('devig') or {}).get('crs') else '无'}")
    return "\n".join(L)


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="LLM 预测数据卡生成器")
    ap.add_argument("cmd", choices=["gen", "context-todo", "context-status", "view"])
    ap.add_argument("sale_date")
    ap.add_argument("code", nargs="?", default=None, help="view 用场次码")
    ap.add_argument("--rebuild", action="store_true",
                    help="存量卡加层重建（判断已冻结则硬拒）")
    args = ap.parse_args()
    if args.cmd == "view":
        if not args.code:
            raise SystemExit("[llm_card] view 需场次码：view <sale_date> <code>")
        print(_view(args.sale_date, args.code))
        return 0
    if args.cmd == "context-todo":
        rows = [r for r in context_todo(args.sale_date) if r["todo"]]
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        print(f"[context-todo] 待检索 {len(rows)} 场", file=sys.stderr)
        return 0
    if args.cmd == "context-status":
        print(json.dumps(context_status(args.sale_date), ensure_ascii=False, indent=1))
        return 0
    rc = gen(args.sale_date, rebuild=args.rebuild)
    try:                                   # 规则简报（rules-registry）：best-effort 不熔断出卡
        import rule_brief
        out = rule_brief.compile_brief(args.sale_date)
        print(f"[llm_card] 规则简报 → {out}（填格前必读）")
    except Exception as e:                 # noqa: BLE001
        print(f"[llm_card] 规则简报跳过：{e}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
