#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全量赛果校准表（outcomes）：未经选择的客观赛果样本库 + 概率带×赛制类型校准。

**与 corpus 的分工（2026-09-28 大哥审计拍板，禁止合并）**：
- `corpus.json`  = agent **选过的腿**（274 条）→ 回答"我的判断准不准"（含选股偏差：
  低概率腿只在特别有信心时才入方案，故命中率虚高 +28.7pp，此偏差是真实信号不可稀释）
- 本表 outcomes  = **全量场次赛果**（未经选择）→ 回答"该概率带客观开出什么"
两表混合会把选股偏差与市场校准偏差搅在一起，从此谁也说不清偏差是真是假——故分表。

数据源（均为"未经 agent 选择"的全量场次）：
- data/07-sfc/{期次}.json  传统足彩 14 场固定盘口（含三向赔率 + 赛果 + 排名）

**样本源现状（2026-09-28 实测，勿再重复踩）**：
- 澳客 okooo：**已上阿里系风控**（405 反爬页，`data-spm` 标记）。26127/26128 隔离重试
  25s/40s 退避仍 405，26129/26130 偶然穿过——非频率限流，是站点级拦截。
  故 `sfc_fetch.py backfill` 批量回填历史期次**不再可行**，只能靠每期在售时增量抓。
- 体彩官方 `getHistoryPageListV1.qry`：接口通但 gameNo=1002/1003 返空（传足 gameNo 未探通）。
- `data/02-results/league/*_matches.json`：本地 20403 场赛果，但**无赔率**（DC 拟合只需
  进球数）——本表核心是"概率带→客观命中"，无赔率无法定带，故不可用。
- fd CSV（band_calibration 走此路）：带赔率、覆盖八联赛四季，但**全为联赛，无国家队/杯赛**。

**⚠ 由此得出的真实结论**：`national_cup` 格（主公最关心的"德国爆冷"那类）**现阶段无批量
数据源**，只能靠传足每期在售增量积累（14 场/期，约 4 期/月）。按每格 n≥100 闸门，
该维度约需 **2 年** 才够进参数链——此为诚实估算，不得以"样本会慢慢攒起来"含糊带过。
可提示籍（boldplay 卡面）不受此限，现在即可用。

维度：概率带（5 档）× 赛制类型（国家队/杯赛 vs 联赛）= 10 格
升籍闸门：**每格** n≥100 且 RPS bootstrap CI 下界<0 才准进参数链（lessons.md 籍别声明）

用法：
  python outcomes.py build          # 扫 07-sfc 全量 → data/08-outcomes/outcomes.json
  python outcomes.py calib          # 出概率带×赛制 校准表 → outcomes_calibration.json
  python outcomes.py                # build + calib

