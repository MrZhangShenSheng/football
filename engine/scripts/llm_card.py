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
            out["v3wChain"] = ({"status": "ok", "model": doc.get("model"),
                                "src": row.get("src"), "lam": row.get("lam"),
                                "crsTop5": row.get("crs_top5"),
                                "ttgBoom": row.get("ttgBoom"), "water": row.get("water"),
                                "leagueWarn": row.get("leagueWarn"),
                                "independentOfMarket": independent,
                                "useNote": ("src=market_crs → crsTop5≈市场去水，勿当独立第二"
                                            "意见；独立信号看 water/ttgBoom"
                                            if independent is False else
                                            "src 非 market_crs → crsTop5 含模型独立成分")}
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


def build_card(sale_date: str, match: dict, *, insight_path: Path | None,
               lineups: dict, now: str | None = None) -> dict:
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
    card = {
        "code": match["matchNumStr"], "match": f'{match["home"]} vs {match["away"]}',
        "league": match["league"], "kickoff": match.get("matchTime"), "saleDate": sale_date,
        "layers": {
            "market": {"had": match.get("had") or {}, "crs": match.get("crs") or {},
                       "ttg": match.get("ttg") or {}, "hhad": match.get("hhad") or {},
                       "hafu": match.get("hafu") or {},
                       "devig": _devig_layer(match),
                       "oddsUpdatedAt": match.get("oddsUpdatedAt")},
            "intel": intel,
            "profile": _profile_layer(match["league"]),
            "lineup": _lineup_layer(match["matchNumStr"], lineups),
            "context": {"status": "pending", "entries": []},
            "ourModels": _our_models_layer(sale_date, match["matchNumStr"]),
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
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(card, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.rename(p)          # 原子写：rename 落卡，杜绝半截 JSON
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
            card = build_card(sale_date, m, insight_path=insight_path, lineups=lineups)
            save_card(card, sale_date)
            print(f'[card] {card["code"]} {card["match"]} | 建议检索: "{card["match"]} injury news preview"')
    return 0


# ── 语境层回写 + 防赛后泄漏三道过滤（spec review R1）───────────────────────
import re

POST_MATCH_PATTERNS = [
    r"\bFT\b", r"full[\s-]?time", r"final\s+score", r"match\s+report",
    r"终场", r"完场", r"全场结束", r"\d+\s*[-:]\s*\d+\s*(完|结束)",
]
QUERY_WORDS = ("injury news", "team news", "preview", "predicted lineup")   # 检索建议词（gen 已用）


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
        if str(e["publishedAt"]) > created:
            dropped.append({"reason": "发布晚于卡createdAt", "title": e.get("title")}); continue
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


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="LLM 预测数据卡生成器")
    ap.add_argument("cmd", choices=["gen"])
    ap.add_argument("sale_date")
    args = ap.parse_args()
    rc = gen(args.sale_date)
    try:                                   # 规则简报（rules-registry）：best-effort 不熔断出卡
        import rule_brief
        out = rule_brief.compile_brief(args.sale_date)
        print(f"[llm_card] 规则简报 → {out}（填格前必读）")
    except Exception as e:                 # noqa: BLE001
        print(f"[llm_card] 规则简报跳过：{e}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
