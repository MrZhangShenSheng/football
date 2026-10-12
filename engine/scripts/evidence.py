# engine/scripts/evidence.py
# -*- coding: utf-8 -*-
"""v35 P1 证据链账本——每场预测九步输入输出落盘（设计 docs/2026-10-11-v35-foundation-design.html）。

铁设计：fail-open（仪表异常只警告不中断主流程）/ 幂等（开赛日+matchKey+step 同键覆盖）/
原子写（tmp+replace）/ 键口径=开赛日（物理比赛日）。开发者 sszhang
"""
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_ROOT = ROOT / "engine" / "cache" / "evidence"
SCHEMA_VERSION = 1

# 九步常量（禁魔法值）
STEP_ODDS = "S1_odds"            # 五池赔率快照引用
STEP_PROFILE = "S2_profiles"     # 画像引用（elo/xg/HST 指针+新鲜度）
STEP_DC = "S3_dc"                # DC 攻防参数 + rho
STEP_LAMBDA = "S4_lambda"        # V3W λ
STEP_DEVIG = "S5_devig"          # 市场去水
STEP_FUSION = "S6_fusion"        # 融合系数 + p_fused
STEP_MATRIX = "S7_matrix"        # 比分矩阵
STEP_HYPO = "S8_hypothesis"      # 假设层（会话登记）
STEP_SCHEME = "S9_scheme"        # 挡位方案（会话登记）
ALL_STEPS = (STEP_ODDS, STEP_PROFILE, STEP_DC, STEP_LAMBDA, STEP_DEVIG,
             STEP_FUSION, STEP_MATRIX, STEP_HYPO, STEP_SCHEME)

SOURCE_CODE = "code"
SOURCE_SESSION = "session"

# S8 假设层理由键（2026-10-12 回溯机制：登记时 payload 须含其一，否则「排除:假设层否决」
# 类结论成无头案——10-11 卡利亚里场教训：pick 只剩一句排除、理由未落档）
S8_REASON_KEYS = ("verdicts", "reason", "note")


def norm_key(match_id=None, code=None, league=None, home=None, away=None) -> str:
    """matchKey 优先级：matchId > 体彩编号 > 联赛|主|客（禁 ':'，Windows 双机兼容）。"""
    if match_id:
        return str(match_id)
    if code:
        return str(code)
    return f"{league}|{home}|{away}"


def record(match_day: str, match_key: str, step: str, payload: dict,
           source: str = SOURCE_CODE) -> bool:
    """幂等落盘一条步骤记录。fail-open：任何异常打印警告返回 False，绝不抛出。
    S8 假设层登记缺理由键（S8_REASON_KEYS）→ 打红警（仍落盘，不阻断——回溯机制四件套）。"""
    try:
        if step == STEP_HYPO and not any(payload.get(k) for k in S8_REASON_KEYS):
            print(f"[evidence] ⚠ S8 假设层登记缺理由（{S8_REASON_KEYS} 须含其一）"
                  f"@{match_day}/{match_key}——结论将成无头案", file=sys.stderr)
        day_dir = EVIDENCE_ROOT / match_day
        day_dir.mkdir(parents=True, exist_ok=True)
        f = day_dir / f"{match_key}.json"
        doc = (json.loads(f.read_text(encoding="utf-8")) if f.exists()
               else {"schema": SCHEMA_VERSION, "day": match_day, "matchKey": match_key, "steps": {}})
        doc["steps"][step] = {"source": source,
                              "recordedAt": datetime.now().isoformat(timespec="seconds"),
                              "payload": payload}
        tmp = f.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(f)                    # 原子写
        return True
    except Exception as exc:              # noqa: BLE001 —— fail-open 铁设计
        print(f"[evidence] 警告: {step}@{match_day}/{match_key} 记录失败 {exc}（不中断）",
              file=sys.stderr)
        return False


def check(match_day: str) -> dict:
    """完整性检查：每场缺哪些步（缺步标注不补造——设计档 P1 铁设计）。"""
    day_dir = EVIDENCE_ROOT / match_day
    if not day_dir.exists():
        return {}
    out = {}
    for f in sorted(day_dir.glob("*.json")):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except Exception:                  # noqa: BLE001 —— 脏文件不炸检查
            out[f.stem] = ["<文件损坏>"]
            continue
        out[f.stem] = [s for s in ALL_STEPS if s not in doc.get("steps", {})]
        # S8 已登记但缺理由 → 标注（缺步标注不补造；缺理由同哲——登记纪律靠警示收口）
        s8 = doc.get("steps", {}).get(STEP_HYPO)
        if s8 and not any((s8.get("payload") or {}).get(k) for k in S8_REASON_KEYS):
            out[f.stem].append(f"{STEP_HYPO}缺理由")
    return out


