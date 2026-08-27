"""freq-band 比分选法（2026-08-27 重设计）：联赛频率模板+球队实力平移+形状带+q排序。开发者 sszhang
铁律（docs/2026-08-27-freq-band-design.html）：分布形状来自真实赛果频率（不造分布）；
赔率只做形状带门槛、不做排序；DC 不参与比分选择；数据缺失零破坏降级（纯联赛模板）。"""
import glob, json, math
from collections import Counter, defaultdict
from pathlib import Path
from band_calibration import DIVS, SEASONS, fetch_rows
from score_ev import map_league

RECENT_WINDOW = 10          # 球队近况窗口（场）
FORM_MIN_MATCHES = 5        # 近况最少场数：不足 → 不平移（纯联赛模板降级）
SURVIVE_Q = 0.01            # 生存阈值：平移后 q < 1% 的比分视为噪声
LAMBDA_CLAMP = (0.2, 4.5)   # λ 合理域（防小样本狂胜拉爆）
SIGMA_FLOOR = 0.5           # 核带宽下限（防极端单点分布 σ→0）
LEAGUE_STORE = ("japan", "korea", "sweden", "saudi")   # 本地赛果库联赛（与 score_ev 口径一致）


def _norm(name: str) -> str:
    """队名宽松匹配键（与 boldplay._dc_params 同口径）：小写去连字符去空格。"""
    return str(name).lower().replace("-", "").replace(" ", "")


def league_base_rates(blob: Counter) -> tuple:
    """频率 Counter → (主场场均进球, 客场场均进球)：Σh·c/n 与 Σa·c/n。"""
    n = blob.get("__n", 0)
    if not n:
        return 0.0, 0.0
    gh = sum(int(s.split(":")[0]) * c for s, c in blob.items() if s != "__n")
    ga = sum(int(s.split(":")[1]) * c for s, c in blob.items() if s != "__n")
    return gh / n, ga / n


def global_pool(freq_table: dict) -> Counter:
    """全联赛汇总 Counter（欧冠/巴甲等无映射联赛的模板池）。"""
    g = Counter()
    for blob in freq_table.values():
        g.update(blob)
    return g
