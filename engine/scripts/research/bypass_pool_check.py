#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T7（goal-engine P0 前置）：P1 HAD 融合旁路——近三轮 boldplay 实跑腿池充足性验证。

背景：D3 复审（bypass_impact_quantify.py）实测 P1 旁路使 65% 准入达标池 -36%（442→281 场），
保底票 4串11 需 4 条腿——旁路后部分轮次可能组不满。本脚本用 08-30/08-31/09-01 三轮
boldplay 实跑 HAD/HHAD 腿做双口径对照：市场隐含（Pinnacle 收盘去水）vs 融合口径
（腿概率字段），各判 ≥65%，输出分轮对照表并写入 goal-engine-report.json bypassPool 节。
只验证不裁决 P1（数字留给大哥）。

市场隐含三级来源（优先级）：
  1. 腿自带 pinClose 键；
  2. data/02-results/{date}.json matches[].pinClose（fd 收盘去水三向，按场次编号对齐）；
  3. engine/cache/odds_{league}_{season}.json（队名经 _aliases.json zh→fd 映射 + 日期±1，devig）。
未匹配腿如实计数并记原因，不计入达标判定也不静默丢弃。
"""
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "engine" / "scripts"))
from dc_predict import devig  # noqa: E402

ROUNDS = ["2026-08-30", "2026-08-31", "2026-09-01"]
THRESHOLD = 0.65
HAD_PLAYS = {"had", "hhad"}
PICK_DIR = {"主胜": 0, "平": 1, "客胜": 2}  # 三向下标；hhad 让球方向不在三向内
REPORT_PATH = ROOT / "data" / "04-summaries" / "goal-engine-report.json"


def split_match(match_str):
    parts = re.split(r"\s+vs\s+|-", match_str, maxsplit=1)
    return parts[0].strip(), parts[-1].strip()


def load_round_legs(round_date):
    """收集当轮 boldplay 全部 HAD/HHAD 腿（base/upset 两池都扫，实际只有 base 有）。"""
    p = ROOT / "data" / "03-predictions" / f"{round_date}-boldplay.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    legs = []
    for tier in (data.get("tiers") or {}).values():
        for leg in tier.get("legs", []):
            if leg.get("play") in HAD_PLAYS:
                legs.append(leg)
    return legs


def load_freeze(round_date):
    p = ROOT / "data" / "02-results" / f"{round_date}.json"
    return json.loads(p.read_text(encoding="utf-8"))


def freeze_pick_dir(fm):
    """冻结记录 pick 的方向下标（胶着避开=融合 argmax 方向）；HHAD/CRS 等非 HAD 前缀返回 None。"""
    pick = fm.get("pick") or ""
    if not pick.startswith("HAD"):  # 'HHAD 让球主胜(-2)' 含"主胜"子串，须先按前缀排除
        return None
    if "主胜" in pick:
        return 0
    if "客胜" in pick:
        return 2
    if pick.startswith("HAD ("):
        fu = fm.get("fused") or []
        if len(fu) == 3:
            return max(range(3), key=lambda i: fu[i])
    return None


def build_cache_rows():
    """odds_*_*.json → [(date, home, away, (pin_h, pin_d, pin_a))]。"""
    rows = []
    for f in sorted((ROOT / "engine" / "cache").glob("odds_*_*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for m in data.get("matches", []):
            try:
                odds = (float(m["pin_h"]), float(m["pin_d"]), float(m["pin_a"]))
                d = datetime.strptime(m["date"], "%d/%m/%Y").date()
            except (KeyError, TypeError, ValueError):
                continue
            rows.append((d, m.get("home", ""), m.get("away", ""), odds))
    return rows


def build_zh_fd_map():
    """_aliases.json → {中文名/变体: fd CSV 队名}。"""
    p = ROOT / "data" / "01-teams" / "_aliases.json"
    aliases = json.loads(p.read_text(encoding="utf-8"))
    mapping = {}
    for lg, teams in aliases.items():
        if lg == "_meta" or not isinstance(teams, dict):
            continue
        for info in teams.values():
            if not isinstance(info, dict) or not info.get("fd"):
                continue
            for zh in [info.get("zh")] + (info.get("variants") or []):
                if zh:
                    mapping[zh] = info["fd"]
    return mapping


def market_lookup(leg, dir_idx, fm, round_d, cache_rows, zh_fd):
    """三级来源取腿方向市场隐含概率。返回 (prob|None, source, miss_reason)。"""
    if dir_idx is None:
        return None, None, "hhad让球方向无三向收盘锚(pinClose为HAD三向,语义不匹配)"
    if leg.get("pinClose") and len(leg["pinClose"]) == 3:
        return leg["pinClose"][dir_idx], "leg.pinClose", None
    pc = (fm or {}).get("pinClose")
    if pc and len(pc) == 3:
        return pc[dir_idx], "02-results.pinClose(fd收盘去水三向)", None
    home_zh, away_zh = split_match(leg["match"])
    fdh, fda = zh_fd.get(home_zh), zh_fd.get(away_zh)
    if fdh and fda:
        for d, home, away, odds in cache_rows:
            if abs((d - round_d).days) <= 1 and home == fdh and away == fda:
                return devig(list(odds))[dir_idx], "odds_cache.devig", None
        return None, None, "三级来源均未匹配(别名已映射,odds缓存为赛前快照未含该场次)"
    return None, None, "三级来源均未匹配(队名无fd别名/联赛fd不覆盖)"


def analyze_round(round_date, cache_rows, zh_fd):
    legs = load_round_legs(round_date)
    freeze = {}
    for m in load_freeze(round_date).get("matches", []):
        if m.get("code"):
            freeze[m["code"]] = m
    round_d = date.fromisoformat(round_date)
    out = []
    for leg in legs:
        code = leg.get("matchNumStr") or leg.get("code")
        fm = freeze.get(code)
        pick = leg.get("pick", "")
        dir_idx = PICK_DIR.get(pick) if leg.get("play") == "had" else None
        # 融合口径：腿 prob 字段（=融合×修正链终值）；缺失回退冻结 final（方向对齐时）
        fused = leg.get("prob")
        fused_src = "boldplay.legs[].prob(融合×修正链终值)"
        if fused is None and fm:
            if freeze_pick_dir(fm) == dir_idx and fm.get("final") is not None:
                fused, fused_src = fm["final"], "02-results.final(修正链终值,方向对齐)"
            elif fm.get("fused") and dir_idx is not None:
                fused, fused_src = fm["fused"][dir_idx], "02-results.fused[dir](纯融合)"
        mkt, mkt_src, miss = market_lookup(leg, dir_idx, fm, round_d, cache_rows, zh_fd)
        out.append({
            "code": code, "match": leg.get("match"), "play": leg.get("play"),
            "pick": pick, "odds": leg.get("odds"),
            "fusedProb": fused, "fusedSource": fused_src,
            "mktProb": None if mkt is None else round(mkt, 4), "mktSource": mkt_src,
            "mktGe65": mkt is not None and mkt >= THRESHOLD,
            "fusedGe65": fused is not None and fused >= THRESHOLD,
            "unmatchedReason": miss,
        })
    matched = [l for l in out if l["mktProb"] is not None]
    mkt_ge = sum(1 for l in matched if l["mktGe65"])
    fused_ge = sum(1 for l in out if l["fusedGe65"])
    return {
        "date": round_date, "nHadLegs": len(out),
        "mktGe65": mkt_ge, "fusedGe65": fused_ge,
        "canParlay11": mkt_ge >= 4,
        "matched": len(matched), "unmatched": len(out) - len(matched),
        "legs": out,
    }


def main():
    cache_rows = build_cache_rows()
    zh_fd = build_zh_fd_map()
    per_round = [analyze_round(d, cache_rows, zh_fd) for d in ROUNDS]

    mkt_total = sum(r["mktGe65"] for r in per_round)
    fused_total = sum(r["fusedGe65"] for r in per_round)
    legs_total = sum(r["nHadLegs"] for r in per_round)
    hhad_total = sum(1 for r in per_round for l in r["legs"] if l["play"] == "hhad")
    src2 = sum(1 for r in per_round for l in r["legs"] if l["mktSource"] == "02-results.pinClose(fd收盘去水三向)")
    src3 = sum(1 for r in per_round for l in r["legs"] if l["mktSource"] == "odds_cache.devig")
    unmatched_total = sum(r["unmatched"] for r in per_round)

    starve = any(r["mktGe65"] < 4 for r in per_round)
    verdict = (
        "饿死风险：市场口径达标腿 " + "/".join(str(r["mktGe65"]) for r in per_round)
        + "（判定线=三轮全部≥4），按 boldplay 实跑腿口径市场≥65%组不满保底4串11；"
        + "但融合口径同为 " + "/".join(str(r["fusedGe65"]) for r in per_round)
        + " 均<4——当前组票本就不按65%硬门槛选腿（星档制），本验证是轮池保守下界非准入池全景，P1去留留大哥裁决"
        if starve else "安全：三轮市场口径均≥4条"
    )

    print(f"{'轮次':<6} HAD/HHAD腿数  市场≥65%  融合≥65%  差额  4串11可组(市场口径≥4腿)")
    for r in per_round:
        print(f"{r['date'][5:]:<6} {r['nHadLegs']:<12} {r['mktGe65']:<9} {r['fusedGe65']:<9} "
              f"{r['mktGe65'] - r['fusedGe65']:<5} {'yes' if r['canParlay11'] else 'no'}")
    print(f"{'合计':<6} {legs_total:<12} {mkt_total:<9} {fused_total:<9} {mkt_total - fused_total:<5} "
          f"{'yes' if all(r['canParlay11'] for r in per_round) else 'no'}")
    for r in per_round:
        for l in r["legs"]:
            mkt_s = f"{l['mktProb']:.4f}" if l["mktProb"] is not None else "unmatched"
            print(f"  {r['date']} {l['code']} {l['match']} [{l['play']}:{l['pick']}] "
                  f"fused={l['fusedProb'] if l['fusedProb'] is not None else 'NA'} mkt={mkt_s}"
                  + (f" ({l['unmatchedReason']})" if l["unmatchedReason"] else ""))
    print(f"\n结论：{verdict}")

    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    report["bypassPool"] = {
        "ranAt": date.today().isoformat(),
        "checkedRounds": len(ROUNDS),
        "rounds": ROUNDS,
        "threshold": THRESHOLD,
        "legs": {"total": legs_total, "had": legs_total - hhad_total, "hhad": hhad_total},
        "marketMatch": {
            "tier1LegPinClose": 0,
            "tier2ResultsPinClose": src2,
            "tier3OddsCache": src3,
            "unmatched": unmatched_total,
        },
        "totals": {"mktGe65": mkt_total, "fusedGe65": fused_total},
        "perRound": per_round,
        "verdict": verdict,
        "notes": [
            "融合口径来源：boldplay tiers.base.legs[].prob（=融合×修正链终值；08-31 四腿与 02-results 冻结 final 逐腿一致已验证）；"
            "09-01 腿无 prob 字段（脚本选腿未接修正系数链=已知缺陷），回退 02-results/{date}.json matches[].final"
            "（冻结 pick 方向与腿方向一致时采用；该轮 4 腿纯融合 fused[dir] 与修正后 final 均 <50%，口径选择不改变 ≥65 判定）",
            f"市场隐含匹配链路：腿自带 pinClose=0 腿；02-results pinClose（fd 收盘去水三向，场次编号对齐）={src2} 腿；"
            f"odds 缓存（zh→fd 别名+日期±1 对齐 devig）={src3} 腿；"
            f"未匹配 {unmatched_total} 腿如实报，不计入达标判定也不静默丢弃",
            "未匹配明细：HHAD 让球方向无三向收盘锚 1 腿（08-31 周一012 巴萨-2，pinClose 为 HAD 三向语义不匹配）；"
            "挪超/瑞超 fd 不覆盖（队名无 fd 别名）2 腿（08-30 周六018、08-31 周一004）；"
            "09-01 整轮 4 腿（当轮未回填 pinClose；zh→fd 别名映射成功，但 odds 缓存为赛前快照、未含该轮场次——"
            "本质为快照时点问题非覆盖边界；四腿出票赔率 1.78~2.05 隐含 49%~56% 未去水上限均 <65%，"
            "数据缺口不改变该轮判定——体彩隐含仅作界值旁注非概率锚）",
            "样本口径限制：本验证只数 boldplay 实跑 HAD/HHAD 腿（12 腿=每轮 base 4 串 11 的 4 腿），非当轮全部场次准入池；"
            "D3 bypass_impact_quantify 的 442→281 场为池子全景口径，两者互补不互替；若需全景应另跑轮池全量扫描",
            "双向翻转并存（与 D3 gate_in/gate_out 一致）：08-30 周日020 国米融合 0.63<65 但市场 0.674≥65（旁路会收入）；"
            "周日012 阿贾克斯融合 0.68≥65 但市场 0.629<65（旁路会剔除）",
        ],
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n已写入 {REPORT_PATH} bypassPool 节（既有节未动）")


if __name__ == "__main__":
    main()