def show(match_day: str, match_key: str) -> None:
    """控制台打表：这场怎么算的（大哥要求直出不切窗口）。"""
    f = EVIDENCE_ROOT / match_day / f"{match_key}.json"
    if not f.exists():
        print(f"[evidence] 无记录: {f}")
        return
    doc = json.loads(f.read_text(encoding="utf-8"))
    print(f"=== 证据链 {match_day} {match_key} (schema {doc['schema']}) ===")
    for step in ALL_STEPS:
        rec = doc["steps"].get(step)
        if not rec:
            print(f"  {step:14s} ✗ 缺")
            continue
        body = json.dumps(rec["payload"], ensure_ascii=False)
        print(f"  {step:14s} ✓ [{rec['source']}] {rec['recordedAt']} · {body[:160]}")


POOL_KEYS = ("had", "crs", "ttg", "hhad", "hafu")


def _matchid_map() -> dict:
    """在售清单 编号→matchId（S1 键与 feed/strength 链统一到 matchId·读失败返回空表退化为编号键）。"""
    try:
        d = json.loads((ROOT / "engine" / "cache" / "sporttery_matches.json").read_text(encoding="utf-8"))
    except Exception:                      # noqa: BLE001
        return {}
    out = {}
    for m in d.get("matches") or []:
        if m.get("code") and m.get("matchId"):
            out[str(m["code"])] = str(m["matchId"])
    for blk in (d.get("value") or {}).get("matchInfoList") or []:
        for m in blk.get("subMatchList") or []:
            if m.get("matchNumStr") and m.get("matchId"):
                out[str(m["matchNumStr"])] = str(m["matchId"])
    return out


def record_s1_from_archive(archive_path_or_day: str) -> int:
    """从 score_odds 日存档逐场登记 S1（键=matchId 经在售清单 join·缺省编号；开赛日=块 businessDate 兜底 stem）。

    存档无 matchId 只有编号，而 strength/dc 链键=matchId——不 join 则同场九步裂成两个文件，
    完整性检查永不全（2026-10-11 整体冒烟实证）。
    """
    p = Path(archive_path_or_day)
    if not p.exists():
        p = ROOT / "engine" / "cache" / "score_odds" / f"{archive_path_or_day}.json"
        if not p.exists():
            print(f"[evidence] S1 源不存在: {archive_path_or_day}")
            return 0
    day = p.stem
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:              # noqa: BLE001
        print(f"[evidence] S1 存档解析失败 {exc}")
        return 0
    mid_map = _matchid_map()
    n = 0
    for blk in doc.get("matchDays") or []:
        # 开赛日=块 businessDate：存档一日文件常含多日比赛（如 10-10 存档含 10-10/11/12 三块），
        # stem=抓取日只能兜底——键口径铁律=物理比赛日（evidence.py 文件头）
        day = str(blk.get("businessDate") or p.stem)
        for m in blk.get("matches") or []:
            pools = [k for k in POOL_KEYS if m.get(k)]
            code = str(m.get("matchNumStr") or "")
            payload = {"archive": p.name, "fetchedAt": doc.get("fetchedAt"),
                       "code": code or None,
                       "pools": pools, "oddsUpdatedAt": m.get("oddsUpdatedAt"),
                       "sellStatus": m.get("sellStatus")}
            n += record(day, mid_map.get(code, code), STEP_ODDS, payload)
    return n


def _cli() -> None:
    args = sys.argv[1:]
    if len(args) >= 5 and args[0] == "record":       # 会话登记 S8/S9 走此口
        ok = record(args[1], args[2], args[3], json.loads(args[4]), source=SOURCE_SESSION)
        print("[evidence] 会话登记 ✓" if ok else "[evidence] 会话登记 失败(警告)")
    elif len(args) >= 2 and args[0] == "s1":
        print(f"[evidence] S1 登记 {record_s1_from_archive(args[1])} 场")
    elif len(args) >= 2 and args[0] == "check":
        r = check(args[1])
        for k, miss in r.items():
            tag = "齐" if not miss else f"缺 {'/'.join(miss)}"
            print(f"  {k}: {tag}")
        print(f"[evidence] {len(r)} 场检查完毕（缺步标注不补造）")
    elif len(args) >= 3 and args[0] == "show":
        show(args[1], args[2])
    else:
        print("用法: evidence.py record <日> <键> <步> '<json>' | s1 <存档日> | "
              "check <日> | show <日> <键>")


if __name__ == "__main__":
    _cli()