落盘：data/08-outcomes/。开发者 sszhang
"""
import json
import random
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from common import log, ROOT

SFC_DIR = ROOT / "data" / "07-sfc"
OUT_DIR = ROOT / "data" / "08-outcomes"
OUTCOMES = OUT_DIR / "outcomes.json"
CALIB = OUT_DIR / "outcomes_calibration.json"

# 赛制类型判别：国家队/杯赛 = DC 无参数的纯市场锚场景（本系统最薄维度）
NATIONAL_CUP_KEYS = (
    "国际赛", "欧国联", "世预", "美金杯", "亚洲杯", "非洲杯", "欧洲杯", "世界杯",
    "亚运", "奥运", "友谊",
    "欧冠", "欧罗巴", "欧协联", "解放者", "南美杯", "亚冠",
    "德国杯", "意大利杯", "法国杯", "西班牙国王杯", "英足总杯", "英联赛杯", "英锦标赛",
    "巴西杯", "超级杯", "超杯", "杯赛",
)

BANDS = (
    ("<0.15", 0.0, 0.15),
    ("0.15-0.30", 0.15, 0.30),
    ("0.30-0.45", 0.30, 0.45),
    ("0.45-0.60", 0.45, 0.60),
    (">=0.60", 0.60, 1.01),
)

GRID_MIN_N = 100          # 升籍闸门：每格样本下限（lessons.md 籍别声明）
BOOTSTRAP_N = 1000


def regime_of(league: str) -> str:
    """赛制类型：national_cup（国家队/杯赛，纯市场锚）vs league（联赛）。"""
    lg = str(league or "")
    return "national_cup" if any(k in lg for k in NATIONAL_CUP_KEYS) else "league"


def devig(odds: list) -> list | None:
    """三向去水归一（power 法过重，此处用基础倒数归一——与 Step 2 口径一致）。"""
    try:
        inv = [1.0 / float(o) for o in odds]
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    s = sum(inv)
    if s <= 0:
        return None
    return [x / s for x in inv]


def band_of(p: float) -> str:
    for nm, lo, hi in BANDS:
        if lo <= p < hi:
            return nm
    return ">=0.60"


def build() -> dict:
    """扫 07-sfc 全量期次 → 逐场三向样本（每场产 3 条：主/平/客各一条带样本）。"""
    rows = []
    issues = []
    for p in sorted(SFC_DIR.glob("*.json")):
        if p.stem.startswith("_") or "-" in p.stem:
            continue          # 跳过 _index / {期}-prediction / {期}-ren9-plan 等衍生文件
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            log("outcomes", f"跳过 {p.name}: {e}")
            continue
        issue = data.get("issue")
        n_ok = 0
        for m in data.get("matches") or []:
            res = m.get("result")
            if res in (None, "", "?"):
                continue      # 未开赛/无赛果
            try:
                res = int(res)
            except (TypeError, ValueError):
                continue
            if res not in (0, 1, 3):
                continue      # 传足口径：3=主胜 1=平 0=客胜
            pm = devig(m.get("odds") or [])
            if pm is None:
                continue
            regime = regime_of(m.get("league"))
            # 三向各产一条：该选项的市场概率 vs 是否开出
            for idx, sel in ((0, 3), (1, 1), (2, 0)):
                rows.append({
                    "issue": issue,
                    "no": m.get("no"),
                    "league": m.get("league"),
                    "regime": regime,
                    "match": f"{m.get('home')} vs {m.get('away')}",
                    "kickoff": m.get("kickoff"),
                    "sel": {3: "h", 1: "d", 0: "a"}[sel],
                    "p_mkt": round(pm[idx], 4),
                    "odds": m.get("odds")[idx] if m.get("odds") else None,
                    "hit": bool(res == sel),
                    "score": m.get("score"),
                    "source": "sfc",
                })
            n_ok += 1
        if n_ok:
            issues.append({"issue": issue, "matches": n_ok})
    out = {
        "generatedAt": date.today().isoformat(),
        "note": "全量场次客观赛果样本（未经 agent 选择）——与 corpus.json 分表，禁止合并（见 outcomes.py docstring）",
        "n_rows": len(rows),
        "n_matches": sum(i["matches"] for i in issues),
        "issues": issues,
        "rows": rows,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTCOMES.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    log("outcomes", f"build → {OUTCOMES.relative_to(ROOT)}: {len(rows)} 条样本 / {out['n_matches']} 场 / {len(issues)} 期")
    return out


def _boot_ci(vals: list, n_boot: int = BOOTSTRAP_N, seed: int = 42) -> list:
    """命中率 - 预测均值 的差值 95% bootstrap CI（正=实测优于市场定价）。"""
    if not vals:
        return [None, None]
    rnd = random.Random(seed)
    diffs = []
    k = len(vals)
    for _ in range(n_boot):
        smp = [vals[rnd.randrange(k)] for _ in range(k)]
        hit = sum(1 for h, _ in smp if h) / k
        pm = sum(p for _, p in smp) / k
        diffs.append(hit - pm)
    diffs.sort()
    return [round(diffs[int(0.025 * n_boot)], 4), round(diffs[int(0.975 * n_boot)], 4)]


def calib(data: dict | None = None) -> dict:
    """概率带 × 赛制类型 校准表（10 格）+ 每格升籍闸门判定。"""
    if data is None:
        data = json.loads(OUTCOMES.read_text(encoding="utf-8"))
    rows = data["rows"]
    grid = {}
    for regime in ("national_cup", "league"):
        for nm, lo, hi in BANDS:
            sel = [(r["hit"], r["p_mkt"]) for r in rows
                   if r["regime"] == regime and lo <= r["p_mkt"] < hi]
            if not sel:
                grid[f"{regime}|{nm}"] = {"n": 0, "gateReady": False, "verdict": "无样本"}
                continue
            n = len(sel)
            hit = sum(1 for h, _ in sel if h) / n
            pm = sum(p for _, p in sel) / n
            ci = _boot_ci(sel)
            ready = n >= GRID_MIN_N
            if not ready:
                verdict = f"样本不足（n={n}<{GRID_MIN_N}）→ 维持提示籍，不得进参数链"
            elif ci[0] is not None and ci[0] > 0:
                verdict = "市场系统性低估该带（CI 下界>0）→ 可议升籍"
            elif ci[1] is not None and ci[1] < 0:
                verdict = "市场系统性高估该带（CI 上界<0）→ 可议升籍"
            else:
                verdict = "CI 跨 0 无显著偏差 → 判定不可归纳，永久留 lessons.md"
            grid[f"{regime}|{nm}"] = {
                "n": n,
                "hitRate": round(hit, 4),
                "pMktMean": round(pm, 4),
                "devPp": round((hit - pm) * 100, 1),
                "ci95": ci,
                "gateReady": ready,
                "verdict": verdict,
            }
    out = {
        "generatedAt": date.today().isoformat(),
        "source": f"outcomes.json（{data['n_matches']} 场 / {data['n_rows']} 条三向样本）",
        "gridMinN": GRID_MIN_N,
        "gateNote": "闸门算在【格】上不算总数：10 格需总样本 1000+ 才可能每格过 100（lessons.md 籍别声明）",
        "grid": grid,
    }
    CALIB.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    log("outcomes", f"calib → {CALIB.relative_to(ROOT)}")
    ready = sum(1 for v in grid.values() if v.get("gateReady"))
    log("outcomes", f"10 格中 {ready} 格过 n≥{GRID_MIN_N} 闸门")
    return out


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd in ("build", "all"):
        data = build()
        if cmd == "all":
            calib(data)
    elif cmd == "calib":
        calib()
    else:
        log("outcomes", f"未知子命令 {cmd!r}；用法: build | calib | (空=全跑)")


if __name__ == "__main__":
    main()
