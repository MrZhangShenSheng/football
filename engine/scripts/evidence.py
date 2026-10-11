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


def norm_key(match_id=None, code=None, league=None, home=None, away=None) -> str:
    """matchKey 优先级：matchId > 体彩编号 > 联赛|主|客（禁 ':'，Windows 双机兼容）。"""
    if match_id:
        return str(match_id)
    if code:
        return str(code)
    return f"{league}|{home}|{away}"


def record(match_day: str, match_key: str, step: str, payload: dict,
           source: str = SOURCE_CODE) -> bool:
    """幂等落盘一条步骤记录。fail-open：任何异常打印警告返回 False，绝不抛出。"""
    try:
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
