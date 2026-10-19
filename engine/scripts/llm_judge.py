# engine/scripts/llm_judge.py
"""llm_judge.py — 会话 LLM 结构化自主判断的校验/冻结工具（v3·llm-autonomy-prereg）。

v2（llm_pred 31格填表+TTG锚定）已于 2026-10-16 淘汰（chain_registry 收口）：实测判断与产出
脱钩——tilt 锚定推向市场 TVD 相对缩减 53.9%、右尾选腿与模型最看好格交集 0/22（R022/R023）。
v3 判据 = engine/cache/llm-autonomy-prereg.json（跑前冻结·铁律 14②）：判断本身即产出与受测
对象，禁穷举 31 格、禁泊松摊格凑数。

输出契约（prereg outputContract·字段级校验，任一场不合格整批拒绝）：
  direction: {"call": 主胜|平|客胜|不站边, "evidence": str}          必填
  goalBand:  {"lo": int, "hi": int, "evidence": str}                必填（『2-3球』→ lo=2, hi=3）
  keyCells:  [{"cell": 市场有价键, "why": str}] × 0..5              允许空=「判断不出具体比分」是诚实产出
  against:   [{"item","evidence"}] ≥1 条 或 {"none": true, "why"}   反对项必给
  confidence: low|medium|high
forward only：冻结落盘幂等只读，修正走 -rN 修正卷（R008）；每卷钉 cardHash + preregHash；
开赛后冻结标 afterKickoff=true（结算侧降 descriptiveOnly，不删不拒——凭证留痕优于静默丢弃）。
开发者 sszhang"""
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from llm_card import _norm_ts  # noqa: E402  时刻规范化（'T'/秒位/时区后缀）

JUDGMENTS_ROOT = ROOT / "engine" / "cache" / "llm_judgments"
CARDS_ROOT = ROOT / "engine" / "cache" / "llm_cards"
PREREG_PATH = ROOT / "engine" / "cache" / "llm-autonomy-prereg.json"
CHAIN = "llm-autonomy"
DIRECTIONS = ("主胜", "平", "客胜", "不站边")
CONF_LEVELS = ("low", "medium", "high")
KEY_CELLS_MAX = 5          # prereg frozenParams.keyCells_max（禁再放宽——跑前冻结）
BAND_HI_MAX = 99           # hi 上限（99=开放上限，「3球及以上」→ lo=3, hi=99）


def _prereg_hash() -> str:
    return hashlib.sha256(PREREG_PATH.read_bytes()).hexdigest()[:16]


def _isnum(v) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def _market_keys(card: dict) -> list[str]:
    """卡市场层 crs → pick 键集（'h:a' / other_h|d|a；仅有价键，价≤1.0 视为无价）。
    键口径与 llm_pred.freeze / paper._llm_pick_key 同源（pick 键三态归一）。开发者 sszhang"""
    keys = []
    for k, v in (((card.get("layers") or {}).get("market") or {}).get("crs") or {}).items():
        if not _isnum(v) or float(v) <= 1.0:
            continue
        if ":" in k:
            keys.append(k)
        elif k.startswith("s") and len(k) == 6 and k[3] == "s":
            keys.append(f"{int(k[1:3])}:{int(k[4:6])}")
        elif k in ("s1sh", "胜其他"):
            keys.append("other_h")
        elif k in ("s1sd", "平其他"):
            keys.append("other_d")
        elif k in ("s1sa", "负其他"):
            keys.append("other_a")
    return keys


def _gap_flags(card: dict) -> list[str]:
    """上下文完整度机器自评（prereg descriptiveOnly『gapFlags 分层』的数据源）。

    判断方自报会有美化倾向，故由冻结器按卡层实况计算，不自报。开发者 sszhang"""
    ly = card.get("layers") or {}
    flags = []
    if (ly.get("intel") or {}).get("status") != "ok":
        flags.append("intel_missing")
    if (ly.get("lineup") or {}).get("status") != "ok":
        flags.append("lineup_missing")
    if (ly.get("context") or {}).get("status") != "ok":
        flags.append("context_missing")
    om = ly.get("ourModels") or {}
    if (om.get("v3wChain") or {}).get("status") != "ok":
        flags.append("v3w_missing")
    if (om.get("v4b") or {}).get("status") != "ok":
        flags.append("v4b_missing")
    if not ((ly.get("market") or {}).get("devig") or {}).get("crs"):
        flags.append("devig_missing")
    return flags


