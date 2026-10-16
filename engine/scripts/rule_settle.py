# engine/scripts/rule_settle.py
"""规则结算钩子（rules-registry Task 3·设计 §2/§3）。

settle <date>：
①拦截账——读 briefs/{date}.md 回执，被拦腿对赛果判死活 → record{rounds,blocked,saved,harmed}
②三模式检测——扫当轮已结算票给匹配候选自动加证据（只加权不立新规）：
   HAFU 过关腿死 / 贴平腿死(|q−1/odds|<2pt) / 深水腿死(score_odds 池返还率<85%)
③生命周期标旗——harmed>=2 降级标红 / 候选30天无证据标stale / 20轮零触发标灰（写flags不动stage）

赛果源：data/02-results/（含 -rN 拆轮·主文件优先·与 data.load_rounds 同口径）。
开发者 sszhang
"""
import argparse
import json
import re
import sys
from datetime import date as date_cls, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import rules_registry as rr  # noqa: E402
import rule_brief as rb  # noqa: E402

RESULTS_DIR = ROOT / "data" / "02-results"
TICKETS = ROOT / "data" / "06-tickets" / "tickets.json"
SHADOW = ROOT / "engine" / "shadow" / "paper_tickets.json"
SCORE_ODDS = ROOT / "engine" / "cache" / "score_odds"
STALE_DAYS = 30
DEMOTION_HARMED = 2
GREY_ROUNDS = 20


def _load_results(day: str) -> dict:
    """{code: {"score": (h,a), "half": (h,a)|None}}（day±1·-rN 与主文件合并·主文件后写覆盖）。"""
    out = {}
    base = date_cls.fromisoformat(day)
    for off in (-1, 0, 1):
        stem = (base.fromordinal(base.toordinal() + off)).isoformat()
        folder = RESULTS_DIR / stem
        files = sorted(RESULTS_DIR.glob(stem + "-r*.json"))
        main = RESULTS_DIR / f"{stem}.json"
        for f in files + ([main] if main.exists() else []):
            try:
                doc = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for m in doc.get("matches") or []:
                r = m.get("result")
                if not r or not re.fullmatch(r"\d+-\d+", str(r)):
                    continue
                h, a = (int(x) for x in str(r).split("-"))
                half = None
                hm = re.match(r"(\d+)[:\-](\d+)", str(m.get("half") or ""))
                if hm:
                    half = (int(hm.group(1)), int(hm.group(2)))
                out[m["code"]] = {"score": (h, a), "half": half}
    return out


def _leg_outcome(code, market, pick, res) -> str | None:
    """被拦腿死活：hit/miss/None(不可判)。HAD方向·TTG总球·CRS精确·HAFU须半场。"""
    r = res.get(code)
    if not r:
        return None
    h, a = r["score"]
    mk = market.upper()
    if mk == "HAD":
        act = "h" if h > a else ("d" if h == a else "a")
        return "hit" if str(pick).lower().startswith(act) else "miss"
    if mk == "TTG":
        p = re.fullmatch(r"s(\d)", str(pick))
        if not p:
            return None
        n = min(h + a, 7)
        return "hit" if int(p.group(1)) == n else "miss"
    if mk == "CRS":
        want = str(pick).replace(":", "-")
        return "hit" if f"{h}-{a}" == want else "miss"
    if mk == "HAFU" and r["half"]:
        hh, ha = r["half"]
        f = lambda x, y: "h" if x > y else ("d" if x == y else "a")  # noqa: E731
        want = f"{f(hh, ha)}{f(h, a)}"
        return "hit" if str(pick).lower() == want.lower() else "miss"
    return None


def settle(date: str) -> dict:
    doc = rr.load()
    receipts = rb.read_receipts(date)
    res = _load_results(date)
    stats = {"receipts": len(receipts), "blocked": 0, "saved": 0, "harmed": 0,
             "unknown": 0, "patterns": 0}
    for row in receipts:
        if not row["blocked_code"]:
            continue
        o = _leg_outcome(row["blocked_code"], row["blocked_market"],
                         row["blocked_pick"], res)
        kw = {"rounds": 0, "blocked": 1}
        if o == "miss":
            kw["saved"] = 1
            stats["saved"] += 1
        elif o == "hit":
            kw["harmed"] = 1
            stats["harmed"] += 1
        else:
            stats["unknown"] += 1
        rr.record_tick(doc, row["rule"], **kw)
        stats["blocked"] += 1
    # ① rounds：本轮简报含的全部 shadow 规则 +1（在场即计）
    shadow_ids = {r["id"] for r in doc.get("active_shadow") or []}
    if rb.brief_path(date).exists():
        for rid in shadow_ids:
            rr.record_tick(doc, rid, rounds=1)
    stats["patterns"] = _pattern_scan(doc, date, res)
    _lifecycle_flags(doc)
    rr.save(doc)
    return stats


