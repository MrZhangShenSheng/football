"""③ 自有 CLV 接线（三梯队一期 · docs/2026-09-27-probability-modernization-design.html）。

问题（E3）：体彩 vs Pinnacle 的 CLV 结构性恒负（166 条 95.8% 负=抽水差 13pp），
衡量不了选单能力。正确口径=同庄家时序：出票冻结价 vs 体彩末次快照价。

clv_self = 腿.odds / 该场末次快照价 − 1
  >0 = 出票时点价优于停售前末次价（ beat the close · 同庄家口径）
  <0 = 出票价差于末次价（买早了/买贵了）

数据边界（诚实标注）：05-trends 快照自 2026-08-30 起积累——此前票 clv_self=null；
末次快照≠真实收盘（停售后无价），口径近似。

用法：python self_clv.py            # 已结算票全量回填 + 汇总
开发者 sszhang
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TRENDS = ROOT / "data" / "05-trends"
TICKETS = ROOT / "data" / "06-tickets" / "tickets.json"

HAFU_KEY = {"胜胜": "hh", "胜平": "hd", "胜负": "ha", "平胜": "dh", "平平": "dd",
            "平负": "da", "负胜": "ah", "负平": "ad", "负负": "aa"}


def pool_key(market: str, pick: str) -> tuple[str, str] | None:
    """market+pick → (池名, 池键)。pick 规范化自 tickets 既有取值。"""
    p = pick.replace(" ", "")
    if market == "HAD":
        for zh, k in (("主胜", "h"), ("平", "d"), ("客胜", "a")):
            if zh in p:
                return "had", k
    elif market == "HHAD":
        for zh, k in (("主胜", "h"), ("平", "d"), ("客胜", "a")):
            if zh in p:
                return "hhad", k
    elif market == "CRS" and ":" in p:
        h, a = p.split(":", 1)[0], p.split(":", 1)[1][:2].strip()  # "1:2" / "1:2 xxx"
        try:
            return "crs", f"s{int(h):02d}s{int(a.strip()[:1] if len(a) > 1 else a):02d}"
        except ValueError:
            return None
    elif market == "TTG":
        seg = [t for t in ("s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7") if t in p]
        if seg:
            return "ttg", seg[0]
    elif market == "HAFU":
        for zh, k in HAFU_KEY.items():
            if zh in p:
                return "hafu", k
    return None


def build_last_prices() -> dict:
    """扫全部 odds 快照（时间序）→ {code: {池名: {键: (价, 末次at)}}}。"""
    last: dict = {}
    for f in sorted(TRENDS.glob("*-odds.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for snap in d.get("snapshots", []):
            at = snap.get("at", "")
            for m in snap.get("matches", []):
                code = m.get("code")
                if not code:
                    continue
                for pool in ("had", "hhad", "crs", "ttg", "hafu"):
                    pv = m.get(pool)
                    if isinstance(pv, dict):
                        for k, v in pv.items():
                            try:
                                last.setdefault(code, {}).setdefault(pool, {})[k] = (float(v), at)
                            except (TypeError, ValueError):
                                continue
    return last


def main() -> None:
    last = build_last_prices()
    data = json.loads(TICKETS.read_text(encoding="utf-8"))
    tickets = data if isinstance(data, list) else data.get("tickets", [])
    n_fill = n_null = 0
    values = []
    for t in tickets:
        settled = t.get("settled") or t.get("settle") or {}
        if settled.get("status") not in ("settled",):
            continue
        for leg in t.get("legs", []):
            if leg.get("clv_self") is not None:
                continue
            pk = pool_key(str(leg.get("market")), str(leg.get("pick")))
            entry = (last.get(str(leg.get("code"))) or {}).get(pk[0], {}).get(pk[1]) if pk else None
            if not entry or not leg.get("odds"):
                leg["clv_self"] = None
                leg["clv_self_note"] = "时序未覆盖（08-30 前出票或快照无该池）"
                n_null += 1
                continue
            last_price, at = entry
            leg["clv_self"] = round(float(leg["odds"]) / last_price - 1, 4)
            leg["clv_self_note"] = f"末次快照 {last_price} @ {at[:16]}"
            values.append(leg["clv_self"])
            n_fill += 1
    TICKETS.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    print(f"[self_clv] 已结算腿: clv_self 填充 {n_fill} / 无覆盖 {n_null}")
    if values:
        avg = sum(values) / len(values)
        pos = sum(1 for v in values if v > 0)
        print(f"[self_clv] 场均 {avg:+.4f} | 正CLV {pos}/{len(values)}"
              f"（同庄家口径：正=出票时点价优于停售前末次价）")


if __name__ == "__main__":
    main()
