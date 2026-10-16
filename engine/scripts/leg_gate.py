# engine/scripts/leg_gate.py
"""选腿硬闸（rules-registry Task 4·设计 §1/§3）。

从规则册读 stage=gate 规则，按 trigger 类型分派检查器：
- {play, parlayMin}：某玩法腿入 N 串以上过关
- {deviationPtMax}：|q − 1/odds| < 阈值（贴平腿·腿须带 q+odds）
- {poolPayoutMin}：玩法池返还率 < 阈值（须传池赔率 dict）
trigger 类型未知或数据缺 → 记 skip 不误拦（宁漏勿错——误拦活腿=harmed）。
CLI: leg_gate.py check <ticket.json路径>  退出码 0=过闸 1=有违规。
开发者 sszhang
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import rules_registry as rr  # noqa: E402


def _market_of(leg) -> str:
    return str(leg.get("market") or leg.get("play") or "").upper()


def _odds_scalar(leg):
    o = leg.get("odds")
    if isinstance(o, dict):
        vals = [v for v in o.values() if isinstance(v, (int, float))]
        return vals[0] if vals else None
    return o if isinstance(o, (int, float)) else None


def pool_payout(pool_odds: dict) -> float | None:
    """玩法池返还率 = 1 / Σ(1/赔率)（庄家抽水倒数）。非数值项剔除。"""
    odds = [float(v) for v in pool_odds.values()
            if isinstance(v, (int, float, str))
            and not str(v).lstrip("-").startswith("goal")
            and _is_num(v) and float(v) > 1]
    if not odds:
        return None
    return 1 / sum(1 / o for o in odds)


def _is_num(v) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def check_ticket(ticket: dict, pool_odds_by_play: dict | None = None,
                 doc: dict | None = None) -> list[dict]:
    """票 → 违规清单 [{rule, reason, leg}]。空=过闸。"""
    doc = doc or rr.load()
    legs = ticket.get("legs") or []
    n = len(legs)
    pools = pool_odds_by_play or {}
    out = []
    for r in doc.get("active_shadow") or []:
        if r.get("stage") != "gate":
            continue
        tr = r.get("trigger") or {}
        if tr.get("requiresHypothesis") or tr.get("noAnchorPureTemplate"):
            continue                     # enforcedBy 上游（hypothesis/boldplay），此处不重复拦
        for leg in legs:
            mk = _market_of(leg)
            if "play" in tr and mk == str(tr["play"]).upper() \
                    and n >= tr.get("parlayMin", 2):
                out.append({"rule": r["id"], "leg": leg.get("code"),
                            "reason": f"{tr['play']} 腿入 {n} 串（≥{tr.get('parlayMin')}）"})
            if "deviationPtMax" in tr and leg.get("q") is not None:
                o = _odds_scalar(leg)
                if o and abs(float(leg["q"]) - 1 / float(o)) * 100 < float(tr["deviationPtMax"]):
                    out.append({"rule": r["id"], "leg": leg.get("code"),
                                "reason": f"贴平腿(|q-1/o|<{tr['deviationPtMax']}pt)"})
            if "poolPayoutMin" in tr:
                po = pools.get(mk.lower()) or pools.get(mk)
                pp = pool_payout(po) if isinstance(po, dict) else None
                if pp is not None and pp < float(tr["poolPayoutMin"]):
                    out.append({"rule": r["id"], "leg": leg.get("code"),
                                "reason": f"{mk} 池返还率 {pp:.1%} < {tr['poolPayoutMin']:.0%}"})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="选腿硬闸")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("ticket_json")
    a = ap.parse_args()
    ticket = json.loads(Path(a.ticket_json).read_text(encoding="utf-8"))
    vs = check_ticket(ticket)
    if vs:
        for v in vs:
            print(f"[gate] ✗ {v['rule']} {v['leg']}: {v['reason']}", file=sys.stderr)
        return 1
    print("[gate] ✓ 过闸")
    return 0


if __name__ == "__main__":
    sys.exit(main())