def validate_judgment(j: dict, market_keys: list[str]) -> list[str]:
    """字段级校验，返回问题清单（空=通过）。整批任一场不合格即拒冻结。开发者 sszhang"""
    code = j.get("code")
    issues = []
    if not code:
        return ["缺 code"]

    d = j.get("direction")
    if not isinstance(d, dict) or d.get("call") not in DIRECTIONS \
            or not str(d.get("evidence") or "").strip():
        issues.append(f"{code}: direction 须为 {{call∈{DIRECTIONS}, evidence}} 实体")

    gb = j.get("goalBand")
    if not isinstance(gb, dict):
        issues.append(f"{code}: goalBand 须为 {{lo,hi,evidence}} 实体")
    else:
        lo, hi = gb.get("lo"), gb.get("hi")
        if isinstance(lo, bool) or isinstance(hi, bool) \
                or not isinstance(lo, int) or not isinstance(hi, int):
            issues.append(f"{code}: goalBand lo/hi 须为 int（『2-3球』→ lo=2, hi=3）")
        elif not (0 <= lo <= hi <= BAND_HI_MAX):
            issues.append(f"{code}: goalBand 须 0≤lo≤hi≤{BAND_HI_MAX}，得 lo={lo} hi={hi}")
        if not str(gb.get("evidence") or "").strip():
            issues.append(f"{code}: goalBand 缺 evidence")

    kc = j.get("keyCells")
    if not isinstance(kc, list):
        issues.append(f"{code}: keyCells 须为列表（允许空=不站具体比分）")
    else:
        if len(kc) > KEY_CELLS_MAX:
            issues.append(f"{code}: keyCells 超上限 {KEY_CELLS_MAX}（prereg frozenParams）")
        for i, c in enumerate(kc):
            if not isinstance(c, dict) or not str(c.get("cell") or "").strip() \
                    or not str(c.get("why") or "").strip():
                issues.append(f"{code}: keyCells[{i}] 须为 {{cell, why}} 实体")
            elif c.get("cell") not in market_keys:
                issues.append(f"{code}: keyCells[{i}] cell={c.get('cell')!r} "
                              f"不在市场有价键集（prereg 红线：禁无价格）")

    ag = j.get("against")
    if isinstance(ag, list):
        if not ag:
            issues.append(f"{code}: against 须 ≥1 条反对项，或显式 {{none:true, why}}")
        for i, a in enumerate(ag):
            if not isinstance(a, dict) or not str(a.get("item") or "").strip() \
                    or not str(a.get("evidence") or "").strip():
                issues.append(f"{code}: against[{i}] 须为 {{item, evidence}} 实体")
    elif isinstance(ag, dict):
        if ag.get("none") is not True or not str(ag.get("why") or "").strip():
            issues.append(f'{code}: against 豁免须 {{"none": true, "why": "理由"}}')
    else:
        issues.append(f"{code}: against 缺失（反对项必给——没有反对项的判断不是判断）")

    if j.get("confidence") not in CONF_LEVELS:
        issues.append(f"{code}: confidence 非法（仅 {CONF_LEVELS}）")
    return issues


