#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""预测偏差归因引擎：对错题逐场判别偏差因子，落 attribution.json。

设计：docs/2026-08-29-attribution-design.html（四层12因子 + 判别树 + 消融门）。
P2 判别树：⓪F5精确(fusedPre对fused错·修正乘子实锤) → ①F3/F4(pinClose真收盘：
分歧且市场对——DC对被稀释=F4/DC也错=F3) → ②F5近似(dc argmax·低置信·无pinClose场)
→ ③F1(λ失准|Δ进球|>1.5) → ④F9兜底；F10(赔率漂移)在 build() 独立叠加。
数据源：data/02-results/*.json 主文件（非 corpus——需 dc/fused/pinClose 数组）。
"""
import json
import re
from collections import defaultdict
from datetime import date
from pathlib import Path

from common import log, ROOT

OUT = ROOT / "data" / "04-summaries" / "attribution.json"
RESULTS_DIR = ROOT / "data" / "02-results"
SCORE_ODDS_DIR = ROOT / "engine" / "cache" / "score_odds"

# 方向 → 三向数组下标
_DIR_IDX = {"主胜": 0, "胜": 0, "平": 1, "平局": 1, "客胜": 2}


def pick_to_index(play: str, pick: str) -> int | None:
    """'HAD 客胜' → 2。非 HAD 或无法解析 → None。

    兼容带后缀的 pick：'主胜(方案外)' → 0（strip 括号注释后缀，I-2 修正）。
    """
    if play != "HAD":
        return None
    direction = pick.split("(")[0].strip()
    return _DIR_IDX.get(direction)


def result_to_idx(result: str) -> int | None:
    """'3-1'→0(主胜) / '0-2'→2(客胜) / '2-2'→1(平)。"""
    if not result or "-" not in str(result):
        return None
    parts = str(result).split("-")
    try:
        h, a = int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return None
    if h > a:
        return 0
    if h < a:
        return 2
    return 1


def _parse_pick(pick: str) -> tuple[str, str]:
    """'HAD 客胜' → ('HAD','客胜')。"""
    if " " in pick:
        p, d = pick.split(" ", 1)
        return p.strip(), d.strip()
    return "", pick.strip()


def correction_flipped(dc: list, fused: list, result_idx: int) -> bool | None:
    """F5 近似（R4 低置信）：dc 最高向==结果（DC 原本对）且 fused 最高向!=结果（融合后错）。

    P1 无法区分 chain 修正乘子 vs 融合 a/b 配比导致 → 统一归 F5；
    F4（纯融合稀释）待 P2 落盘 chainSteps[] 后从 F5 中分离。
    数据不足（无 dc/fused 或 result_idx 越界）→ None。
    """
    if not dc or not fused or result_idx is None or result_idx < 0 or result_idx >= 3:
        return None
    dc_best = dc.index(max(dc))
    fused_best = fused.index(max(fused))
    return dc_best == result_idx and fused_best != result_idx


def classify(rec: dict) -> dict:
    """主判别树：错题 → {primary, secondary, evidence, confidence}。

    P2 判别顺序：⓪F5精确 → ①F3/F4 → ②F5近似 → ③F1 → ④F9（同模块 docstring）。
    pickDeviation 标记：pick≠fused argmax（方案外/搏冷场）——错在选法不在概率。
    F10 执行层在 build() 中独立叠加（不在此函数，因需 score_odds 外部数据）。
    """
    play, direction = _parse_pick(rec.get("pick") or "")
    pick_idx = pick_to_index(play, direction)
    result_idx = result_to_idx(rec.get("result") or "")
    dc = rec.get("dc")
    fused = rec.get("fused")
    ev = {"pfinalPick": None, "dcBest": None, "fusedBest": None,
          "pickIdx": pick_idx, "resultIdx": result_idx,
          "result": rec.get("result")}

    # 非 HAD 或结果不可解析 → F9 低置信（R7 变体待 P2）
    if pick_idx is None or result_idx is None:
        return {"primary": "F9", "secondary": [], "evidence": ev, "confidence": "low"}

    if fused and pick_idx < len(fused):
        ev["pfinalPick"] = round(float(fused[pick_idx]), 4)
    if dc:
        ev["dcBest"] = dc.index(max(dc))
    if fused:
        ev["fusedBest"] = fused.index(max(fused))
        # pick 偏离模型最优向（方案外/搏冷场）：错在选法不在概率，账本须可区分（2026-08-23 周日016 实例）
        if pick_idx != ev["fusedBest"]:
            ev["pickDeviation"] = True

    # ⓪ F5 精确重放（P2：fusedPre 修正前三向对 + fused 错 = 修正乘子实锤）
    fused_pre = rec.get("fusedPre")
    if fused_pre and len(fused_pre) == 3:
        if fused_pre.index(max(fused_pre)) == result_idx and \
                fused and fused.index(max(fused)) != result_idx:
            ev["replay"] = "fusedPre"
            return {"primary": "F5", "secondary": [], "evidence": ev, "confidence": "high"}

    # ① F3/F4 市场锚分歧（P2：pinClose 真收盘·先于 F5 近似——dc对+fused错+pin对=F4实锤）
    pin = rec.get("pinClose")
    ev["pinSource"] = rec.get("pinSource")   # 透传（无 pinClose 场也保留 ambiguous/none 观测粒度）
    if pin and len(pin) == 3:
        pin_best = pin.index(max(pin))
        dc_best = ev.get("dcBest")
        fused_best = ev.get("fusedBest")
        if fused_best is not None and fused_best != pin_best and pin_best == result_idx:
            if dc_best == result_idx:
                return {"primary": "F4", "secondary": [], "evidence": ev, "confidence": "high"}
            return {"primary": "F3", "secondary": [], "evidence": ev, "confidence": "high"}

    # ② F5 修正/融合背锅（dc 对 + fused 错·近似，pinClose 缺失或同向时）
    if correction_flipped(dc, fused, result_idx):
        ev["replay"] = "dc_approx"
        return {"primary": "F5", "secondary": [], "evidence": ev, "confidence": "low"}

    # ③ F1 λ 失准（P2：|实际总进球 − λ总期望| > 1.5）
    lh, la = rec.get("lambdaHome"), rec.get("lambdaAway")
    if lh is not None and la is not None:
        h, _, a = str(rec.get("result") or "").partition("-")
        try:
            gap = abs((int(h) + int(a)) - (float(lh) + float(la)))
            if gap > LAMBDA_GAP_THRESHOLD:
                ev["lambdaGap"] = round(gap, 2)
                return {"primary": "F1", "secondary": [], "evidence": ev, "confidence": "high"}
        except (TypeError, ValueError):
            pass

    # ④ F9 随机兜底（dc 也错，或 dc/fused 同向错）
    return {"primary": "F9", "secondary": [], "evidence": ev, "confidence": "high"}


def odds_drift_buy_heat(drift: dict | None) -> bool:
    """R5 判据：出票赔率 < 漂移后赔率（赔率向不利方向漂移=追热入场）。

    laterOdds > pickOdds × 1.02（2% 涨幅阈值防噪声）→ 追热。
    drift={pickOdds, laterOdds}：来源 score_odds oddsUpdatedAt 时间轴回放。
    """
    if not drift:
        return False
    po = drift.get("pickOdds")
    lo = drift.get("laterOdds")
    if po is None or lo is None:
        return False
    try:
        return float(lo) > float(po) * 1.02
    except (TypeError, ValueError):
        return False


# 方向下标 → score_odds had 扁平 key（h=主胜/d=平/a=客胜）
_HAD_KEY = {0: "h", 1: "d", 2: "a"}

# F1 判别阈值：|实际总进球 − λ总期望| 超此值判 λ 失准（设计 §6 ③）
LAMBDA_GAP_THRESHOLD = 1.5


def load_odds_drift(code: str, pick_odds, pick_idx: int | None) -> dict | None:
    """从 score_odds 日存档取该场 pick 方向的快照赔率（R5）。

    had 扁平结构 {h,d,a}（非嵌套）；遍历所有日文件按 matchNumStr 匹配 code。
    文件按日期升序遍历，laterOdds 取最晚日期匹配快照（I-1 修正：避免文件系统返回顺序不确定）。
    返回 {pickOdds, laterOdds}；无存档或无该场 → None（F10 不触发，安全降级）。
    P1 简化：不校验快照时刻 vs 出票时刻先后（待 P2 oddsUpdatedAt 时间轴）。
    """
    if not SCORE_ODDS_DIR.exists() or pick_idx not in _HAD_KEY:
        return None
    had_key = _HAD_KEY[pick_idx]
    later = None
    for p in sorted(SCORE_ODDS_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for day in data.get("matchDays") or []:
            for m in day.get("matches") or []:
                if m.get("matchNumStr") != code:
                    continue
                had = m.get("had") or {}
                val = had.get(had_key)
                if val is not None:
                    try:
                        later = float(val)
                    except (TypeError, ValueError):
                        pass
    if later is None:
        return None
    try:
        po = float(pick_odds)
    except (TypeError, ValueError):
        return None
    return {"pickOdds": po, "laterOdds": later}


def _is_error(rec: dict) -> bool:
    """错题入场：directionHit=False（P1 仅处理 HAD 方向错；CRS 待 P2 R7 变体）。"""
    return rec.get("directionHit") is False


def build() -> tuple[dict, dict]:
    """遍历 02-results 主文件 → 对错题跑 classify → 叠加 F10 → 落 attribution.json。

    返回 (records, factorStats)。主文件=无 -rN 后缀（终审版，同 corpus round_sort 语义）。
    已知限制：仅处理 pick 含 'HAD ' 前缀的场次（v4.6+ 格式）；早期无前缀格式
    （如 pick='主胜'，play 字段='胜平负'）被跳过（I-3，1 场历史数据，YAGNI 不兼容）。
    """
    records = {}
    seen_crossday = set()  # 跨日同场去重（同场卖两天进两个日期文件；corpus 2026-09-02 同款语义）
    for p in sorted(RESULTS_DIR.glob("*.json")):
        if p.name.startswith("_") or "-r" in p.stem:
            continue  # 跳过 _ 前缀和 -rN 过程快照
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        round_id = p.stem
        for m in data.get("matches") or []:
            if not _is_error(m):
                continue
            play, pick_txt = _parse_pick(m.get("pick") or "")
            if not play:
                continue
            match_str = re.sub(r"\[.*?\]", "", str(m.get("match") or "")).strip()
            if match_str and " vs " in match_str:  # 无对阵串的记录无法识别同场 → 不参与去重（防误杀）
                cd_key = (str(m.get("league") or "").split("(")[0], match_str, play, pick_txt)
                if cd_key in seen_crossday:
                    continue  # 同场同玩法同选法跨日复用 → 首见已归因
                seen_crossday.add(cd_key)
            key = f"{round_id[:10]}|{m.get('code')}|{play}"
            out = classify(m)
            out["source"] = "rule"
            out["confirmed"] = True   # rule 判定默认确认；llm 软标签才 false

            # F10 执行层叠加（独立判别，方向错时记次因）
            pick_idx = out["evidence"].get("pickIdx")
            drift = load_odds_drift(m.get("code"), m.get("odds"), pick_idx)
            if drift and odds_drift_buy_heat(drift):
                out.setdefault("secondary", []).append("F10")
                out["evidence"]["oddsDrift"] = drift
            records[key] = out

    # factorStats：主因频次（key 去重已保证按场）+ avgProbGap
    prim = defaultdict(lambda: [0, 0.0])   # [n, probgap_sum]
    sec = defaultdict(int)
    for r in records.values():
        f = r["primary"]
        prim[f][0] += 1
        pg = r["evidence"].get("pfinalPick")
        if pg is not None:
            prim[f][1] += (1.0 - float(pg))   # 错向置信度 → 损失量近似
        for s in r.get("secondary", []):
            sec[s] += 1
    factorStats = {}
    for f, (n, s) in prim.items():
        factorStats[f] = {"nPrimary": n, "nSecondary": sec.get(f, 0),
                          "avgProbGap": round(s / n, 4) if n else 0.0}

    candidates = [f for f, (n, _) in prim.items() if n >= 20]
    payload = {"schemaVersion": 1, "generatedAt": str(date.today()),
               "records": records, "factorStats": factorStats,
               "ablateCandidates": candidates, "resolved": {}}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    return records, factorStats


def main() -> None:
    records, stats = build()
    log("attribute", f"归因 {len(records)} 场错题 → {OUT.relative_to(ROOT)}")
    for f, s in sorted(stats.items(), key=lambda kv: -kv[1]["nPrimary"]):
        log("attribute", f"  {f}: 主因 {s['nPrimary']}场 / 次因 {s['nSecondary']} / avgGap {s['avgProbGap']}")
    cand = [f for f, s in stats.items() if s["nPrimary"] >= 20]
    if cand:
        log("attribute", f"⚠️ 消融候选（nPrimary≥20）：{cand}")
    else:
        mx = max((s["nPrimary"] for s in stats.values()), default=0)
        log("attribute", f"消融门槛未达（需≥20场，当前最高 {mx} 场）")


if __name__ == "__main__":
    main()