def _odds_archive(sale_day: str) -> dict:
    """score_odds 档案 → {code: match}（找池返还率用·缺档返回空）。"""
    f = SCORE_ODDS / f"{sale_day}.json"
    if not f.exists():
        return {}
    try:
        doc = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for day in doc.get("matchDays") or []:
        for m in day.get("matches") or []:
            if m.get("matchNumStr"):
                out[m["matchNumStr"]] = m
    return out


def _pool_payout_of(match: dict, market: str) -> float | None:
    pool = match.get(market.lower()) or {}
    odds = [float(v) for v in pool.values()
            if isinstance(v, (int, float, str)) and not str(v).startswith("goal")
            and _is_num(v) and float(v) > 1]
    if not odds:
        return None
    return len(odds) / sum(1 / o for o in odds)


def _is_num(v) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def _iter_settled_tickets(date: str):
    for path in (TICKETS, SHADOW):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        ts = data.get("tickets") if isinstance(data, dict) else data
        for t in ts or []:
            if t.get("result") == "settled" and date in (t.get("matchDays") or []):
                yield t


def _pattern_scan(doc: dict, date: str, res: dict) -> int:
    """三模式检测：给匹配候选 evidence+1（按 trigger 匹配到候选规则）。
    ①HAFU过关腿死 ②贴平腿死(|q−1/odds|<2pt) ③深水腿死(池返还率<85%·须档案)。"""
    cands = {r["id"]: r for r in doc.get("candidates") or []}
    archive = _odds_archive(date)
    hits = 0
    for t in _iter_settled_tickets(date):
        nlegs = len(t.get("legs") or [])
        for leg in t["legs"]:
            if leg.get("result") != "miss":
                continue
            mk = str(leg.get("market") or leg.get("play") or "").upper()
            rid = None
            if mk == "HAFU" and nlegs >= 3:
                rid = _find(cands, {"play": "HAFU"})
            elif leg.get("q") is not None and leg.get("odds"):
                o = leg["odds"]
                ov = list(o.values())[0] if isinstance(o, dict) else o
                if _is_num(ov) and abs(float(leg["q"]) - 1 / float(ov)) < 0.02:
                    rid = _find(cands, {"deviationPtMax": 2})
            if rid is None and archive:
                m = archive.get(leg.get("code"))
                if m and _pool_payout_of(m, mk) is not None \
                        and _pool_payout_of(m, mk) < 0.85:
                    rid = _find(cands, {"poolPayoutMin": 0.85})
            if rid:
                cands[rid].setdefault("evidence", []).append(
                    {"round": date, "legs": [leg.get("code")], "loss": t.get("stake"),
                     "pattern": True})
                hits += 1
    return hits


def _find(cands: dict, trigger_frag: dict) -> str | None:
    for rid, r in cands.items():
        tr = r.get("trigger") or {}
        if all(tr.get(k) == v for k, v in trigger_frag.items()):
            return rid
    return None


def _lifecycle_flags(doc: dict) -> int:
    """harmed>=2 标红待降 / 候选30天无新证据标stale / 20轮零触发标灰。写flags不动stage。"""
    n = 0
    today = datetime.now().date()
    for _, r in rr.iter_rules(doc):
        flags = r.setdefault("flags", [])
        if r["record"]["harmed"] >= DEMOTION_HARMED and "red_review" not in flags:
            flags.append("red_review")
            n += 1
        if r["stage"] == "candidate":
            evs = r.get("evidence") or []
            last = evs[-1].get("round") if evs else r.get("addedAt")
            try:
                aged = (today - date_cls.fromisoformat(str(last)[:10])).days
            except ValueError:
                aged = 0
            if aged > STALE_DAYS and "stale" not in flags:
                flags.append("stale")
                n += 1
        if r["record"]["rounds"] >= GREY_ROUNDS and r["record"]["blocked"] == 0 \
                and "grey_idle" not in flags:
            flags.append("grey_idle")
            n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description="规则结算钩子")
    ap.add_argument("cmd", choices=["settle"])
    ap.add_argument("date")
    a = ap.parse_args()
    st = settle(a.date)
    print(f"[rule-settle] {a.date}: 回执{st['receipts']} 拦截{st['blocked']} "
          f"救{st['saved']} 误{st['harmed']} 不可判{st['unknown']} "
          f"模式命中{st['patterns']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
