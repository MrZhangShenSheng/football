# engine/scripts/attribution_dims.py
"""比分归因维度计算器（纯函数·同输入同输出）。设计 §二。开发者 sszhang"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from market_lambda import had_devig, solve_lamh

def lambda_from_had(odds: list[float], lam_sum: float) -> tuple[float, float] | None:
    """L1 基线：HAD 三向去水→p_h→solve_lamh 反解 (λh, λa)。开发者 sszhang"""
    if lam_sum is None or lam_sum <= 0.5:
        return None
    p_h, _, _ = had_devig(*odds)
    if not 0.05 < p_h < 0.95:
        return None
    lam_h = solve_lamh(lam_sum, p_h)
    return round(lam_h, 3), round(lam_sum - lam_h, 3)


# attribution_dims.py 追加
ELO_SCALE = 400.0  # Elo 差 400≈净胜 1 球（经验先验·待频次表校准·设计§二A1）

def dim_elo(elo_h, elo_a) -> dict | None:
    """A1 实力差→净胜偏移（对半分给 h/a）。缺 Elo 返回 None 进 missing。开发者 sszhang"""
    if elo_h is None or elo_a is None:
        return None
    net = (elo_h - elo_a) / ELO_SCALE
    return {"dim": "A1.elo", "h": round(net / 2, 3), "a": round(-net / 2, 3),
            "evidence": f"elo_diff={round(elo_h - elo_a)}"}


# attribution_dims.py 追加（Task 4：L0 赛制档案+dim_stage）
import json

# brief 原稿 parents[1] 指向 engine/data/00-leagues（不存在）必落空，按仓库实际结构
# 改 parents[2]=repo 根（与 score_attribution.py 的 ROOT=SCRIPTS_DIR.parents[1] 同层级约定）
FORMATS_PATH = Path(__file__).resolve().parents[2] / "data" / "00-leagues" / "_formats.json"
FIFA_VIRUS_DAYS = 5  # 窗口结束后 N 天内的联赛场算 FIFA 病毒（先验·待频次表校准）

def _formats() -> dict:
    return json.loads(FORMATS_PATH.read_text(encoding="utf-8"))

def _in_fifa_window_tail(date_str: str) -> bool:
    for win in _formats().get("_fifa_windows_2026", []):
        end = win.split("/")[1]
        d0 = datetime.fromisoformat(date_str).date()
        d1 = datetime.fromisoformat(end).date()
        if 0 < (d0 - d1).days <= FIFA_VIRUS_DAYS:
            return True
    return False

def dim_stage(rec: dict) -> dict:
    """L0 赛制情境→形状系数（期1只判 league_regular/fifa_virus；淘汰赛档期4接 C1 细分）。开发者 sszhang"""
    fmt = _formats().get(rec.get("league"))
    if fmt is None:
        return {"dim": "L0.stage", "tail_scale": 1.0, "evidence": "no_format"}
    ev = "league_regular" if fmt.get("type", "league").startswith("league") else fmt["type"]
    if _in_fifa_window_tail(rec["date"]):
        ev += "+fifa_virus"
    return {"dim": "L0.stage", "tail_scale": 1.0, "evidence": ev}


# attribution_dims.py 追加（Task 5：德比/宿敌标记维度）
DERBY_TAIL = 1.4  # 德比尾格系数（先验·待频次表校准·设计§二A档3）
# 同 Task4 FORMATS_PATH 层级约定：brief 原稿 parents[1] 指 engine/data（不存在），按仓库实际结构 parents[2]=repo 根
DERBIES_PATH = Path(__file__).resolve().parents[2] / "data" / "00-leagues" / "_derbies.json"
# _derbies.json 分组键=国家级（england/italy/spain/germany），调用方（Task7 账单/T5 测试）传中文联赛名
# rec["league"]——brief 原稿 table.get(league) 对 "英超" 必落空（其自带测试与 JSON 键不一致），补归一映射兜住两种口径
DERBY_LEAGUE_KEYS = {"英超": "england", "意甲": "italy", "西甲": "spain", "德甲": "germany"}

def dim_derby(league: str, team_h: str, team_a: str) -> dict | None:
    """德比/宿敌标记→形状系数。开发者 sszhang"""
    if not team_h or not team_a:
        return None
    table = json.loads(DERBIES_PATH.read_text(encoding="utf-8"))
    pairs = table.get(DERBY_LEAGUE_KEYS.get(league, league), [])
    for a, b in pairs:
        if {team_h, team_a} == {a, b}:
            return {"dim": "derby", "tail_scale": DERBY_TAIL,
                    "evidence": f"derby:{a}/{b}"}
    return None


# attribution_dims.py 追加（Task 6：E2 半场结构+R1 粗档+L5 剧本）
HTFT_SURGE_PER_GOAL = 0.35  # 下半场净增每球的偏移贡献（先验·待频次表校准）

def dim_htft(rec: dict) -> dict | None:
    """E2 半场→全场结构：下半场净增球的归属偏移。缺半场比分返回 None。开发者 sszhang"""
    r = rec.get("result") or {}
    if r.get("ht_hg") is None or r.get("hg") is None:
        return None
    s2_h = r["hg"] - r["ht_hg"]
    s2_a = r["ag"] - r["ht_ag"]
    net = s2_h - s2_a
    half = net * HTFT_SURGE_PER_GOAL / 2
    return {"dim": "E2.htft", "h": round(half, 3), "a": round(-half, 3),
            "evidence": f"半场{r['ht_hg']}:{r['ht_ag']}→全场{r['hg']}:{r['ag']}"}

def dim_injury_coarse(rec: dict) -> dict | None:
    """R1 粗档（期1标记不量化；LLM 轨量化在期3）。人数差≥2 才标记。开发者 sszhang"""
    nh, na = rec.get("inj_n_h", 0), rec.get("inj_n_a", 0)
    if abs(nh - na) < 2:
        return None
    return {"dim": "R1.coarse", "h": None, "a": None,
            "evidence": f"伤停人数差 h{nh}/a{na}(未量化)"}

def classify_script(rec: dict) -> dict:
    """L5 剧本规则定档（期1可判6/8：红牌/门将/默契待期2画面数据）。开发者 sszhang"""
    r = rec.get("result") or {}
    hg, ag = r.get("hg"), r.get("ag")
    if hg is None:
        return {"type": "unclassified", "reason": "no_result"}
    hth, hta = r.get("ht_hg"), r.get("ht_ag")
    net, tot = hg - ag, hg + ag
    if net >= 3:
        if hth is not None and (hth - hta) >= 1:
            return {"type": "crush", "reason": f"净胜{net}·半场已领先{hth}:{hta}"}
        return {"type": "early_crush", "reason": f"净胜{net}·半场{hth}:{hta}"}
    if net == 0 and tot <= 1:
        return {"type": "iron_grid", "reason": f"总球{tot}互磨"}
    if tot >= 5:
        return {"type": "leaky", "reason": f"总球{tot}互漏"}
    if hth is not None:
        s2_net = (hg - hth) - (ag - hta)
        if hth != hta and s2_net * (hth - hta) > 0 and abs(s2_net) >= 2:
            return {"type": "press_counter", "reason": "半场领先方下半场继续扩大=压上反杀族"}
        if abs(net) <= 1 and s2_net != 0:
            return {"type": "open_play", "reason": "开放对攻有来有回"}
    if tot <= 1:
        return {"type": "early_goal_lock", "reason": f"总球{tot}"}
    return {"type": "open_play", "reason": "默认开放"}
