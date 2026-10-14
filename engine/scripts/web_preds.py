#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""web_preds.py — 网络预测源记录与打分层（设计 docs/2026-10-14-web-source-score-design.html）。

纯观察层：record 冻结会话检索到的各源预测 / settle 挂赛果判三判据 / why 三向回溯 /
leaderboard 出信誉榜。判据 §2 五条已随设计冻结（forward only·铁律 14）。
不参与选腿排序（组件准入制：入链须另立审计案）。开发者 sszhang"""
import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TRENDS = ROOT / "data" / "05-trends"
RESULTS = ROOT / "data" / "02-results"
SUMMARIES = ROOT / "data" / "04-summaries"
SOURCES_MAP = ROOT / "engine" / "cache" / "web_sources.json"
MATCHES_CACHE = ROOT / "engine" / "cache" / "sporttery_matches.json"

PICK_RE = re.compile(r"^\d{1,2}:\d{1,2}$")
DIRECTIONS = ("home", "draw", "away")       # 方向型 pick（多数源只给方向·不逼会话转译出假精度）
PICK_TYPES = ("source_exact", "session_derived")
FORMAL_N = 30          # 判据①②门槛（prereg 冻结值）
DEV_N = 15             # 判据③偏离场门槛（prereg 冻结值）


def norm_slug(name: str) -> str:
    """源名归一：去空格/点/连字符→首字母大写连字符式（防 Sports Mole/sportsmole 分裂）。"""
    s = re.sub(r"[\s._]+", "-", str(name).strip())
    s = re.sub(r"-+", "-", s).strip("-")
    return "-".join(w[:1].upper() + w[1:] for w in s.split("-") if w)


def load_sources() -> dict:
    return json.loads(SOURCES_MAP.read_text(encoding="utf-8")) if SOURCES_MAP.exists() else {}


def register_source(raw: str) -> str:
    """首见自动注册，返回归一 slug。"""
    slug = norm_slug(raw)
    doc = load_sources()
    aliases = doc.setdefault("aliases", {})
    if str(raw) not in aliases:
        aliases[str(raw)] = slug
        doc.setdefault("firstSeen", {}).setdefault(slug, datetime.now().strftime("%Y-%m-%d"))
        SOURCES_MAP.parent.mkdir(parents=True, exist_ok=True)
        SOURCES_MAP.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return aliases[str(raw)]


def outcome_of(pick: str) -> str:
    """比分型→派生方向；方向型→直通（方向型源无比分可判·scoreHit 记 None 不记错）。"""
    if pick in DIRECTIONS:
        return pick
    h, a = (int(x) for x in pick.split(":"))
    return "home" if h > a else "away" if a > h else "draw"


def is_score_pick(pick: str) -> bool:
    return bool(PICK_RE.match(str(pick)))


def _norm_score(s: str) -> str:
    """赛果 "1-2" / 预测 "1:2" 两式归一为冒号式。"""
    return str(s).strip().replace("-", ":")


def sale_matches(sale_date: str) -> dict:
    """当日在售清单 {code: kickoff}——校验④用。缺文件返回空表（校验降级为仅格式检查）。"""
    if not MATCHES_CACHE.exists():
        return {}
    doc = json.loads(MATCHES_CACHE.read_text(encoding="utf-8"))
    return {m.get("code"): (m.get("matchTime") or m.get("kickoff") or "")
            for m in doc.get("matches") or [] if m.get("code")}


def preds_path(sale_date: str) -> Path:
    return TRENDS / f"{sale_date}-webpreds.json"


def load_preds(sale_date: str) -> dict:
    p = preds_path(sale_date)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"saleDate": sale_date, "batches": [], "entries": [], "consensus": []}


def batch_hash(entries: list) -> str:
    blob = json.dumps(entries, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def validate_entry(e: dict, sale_date: str, codes: dict, seen: set) -> list:
    """schema 七校验（设计 §2）。返回 issues 列表，空=过。"""
    issues = []
    code, src, pick = e.get("code"), e.get("source"), str(e.get("pick") or "")
    if not (PICK_RE.match(pick) or pick in DIRECTIONS):            # ①（比分型或方向型）
        issues.append(f"{code}/{src}: pick 非法 {pick!r}（须 比分 1:1 或方向 home/draw/away）")
    if e.get("pickType") not in PICK_TYPES:                        # ①b 转译留痕（008 失真教训）
        issues.append(f"{code}/{src}: pickType 须为 {PICK_TYPES} 之一——会话转译须自认")
    url = str(e.get("url") or "")
    if not url.startswith("http"):                                 # ②
        issues.append(f"{code}/{src}: url 缺失或非 http（铁律1）")
    kickoff = codes.get(code)
    pub = str(e.get("publishedAt") or "")
    if not pub:
        issues.append(f"{code}/{src}: 缺 publishedAt")
    elif kickoff and _cmp_time(pub, kickoff, sale_date) > 0:       # ③
        issues.append(f"{code}/{src}: publishedAt({pub}) 晚于开赛({kickoff})——赛后源拒录（防泄漏）")
    if codes and code not in codes:                                # ④
        issues.append(f"{code}/{src}: 不在当日在售清单")
    if not src:                                                    # ⑤（归一在 record 内做）
        issues.append(f"{code}: 缺 source")
    key = (code, norm_slug(src or ""))
    if key in seen:                                                # ⑥
        issues.append(f"{code}/{src}: 同批同源同场重复")
    return issues


def _cmp_time(pub: str, kickoff: str, sale_date: str) -> int:
    """pub 与开赛时刻比较（kickoff 可能只有 HH:MM:SS，按销售日补日期；跨零点场次不苛求精确）。"""
    k = kickoff if len(kickoff) > 8 else f"{sale_date} {kickoff}"
    k = k.replace("T", " ")[:16]
    p = str(pub).replace("T", " ")[:16]
    return (p > k) - (p < k)


def detect_consensus(entries: list) -> list:
    """≥2 源同比分=比分共识；≥3 源同方向=方向共识（设计 §1）。"""
    out = []
    by_code = {}
    for e in entries:
        by_code.setdefault(e["code"], []).append(e)
    for code, rows in sorted(by_code.items()):
        by_pick, by_out = {}, {}
        for r in rows:
            if is_score_pick(r["pick"]):            # 比分共识只由比分型源构成（方向型无比分）
                by_pick.setdefault(r["pick"], []).append(r["source"])
            by_out.setdefault(r["outcome"], []).append(r["source"])
        for pick, srcs in by_pick.items():
            if len(srcs) >= 2:
                out.append({"code": code, "kind": "score", "value": pick,
                            "members": sorted(srcs), "n_members": len(srcs)})
        for oc, srcs in by_out.items():
            if len(srcs) >= 3:
                out.append({"code": code, "kind": "direction", "value": oc,
                            "members": sorted(srcs), "n_members": len(srcs)})
    return out


def record(sale_date: str, entries_file: Path) -> int:
    """批次冻结：全批过校验方落盘，追加不覆盖，已录条目不可改。"""
    raw = json.loads(Path(entries_file).read_text(encoding="utf-8"))
    rows = raw.get("entries") if isinstance(raw, dict) else raw
    if not rows:
        print("[record] 空批——检索无果不编造（铁律4）")
        return 0
    doc = load_preds(sale_date)
    codes = sale_matches(sale_date)
    existing = {(e["code"], e["source"]) for e in doc["entries"]}
    seen, issues = set(existing), []
    for e in rows:
        issues += validate_entry(e, sale_date, codes, seen)
        seen.add((e.get("code"), norm_slug(e.get("source") or "")))
    if issues:
        print("[record] 整批拒绝（校验失败）:")
        for i in issues:
            print(f"  - {i}")
        return 1
    bno = len(doc["batches"]) + 1
    fresh = []
    for e in rows:
        fresh.append({"code": e["code"], "matchDay": e.get("matchDay") or sale_date,
                      "source": register_source(e["source"]), "pick": e["pick"],
                      "outcome": outcome_of(e["pick"]), "publishedAt": e["publishedAt"],
                      "url": e["url"], "batch": bno,
                      "note": e.get("note")})
    doc["entries"] += fresh
    doc["batches"].append({"batch": bno, "n": len(fresh), "batchHash": batch_hash(fresh),
                           "recordedAt": datetime.now().isoformat(timespec="seconds")})
    doc["consensus"] = detect_consensus(doc["entries"])
    preds_path(sale_date).parent.mkdir(parents=True, exist_ok=True)
    preds_path(sale_date).write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[record] 批{bno} 冻结 {len(fresh)} 条 → {preds_path(sale_date).name}"
          f" | 累计 {len(doc['entries'])} 条 | 共识 {len(doc['consensus'])} 例")
    for c in doc["consensus"]:
        print(f"  共识[{c['kind']}] {c['code']} = {c['value']} ({'/'.join(c['members'])})")
    return 0


def _results_index(sale_date: str) -> dict:
    """赛果索引 {code: "1:2"}——销售日及次日两文件（跨零点场次·与 settle_tickets 的 day±1 同口径）。"""
    idx = {}
    for f in sorted(RESULTS.glob("*.json")):
        stem = f.stem.replace("-r1", "")
        if stem < sale_date:
            continue
        if stem > _plus_days(sale_date, 2):
            continue
        doc = json.loads(f.read_text(encoding="utf-8"))
        for m in doc.get("matches") or []:
            if m.get("code") and m.get("result"):
                idx.setdefault(m["code"], _norm_score(m["result"]))
    return idx


def _plus_days(d: str, n: int) -> str:
    from datetime import timedelta
    return (datetime.strptime(d, "%Y-%m-%d") + timedelta(days=n)).strftime("%Y-%m-%d")


def _market_fav(sale_date: str, code: str) -> str | None:
    """判据④：市场热门=体彩 HAD 去水后最高概率方向（r1 预测卷 odds 即体彩赔率）。"""
    cands = [RESULTS / f"{sale_date}-r1.json", RESULTS / f"{sale_date}.json",
             RESULTS / f"{_plus_days(sale_date, 1)}-r1.json", RESULTS / f"{_plus_days(sale_date, 1)}.json"]
    for f in cands:                     # 赔率常在销售日预测卷；跨零点场次的赛果卷亦可携带（day+1 同口径）
        if not f.exists():
            continue
        doc = json.loads(f.read_text(encoding="utf-8"))
        for m in doc.get("matches") or []:
            if m.get("code") != code:
                continue
            o = m.get("odds") or {}
            vals = {k: o.get(k) for k in ("h", "d", "a") if isinstance(o.get(k), (int, float)) and o[k] > 1}
            if len(vals) < 3:
                return None
            inv = {k: 1.0 / v for k, v in vals.items()}
            best = max(inv, key=inv.get)
            return {"h": "home", "d": "draw", "a": "away"}[best]
    return None


def settle(sale_date: str) -> int:
    """三判据结算（方向/比分/市场对标），写结算区（与冻结区物理分离）。幂等：已结算条目跳过。"""
    doc = load_preds(sale_date)
    if not doc["entries"]:
        print(f"[settle] {sale_date} 无记录")
        return 0
    res = _results_index(sale_date)
    done = pend = 0
    for e in doc["entries"]:
        if e.get("settled"):
            done += 1
            continue
        actual = res.get(e["code"])
        if not actual:
            pend += 1
            continue
        fav = _market_fav(sale_date, e["code"])
        e["settled"] = {"actual": actual,
                        "directionHit": e["outcome"] == outcome_of(actual),
                        # 方向型源无比分可判→None（不记 false·否则比分率被结构性拖低）
                        "scoreHit": (e["pick"] == actual) if is_score_pick(e["pick"]) else None,
                        "vsMarket": {"favOutcome": fav,
                                     "isDeviation": (fav is not None and e["outcome"] != fav)},
                        "settledAt": datetime.now().isoformat(timespec="seconds")}
        done += 1
    for c in doc["consensus"]:
        if c.get("settled"):
            continue
        actual = res.get(c["code"])
        if not actual:
            continue
        c["settled"] = {"actual": actual,
                        "hit": (c["value"] == actual) if c["kind"] == "score"
                               else (c["value"] == outcome_of(actual))}
    preds_path(sale_date).write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[settle] {sale_date}: 已结算 {done} 条 | 待赛果 {pend} 条 | 共识例 {len(doc['consensus'])}")
    return 0


def _all_docs() -> list:
    return [json.loads(f.read_text(encoding="utf-8")) for f in sorted(TRENDS.glob("*-webpreds.json"))]


def leaderboard(as_json: bool = False) -> int:
    """信誉榜（视图·现算现写不存二级真相）。n<30 标积累中（prereg 冻结门槛）。"""
    stats, cons = {}, {"score": [0, 0], "direction": [0, 0]}
    for doc in _all_docs():
        for e in doc["entries"]:
            s = e.get("settled")
            if not s:
                continue
            st = stats.setdefault(e["source"], {"n": 0, "dir": 0, "score": 0, "n_score": 0,
                                                "n_dev": 0, "dev_dir": 0, "mkt_dir_same_set": 0})
            st["n"] += 1
            st["dir"] += bool(s["directionHit"])
            if s["scoreHit"] is not None:          # 方向型源不入比分率分母
                st["n_score"] += 1
                st["score"] += bool(s["scoreHit"])
            if (s.get("vsMarket") or {}).get("isDeviation"):
                st["n_dev"] += 1
                st["dev_dir"] += bool(s["directionHit"])
                fav = s["vsMarket"].get("favOutcome")
                st["mkt_dir_same_set"] += bool(fav and fav == outcome_of(s["actual"]))
        for c in doc.get("consensus") or []:
            if c.get("settled"):
                cons[c["kind"]][0] += 1
                cons[c["kind"]][1] += bool(c["settled"]["hit"])
    rows = []
    for src, st in sorted(stats.items(), key=lambda kv: -kv[1]["n"]):
        formal = st["n"] >= FORMAL_N
        r = {"source": src, "n": st["n"], "formal": formal,
             "dirRate": round(st["dir"] / st["n"], 4) if st["n"] else None,
             "n_score": st["n_score"],
             "scoreRate": round(st["score"] / st["n_score"], 4) if st["n_score"] else None,
             "n_dev": st["n_dev"],
             "devHitRate": round(st["dev_dir"] / st["n_dev"], 4) if st["n_dev"] else None,
             "marketHitOnDevSet": (round(st["mkt_dir_same_set"] / st["n_dev"], 4)
                                   if st["n_dev"] else None),
             "vsMarket": (round(st["dev_dir"] / st["n_dev"] - st["mkt_dir_same_set"] / st["n_dev"], 4)
                          if st["n_dev"] >= DEV_N else None),
             "verdict": "可判" if formal else f"积累中 {st['n']}/{FORMAL_N}"}
        rows.append(r)
    out = {"generatedAt": datetime.now().isoformat(timespec="seconds"),
           "prereg": {"formalN": FORMAL_N, "devN": DEV_N,
                      "note": "判据冻结于 2026-10-14 设计档·forward only（铁律14）"},
           "sources": {r["source"]: r for r in rows},
           "consensus": {k: {"n": v[0], "hit": v[1],
                             "rate": round(v[1] / v[0], 4) if v[0] else None,
                             "verdict": "可判" if v[0] >= FORMAL_N else f"积累中 {v[0]}/{FORMAL_N}"}
                         for k, v in cons.items()}}
    SUMMARIES.mkdir(parents=True, exist_ok=True)
    (SUMMARIES / "web-source-ledger.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    if as_json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return out
    print(f"== 网络源信誉榜（{len(rows)} 源·判据冻结 n≥{FORMAL_N}）==")
    for r in rows:
        vm = "—" if r["vsMarket"] is None else f'{r["vsMarket"]:+.3f}'
        print(f'  {r["source"]:<20} n={r["n"]:<4} 方向={_pct(r["dirRate"])} 比分={_pct(r["scoreRate"])}'
              f' 对标={vm}(n_dev={r["n_dev"]}) [{r["verdict"]}]')
    for k, v in out["consensus"].items():
        print(f'  共识[{k}] n={v["n"]} 命中={v["hit"]} 率={_pct(v["rate"])} [{v["verdict"]}]')
    return out


def _pct(x) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


def why(target: str) -> int:
    """三向回溯：日期=当日全卷 | 源名=该源历史 | 场次编号=该场全貌。"""
    docs = _all_docs()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", target):
        doc = load_preds(target)
        print(f"== {target} 全卷：{len(doc['entries'])} 条 / {len(doc['batches'])} 批 ==")
        for b in doc["batches"]:
            print(f"  批{b['batch']} n={b['n']} hash={b['batchHash']} at={b['recordedAt']}")
        for e in doc["entries"]:
            print(f"  {e['code']:<9} {e['source']:<18} {e['pick']:<6} {_fmt_settled(e)}  {e['url'][:60]}")
        for c in doc.get("consensus") or []:
            st = c.get("settled")
            print(f"  共识[{c['kind']}] {c['code']} = {c['value']} ({'/'.join(c['members'])})"
                  f" {'✓' if st and st['hit'] else '✗' if st else '待'}")
        return 0
    if re.match(r"^周[一二三四五六日]\d{3}$", target):
        print(f"== 场次 {target} 全貌 ==")
        for doc in docs:
            for e in doc["entries"]:
                if e["code"] == target:
                    print(f"  [{doc['saleDate']}] {e['source']:<18} {e['pick']:<6} {_fmt_settled(e)}  {e['url'][:60]}")
            for c in doc.get("consensus") or []:
                if c["code"] == target:
                    st = c.get("settled")
                    print(f"  共识[{c['kind']}] {c['value']} ({'/'.join(c['members'])})"
                          f" {'✓' if st and st['hit'] else '✗' if st else '待'}")
        return 0
    slug = norm_slug(target)
    n = hit = sc = 0
    print(f"== 源 {slug} 历史 ==")
    for doc in docs:
        for e in doc["entries"]:
            if e["source"] != slug:
                continue
            print(f"  [{doc['saleDate']}] {e['code']:<9} {e['pick']:<6} {_fmt_settled(e)}  {e['url'][:60]}")
            s = e.get("settled")
            if s:
                n += 1
                hit += bool(s["directionHit"])
                sc += bool(s["scoreHit"])
    if n:
        print(f"  小计 n={n} 方向={hit}({hit / n * 100:.1f}%) 比分={sc}({sc / n * 100:.1f}%)"
              f" {'[可判]' if n >= FORMAL_N else f'[积累中 {n}/{FORMAL_N}]'}")
    else:
        print("  无已结算记录")
    return 0


def _fmt_settled(e: dict) -> str:
    s = e.get("settled")
    if not s:
        return "待结算"
    d = "方向✓" if s["directionHit"] else "方向✗"
    c = "比分✓" if s["scoreHit"] else "比分✗"
    dev = "偏离" if (s.get("vsMarket") or {}).get("isDeviation") else "随热"
    return f"实{s['actual']} {d} {c} {dev}"


def main() -> int:
    ap = argparse.ArgumentParser(description="网络预测源记录与打分层（纯观察层）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record", help="冻结一批源预测")
    r.add_argument("sale_date")
    r.add_argument("entries_file")
    s = sub.add_parser("settle", help="挂赛果判三判据")
    s.add_argument("sale_date")
    w = sub.add_parser("why", help="三向回溯：日期|源名|场次编号")
    w.add_argument("target")
    lb = sub.add_parser("leaderboard", help="出信誉榜")
    lb.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.cmd == "record":
        return record(a.sale_date, Path(a.entries_file))
    if a.cmd == "settle":
        return settle(a.sale_date)
    if a.cmd == "why":
        return why(a.target)
    leaderboard(a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
