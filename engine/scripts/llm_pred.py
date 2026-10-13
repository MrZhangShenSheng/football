# engine/scripts/llm_pred.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""llm_pred.py — 会话内 LLM 预测的校验/归一/冻结工具（spec §3）。

31 格（市场有价键）分布；校验失败整批拒绝；冻结后幂等只读（凭证·forward only 地基）。
开发者 sszhang"""
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREDS_ROOT = ROOT / "engine" / "cache" / "llm_preds"
CARDS_ROOT = ROOT / "engine" / "cache" / "llm_cards"
CONF_LEVELS = ("low", "medium", "high")
RAW_SUM_WARN = (0.5, 2.0)


def validate_pred(pred: dict, market_keys: list[str]) -> tuple[bool, list[str]]:
    """结构校验：键∈市场键集 / 0≤p≤1 / 判断≥1 条带 evidence / confidence 枚举。"""
    issues = []
    if not pred.get("code"):
        issues.append("缺 code")
    p = pred.get("p_crs") or {}
    if not p:
        issues.append(f'{pred.get("code")}: p_crs 空')
    for k, v in (p or {}).items():
        if k not in market_keys:
            issues.append(f'{pred.get("code")}: 键 {k} 不在市场键集')
        if not (isinstance(v, (int, float)) and 0.0 <= float(v) <= 1.0):
            issues.append(f'{pred.get("code")}: {k} 值非法 {v}')
    kj = pred.get("keyJudgments") or []
    if not kj or any(not (x.get("judgment") and x.get("evidence")) for x in kj):
        issues.append(f'{pred.get("code")}: keyJudgments 缺失或不完整')
    if pred.get("confidence") not in CONF_LEVELS:
        issues.append(f'{pred.get("code")}: confidence 非法')
    return (not issues), issues


def normalize_to_keys(p_crs: dict, keys: list[str]) -> tuple[dict, float]:
    """条件归一到市场键集（spec·对拍键集口径），返回 (分布, 原始和)。"""
    raw_sum = sum(float(v) for v in p_crs.values())
    sub = {k: float(p_crs.get(k, 0.0)) for k in keys}
    z = sum(sub.values())
    if z <= 0:
        n = len(keys)
        return {k: 1.0 / n for k in keys}, raw_sum          # 全零兜底均匀（记录 issues 由调用方）
    return {k: v / z for k, v in sub.items()}, raw_sum


def freeze(sale_date: str, preds: list[dict]) -> Path:
    """逐场校验（键集=该场数据卡市场层有价键）→归一→写冻结文件。幂等只读。"""
    out = PREDS_ROOT / f"{sale_date}.json"
    if out.exists():
        return out
    frozen = {"frozenAt": datetime.now().isoformat(timespec="seconds"), "preds": []}
    for pred in preds:
        card_p = CARDS_ROOT / sale_date / f'{pred.get("code")}.json'
        if not card_p.exists():
            raise SystemExit(f'[llm_pred] 缺数据卡 {card_p}——先 gen 数据卡')
        card = json.loads(card_p.read_text(encoding="utf-8"))
        ctx = card["layers"]["context"]
        market = card["layers"]["market"]["crs"] or {}
        # 键转换：体彩存档 sXXsYY → pick 键（与 crs_key 反向）；已是 pick 键直通
        mk = {}
        for k, v in market.items():
            kk = k if (":" in k) else (
                f"{int(k[1:3])}:{int(k[4:6])}" if k.startswith("s") and len(k) == 6 and k[3] == "s"
                else ("other_h" if k in ("s1sh", "胜其他") else "other_d" if k in ("s1sd", "平其他")
                      else "other_a" if k in ("s1sa", "负其他") else None))
            if kk and isinstance(v, (int, float)) and float(v) > 1.0:
                mk[kk] = float(v)
        keys = list(mk)
        good, issues = validate_pred(pred, keys)
        if not good:
            raise SystemExit(f'[llm_pred] 校验失败拒冻结: {issues}')
        norm, raw_sum = normalize_to_keys(pred["p_crs"], keys)
        frozen["preds"].append({
            "code": pred["code"], "cardHash": card["cardHash"], "p_crs": norm,
            "pRawSum": round(raw_sum, 4),
            "rawSumWarn": not (RAW_SUM_WARN[0] <= raw_sum <= RAW_SUM_WARN[1]),
            "contextStatus": ctx.get("status"),
            "keyJudgments": pred["keyJudgments"], "confidence": pred["confidence"]})
    PREDS_ROOT.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(frozen, ensure_ascii=False, indent=1), encoding="utf-8")
    return out