def freeze(sale_date: str, preds: list[dict], *, revision: int | None = None,
           reason: str | None = None, now: str | None = None,
           root: Path | None = None, cards_root: Path | None = None) -> Path:
    """逐场校验→钉 cardHash/kickoffAt/gapFlags→写冻结卷。主卷幂等只读（凭证纪律）。

    修正卷 revision=N 写 {date}-r{N}.json 带 revisionOf+revisionReason（R008）。
    任一场校验失败整批拒绝——半卷判断比无判断更危险（口径断裂）。开发者 sszhang"""
    if not PREREG_PATH.exists():
        raise SystemExit(f"[llm_judge] 缺判据文件 {PREREG_PATH}——无判据不得冻结")
    base = Path(root) if root else JUDGMENTS_ROOT
    cards = Path(cards_root) if cards_root else CARDS_ROOT
    suffix = f"-r{revision}" if revision else ""
    out = base / f"{sale_date}{suffix}.json"
    if out.exists():
        return out                                     # 幂等只读
    ph = _prereg_hash()
    stamp = now or datetime.now().isoformat(timespec="seconds")
    frozen = {"frozenAt": stamp, "chain": CHAIN,
              "prereg": "engine/cache/llm-autonomy-prereg.json", "preregHash": ph,
              "preds": []}
    if revision:
        frozen["revisionOf"] = sale_date
        frozen["revisionReason"] = reason or ""
    for j in preds:
        code = j.get("code")
        card_p = cards / sale_date / f"{code}.json"
        if not card_p.exists():
            raise SystemExit(f"[llm_judge] 缺数据卡 {card_p}——先 gen 数据卡")
        card = json.loads(card_p.read_text(encoding="utf-8"))
        issues = validate_judgment(j, _market_keys(card))
        if issues:
            raise SystemExit(f"[llm_judge] 校验失败拒冻结: {issues}")
        kickoff = card.get("kickoffAt") or ""
        after = bool(kickoff) and len(kickoff) >= 16 \
            and _norm_ts(stamp) > _norm_ts(kickoff)
        frozen["preds"].append({
            "code": code,
            "cardHash": card.get("cardHash"),
            "kickoffAt": kickoff or None,
            "afterKickoff": after,
            "gapFlags": _gap_flags(card),
            "direction": j["direction"], "goalBand": j["goalBand"],
            "keyCells": j["keyCells"], "against": j["against"],
            "confidence": j["confidence"],
        })
    base.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(frozen, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(out)                                   # 原子写（Windows rename 不覆盖）
    return out


def load_judgments(sale_date: str, *, root: Path | None = None) -> dict | None:
    """结算侧装载：-rN 取最大 N（数值序），无则主卷；全缺返回 None（诚实降级）。
    与 paper.load_llm_preds 同取卷纪律（R008 消费方口径）。开发者 sszhang"""
    base = Path(root) if root else JUDGMENTS_ROOT
    if not base.exists():
        return None
    p = None
    try:
        revs = sorted((f for f in base.iterdir()
                       if f.name.startswith(f"{sale_date}-r") and f.suffix == ".json"),
                      key=lambda f: int(f.stem.split("-r")[-1]))
        if revs:
            p = revs[-1]
    except (OSError, ValueError):
        pass
    if p is None:
        _main = base / f"{sale_date}.json"
        if not _main.exists():
            return None
        p = _main
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="LLM 结构化自主判断冻结器（v3）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    fz = sub.add_parser("freeze", help="冻结判断（输入=判断 JSON 文件）")
    fz.add_argument("sale_date")
    fz.add_argument("preds_file")
    fz.add_argument("--revision", type=int, default=None)
    fz.add_argument("--reason", default=None)
    sh = sub.add_parser("show", help="查看当日冻结卷摘要")
    sh.add_argument("sale_date")
    a = ap.parse_args()
    if a.cmd == "freeze":
        preds = json.loads(Path(a.preds_file).read_text(encoding="utf-8"))
        out = freeze(a.sale_date, preds, revision=a.revision, reason=a.reason)
        print(f"[llm_judge] 冻结 → {out}")
        return 0
    doc = load_judgments(a.sale_date)
    if not doc:
        print(f"[llm_judge] {a.sale_date} 无冻结卷")
        return 1
    print(f"chain={doc.get('chain')} frozenAt={doc.get('frozenAt')} "
          f"preregHash={doc.get('preregHash')} n={len(doc.get('preds') or [])}")
    for x in doc.get("preds") or []:
        kc = "、".join(c["cell"] for c in x.get("keyCells") or []) or "—"
        late = " ⚠开赛后" if x.get("afterKickoff") else ""
        gaps = len(x.get("gapFlags") or [])
        print(f"  {x['code']} {x['direction']['call']}  {x['goalBand']['lo']}-{x['goalBand']['hi']}球"
              f"  关键格[{kc}]  {x['confidence']}{late} 缺口{gaps}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
