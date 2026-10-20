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
KJ_MIN = 3                      # keyJudgments 条数门槛（SKILL.md §3·v3.4 补入校验）
RAW_SUM_WARN = (0.5, 2.0)
# 2026-10-15 大哥拍板直接上 v2：冻结前 TTG 维度锚市场（治臣填格低分倾向 −0.78 球），
# 形状维度（同总进球格间主客分配）保留独立判断。epoch 登记 engine/cache/chain_registry.json。
CHAIN_VERSION = "llm-31grid-v2"


def _market_ttg(card: dict) -> float | None:
    """卡市场层 TTG 赔率 → 期望总进球。与生产链同口径优先（freq_band power 去水+s7=7.4），
    freq_band 不可达时退化 ttg_anchor 朴素去水（s7=7）——锚定是增益不是依赖。"""
    ttg = ((card.get("layers") or {}).get("market") or {}).get("ttg") or {}
    if not ttg:
        return None
    try:
        from freq_band import _ttg_market_expect
        return _ttg_market_expect(ttg)
    except Exception:                                  # noqa: BLE001 —— 独立可跑降级
        from ttg_anchor import market_ttg_expect
        return market_ttg_expect(ttg)


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
    _check_kj_conf(pred, issues)
    return (not issues), issues


def _check_kj_conf(pred: dict, issues: list[str]) -> None:
    """keyJudgments 条数/完整性 + confidence 枚举（v2 手填与 v3 结构化两路共用）。"""
    kj = pred.get("keyJudgments") or []
    # 条数门槛 ≥3（v3.4 2026-10-19 补齐 SKILL.md §3 规格——原校验只判 ≥1,放过了
    # 10-14~16 三天 38 场「三条压成一条」的卷:形态+H2H+伤停挤在单个 evidence 字段,
    # 压扁后无法逐条评判对错=归因失效。旧卷是凭证不改写,按 KJ_V1_COMPRESSED 留痕）
    if len(kj) < KJ_MIN:
        issues.append(f'{pred.get("code")}: keyJudgments 需≥{KJ_MIN} 条（现 {len(kj)} 条）'
                      f'——每条一个独立论点,勿把多论据压进单条 evidence')
    if any(not (x.get("judgment") and x.get("evidence")) for x in kj):
        issues.append(f'{pred.get("code")}: keyJudgments 条目不完整（judgment/evidence 缺）')
    if pred.get("confidence") not in CONF_LEVELS:
        issues.append(f'{pred.get("code")}: confidence 非法')


def normalize_to_keys(p_crs: dict, keys: list[str]) -> tuple[dict, float]:
    """条件归一到市场键集（spec·对拍键集口径），返回 (分布, 原始和)。"""
    raw_sum = sum(float(v) for v in p_crs.values())
    sub = {k: float(p_crs.get(k, 0.0)) for k in keys}
    z = sum(sub.values())
    if z <= 0:
        n = len(keys)
        return {k: 1.0 / n for k in keys}, raw_sum          # 全零兜底均匀（记录 issues 由调用方）
    return {k: v / z for k, v in sub.items()}, raw_sum


def freeze(sale_date: str, preds: list[dict], revision: int | None = None,
           reason: str | None = None) -> Path:
    """逐场校验→归一→v2锚定→写冻结卷。主卷幂等只读（防事后改）。

    修正卷（rules-registry R008）：revision=N 写 {date}-r{N}.json，带 revisionOf+
    revisionReason，消费方（llm_eval/L族）按 -rN 优先读。修正卷不必先有主卷。"""
    suffix = f"-r{revision}" if revision else ""
    out = PREDS_ROOT / f"{sale_date}{suffix}.json"
    if out.exists():
        return out
    frozen = {"frozenAt": datetime.now().isoformat(timespec="seconds"),
              "chain": CHAIN_VERSION, "preds": []}
    if revision:
        frozen["revisionOf"] = sale_date
        frozen["revisionReason"] = reason or ""
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
        # v2 锚定：TTG 维度锚市场、形状维度保留（同总进球格间比例守恒）。
        # 无市场 TTG（卡缺 ttg）→ 不锚，anchor=null 留痕——锚定是增益不是依赖。
        anchor = None
        target = _market_ttg(card)
        if target:
            from ttg_anchor import dist_expect, solve_tilt, tilt_dist
            before = dist_expect(norm)
            if abs(before - target) > 1e-9:
                k = solve_tilt(norm, target)
                norm = tilt_dist(norm, k)
            else:
                k = 0.0
            anchor = {"k": round(k, 4), "before": round(before, 3),
                      "target": round(target, 3)}
        frozen["preds"].append({
            "code": pred["code"], "cardHash": card["cardHash"], "p_crs": norm,
            "pRawSum": round(raw_sum, 4),
            "rawSumWarn": not (RAW_SUM_WARN[0] <= raw_sum <= RAW_SUM_WARN[1]),
            "contextStatus": ctx.get("status"),
            "anchor": anchor,
            "keyJudgments": pred["keyJudgments"], "confidence": pred["confidence"]})
    PREDS_ROOT.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(frozen, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


# ── v3.6 结构化生成 ──────────────────────────────────────────────────────
# 手填 31 格 → 三参数(direction/shape/evidenceTier)输入 + 机器生成 + 拒冻四闸。
# 病根（10-19 轮实证）：手填格在"方向改了格不动"——003 判客胜却把 0:3 填到市场的
# 0.49 倍，方向对了分布照输。机制解=带内继承：三向质量由判断定，带内形状继承市场。
OTHER_TOT = 5.5    # other_* 兜底格的总进球代表值（6+ 球质的折中，校准脚本同口径）


def dir_of_key(k: str) -> str:
    """pick 键 → 三向（h/d/a）。other_ 前缀取末字母；格键按比分。开发者 sszhang"""
    if k.startswith("other_"):
        return k[-1]
    h, a = (int(x) for x in k.split(":"))
    return "h" if h > a else ("d" if h == a else "a")


def tot_of_key(k: str) -> float:
    return OTHER_TOT if k.startswith("other_") else sum(int(x) for x in k.split(":"))


SHAPE_TEMPLATES = {          # (加权格集|谓词, 加权倍率, 减权格集|谓词, 减权倍率)——设计 §4.2
    "crush": ({"3:0", "4:0", "3:1", "4:1"}, 1.8, {"1:1", "0:0"}, 0.7),
    "lock":  ({"0:0", "1:0", "0:1", "1:1"}, 1.5, "big", 0.5),    # big=总球≥4 全格
    "leaky": ({"2:2", "3:2", "2:3", "3:3"}, 1.8, "cs", 0.6),     # cs=零封格（某方 0）
    "open":  ({"1:2", "2:1", "2:2", "1:1"}, 1.3, {"0:0"}, 0.6),
}


def _pred_hit(k: str, spec) -> bool:
    if isinstance(spec, str):                     # 谓词简写
        if spec == "big":
            return tot_of_key(k) >= 4
        if spec == "cs":
            return ":" in k and (int(k[0]) == 0 or int(k[2]) == 0)
    return k in spec


def _apply_template(p: dict, st) -> dict:
    """格形指纹：四型各自加权/减权格集（设计 §4.2）。调形后由 generate_dist 归一。"""
    up, uw, down, dw = st
    return {k: v * (uw if _pred_hit(k, up) else dw if _pred_hit(k, down) else 1.0)
            for k, v in p.items()}


EVIDENCE_TIERS = ("state", "structural", "info")
OVERRIDE_MAX = 5                   # cellOverrides 上限（设计 §三：手动干预留口但封顶）


def validate_structured(pred: dict, market_keys: list[str]) -> tuple[bool, list[str]]:
    """结构化输入校验（设计 §三）。kj≥3 与 confidence 复用 _check_kj_conf。开发者 sszhang"""
    issues: list[str] = []
    d = pred.get("direction") or {}
    if set(d) != {"h", "d", "a"} or abs(sum(d.values()) - 1.0) > 0.02:
        issues.append(f'{pred.get("code")}: direction 须 h/d/a 且和≈1（现 {d}）')
    if pred.get("evidenceTier") not in EVIDENCE_TIERS:
        issues.append(f'{pred.get("code")}: evidenceTier ∈ {EVIDENCE_TIERS}'
                      f'（现 {pred.get("evidenceTier")}）')
    if (pred.get("shape") or {}).get("type") not in SHAPE_TEMPLATES:
        issues.append(f'{pred.get("code")}: shape.type ∈ {tuple(SHAPE_TEMPLATES)}')
    ov = pred.get("cellOverrides") or {}
    if len(ov) > OVERRIDE_MAX:
        issues.append(f'{pred.get("code")}: cellOverrides 须≤{OVERRIDE_MAX} 格（现 {len(ov)}）')
    for k in ov:
        if k not in market_keys:
            issues.append(f'{pred.get("code")}: 覆盖格 {k} 不在市场键集')
    _check_kj_conf(pred, issues)
    return (not issues), issues


def _apply_xg(p: dict, shape: dict) -> dict:
    """xG 双参二阶调整（设计 §4.1④）：xgSum≥2.8 抬大球带；|xgDiff|≥0.8 抬对应向大胜格。
    倍率封顶 ×2（防极端参数失控）。参数缺省/null=不调（消融口径）。开发者 sszhang"""
    xs, xd = shape.get("xgSum"), shape.get("xgDiff")
    out = dict(p)
    if isinstance(xs, (int, float)) and xs >= 2.8:
        f = min(1 + 0.3 * (xs - 2.8), 2.0)
        out = {k: (v * f if tot_of_key(k) >= 4 else v) for k, v in out.items()}
    if isinstance(xd, (int, float)) and abs(xd) >= 0.8:
        f = min(1 + 0.4 * (abs(xd) - 0.8), 2.0)
        side = "h" if xd > 0 else "a"
        out = {k: (v * f if dir_of_key(k) == side and ":" in k
                   and abs(int(k[0]) - int(k[2])) >= 3 else v)
               for k, v in out.items()}
    return out


def generate_dist(direction: dict, market: dict, shape: dict,
                  overrides: dict | None = None) -> dict:
    """五步确定性生成 31 格（设计 §4.1）。market=去水 pick 键分布（须含全部计分键）。
    步序：①市场带内形状 ②三向分摊 ③模板调形 ④xG 双参 ⑤覆盖归一。同参同出（P26）。
    开发者 sszhang"""
    bands: dict[str, dict] = {"h": {}, "d": {}, "a": {}}
    for k, v in market.items():
        bands[dir_of_key(k)][k] = v
    p = {}
    for d, band in bands.items():
        z = sum(band.values()) or 1.0
        for k, v in band.items():
            p[k] = direction[d] * v / z
    st = SHAPE_TEMPLATES.get((shape or {}).get("type"))
    if st:
        p = _renorm_bands(_apply_template(p, st), direction)
    p = _renorm_bands(_apply_xg(p, shape or {}), direction)
    for k, v in (overrides or {}).items():
        if k in p:
            p[k] = float(v)
    z = sum(p.values())
    return {k: v / z for k, v in p.items()}


def _renorm_bands(p: dict, direction: dict) -> dict:
    """按三向带重归一：方向(direction)与形状(shape/xG)正交——调形只许带内换形,
    不许跨带搬质量。模板格集天然跨带（crush 抬 3:0 压 1:1）,不归一则实测
    5.8pt 从平局带漂进主胜带,direction 被模板悄悄改写且闸4 测不出。开发者 sszhang"""
    out = {}
    for d in ("h", "d", "a"):
        band = {k: v for k, v in p.items() if dir_of_key(k) == d}
        z = sum(band.values())
        for k, v in band.items():
            out[k] = direction[d] * v / z if z > 0 else 0.0
    return out
