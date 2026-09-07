#!/usr/bin/env python3
"""shapes.py — 影子层票型引擎（2026-09-07 重建版 v2）。

原版随 scratch/replay_v2 清理误删（未进 git），本文件按两份事实源重建：
①设计规格 docs/superpowers/plans/2026-09-05-complex-ticket-test.md Task 4；
②回放实况报告 data/04-summaries/2026-09-05-complex-test-interim.html /
  -largesample.html 的 19 spec 家族清单与等成本数字（4串11=22 / 8串1×15=30 /
  全2关-4=24 / 全2关-6=30 / 4串1+2双选=24 / ev-8串9=18 / 胶着系 18~48）。
重建口径声明（与旧版的已知差异，不冒充逐位一致）：
- 等成本规则统一 cost = n_bets × BET_UNIT × mult，mult = max(1, BUDGET//(n_bets×BET_UNIT))
  ——上述全部 spec 成本可由此复算对上，视为忠实还原；
- 选腿池：strong=市场去水主概率降序（影子层 legs.fused 为体彩去水口径·spec#15）；
  jiao_zhuo=三向极差升序；ev_best=主选项 EV 降序；ttg/crs=q 快照驱动（无 q 整族跳过）；
- 结算键格式（本版定义）：had pick=int(0/1/2) 直索引 odds 三槽 list；
  ttg pick='s0'~'s7'（体彩池键，s7=7+球）；crs pick='h:a' 精确键或'胜其他/平其他/负其他'
  （其他键按方向匹配，高比分语义近似）；
- qcache 引擎（freq_snap）未重建：qcache/{date}.json 缺失 → load_qcache 返回 None →
  ttg/crs spec build_ticket 返回 None 跳过（诚实降级，未来重建生成器后自动恢复）。
开发者 sszhang
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
QCACHE_DIR = os.path.join(HERE, 'qcache')

BET_UNIT = 2      # 单注 2 元（与实票账本/narrative.SHADOW_BET_UNIT 同源）
BUDGET = 30       # 等成本预算上限（mult=⌊30/(n_bets*2)⌋ 至少 1）


# ── 方案族（19 spec · 家族/池/形状/腿数/选项规则对齐回放报告）──
PLAN_FAMILIES = [
    # A 族：HAD 方向（strong=市场去水强腿 / jiao_zhuo=胶着池）
    {'family': 'A', 'name': '8串1',        'pool': 'strong',   'shape': '8串1',  'n_legs': 8, 'options': 'single', 'track': 'A'},
    {'family': 'A', 'name': '4串1',        'pool': 'strong',   'shape': '4串1',  'n_legs': 4, 'options': 'single', 'track': 'A'},
    {'family': 'A', 'name': '4串11',       'pool': 'strong',   'shape': '4串11', 'n_legs': 4, 'options': 'single', 'track': 'A'},
    {'family': 'A', 'name': '4串1+2双选',  'pool': 'strong',   'shape': '4串1',  'n_legs': 4, 'options': 'dual2', 'track': 'A'},
    {'family': 'A', 'name': '全2关-6',     'pool': 'strong',   'shape': '全2关', 'n_legs': 6, 'options': 'single', 'track': 'A'},
    {'family': 'A', 'name': '全2关-4',     'pool': 'strong',   'shape': '全2关', 'n_legs': 4, 'options': 'single', 'track': 'A'},
    {'family': 'A', 'name': '胶着-三选2串1', 'pool': 'jiao_zhuo', 'shape': '2串1', 'n_legs': 2, 'options': 'triple', 'track': 'A'},
    {'family': 'A', 'name': '胶着-双选全2关', 'pool': 'jiao_zhuo', 'shape': '全2关', 'n_legs': 4, 'options': 'dual', 'track': 'A'},
    {'family': 'A', 'name': '胶着-双选单关', 'pool': 'jiao_zhuo', 'shape': '单关', 'n_legs': 7, 'options': 'dual', 'track': 'A'},
    # B 族：EV 最优池（同 A 形状）
    {'family': 'B', 'name': 'ev-8串1',     'pool': 'ev_best',  'shape': '8串1',  'n_legs': 8, 'options': 'single', 'track': 'A'},
    {'family': 'B', 'name': 'ev-4串11',    'pool': 'ev_best',  'shape': '4串11', 'n_legs': 4, 'options': 'single', 'track': 'A'},
    {'family': 'B', 'name': 'ev-8串9',     'pool': 'ev_best',  'shape': '8串9',  'n_legs': 8, 'options': 'single', 'track': 'A'},
    {'family': 'B', 'name': 'ev-双选全2关-4', 'pool': 'ev_best', 'shape': '全2关', 'n_legs': 4, 'options': 'dual', 'track': 'A'},
    # C 族：TTG（q 快照驱动 · 无 q 跳过）
    {'family': 'C', 'name': 'ttg-4串1',    'pool': 'ttg',      'shape': '4串1',  'n_legs': 4, 'options': 'single', 'track': 'A'},
    {'family': 'C', 'name': 'ttg-双选2串1', 'pool': 'ttg',     'shape': '全2关', 'n_legs': 3, 'options': 'dual', 'track': 'A'},
    {'family': 'C', 'name': 'ttg-单关',    'pool': 'ttg',      'shape': '单关',  'n_legs': 3, 'options': 'single', 'track': 'A'},
    # D 族：CRS（q 快照驱动 · 无 q 跳过）
    {'family': 'D', 'name': 'crs-4串1',    'pool': 'crs',      'shape': '4串1',  'n_legs': 4, 'options': 'single', 'track': 'A'},
    {'family': 'D', 'name': 'crs-双选2串1', 'pool': 'crs',     'shape': '全2关', 'n_legs': 3, 'options': 'dual', 'track': 'A'},
    {'family': 'D', 'name': 'crs-双选单关', 'pool': 'crs',     'shape': '单关',  'n_legs': 7, 'options': 'dual', 'track': 'A'},
]


def load_qcache(date: str):
    """当日 freq q 快照 → dict；qcache/{date}.json 缺失返回 None（ttg/crs 族诚实跳过）。
    生成器 freq_snap 未随本版重建——旧快照随事故丢失，重建生成器后本函数自动生效。"""
    p = os.path.join(QCACHE_DIR, f'{date}.json')
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


# ── 选腿（池排序 → 前 n 腿）──
def _key_strong(leg):
    return max(leg['fused'])


def _key_jiao(leg):
    f = sorted(leg['fused'])
    return f[-1] - f[0]          # 三向极差升序 = 最胶着优先


def _key_ev(leg):
    i = leg['fused'].index(max(leg['fused']))
    return leg['fused'][i] * leg['odds'][i] - 1.0   # 主选项 EV


def _pick_legs(legs, spec):
    """池选腿 → [(原索引, leg)]（tleg.src 需指向入参 legs 下标）；池不足最小需求 → None
    （串关≥2、单关≥1；胶着池腿<N 用全部可用降级）。"""
    keyed = list(enumerate(legs))
    if spec['pool'] == 'strong':
        pool = sorted(keyed, key=lambda kv: _key_strong(kv[1]), reverse=True)
    elif spec['pool'] == 'jiao_zhuo':
        pool = sorted(keyed, key=lambda kv: _key_jiao(kv[1]))
    elif spec['pool'] == 'ev_best':
        pool = sorted(keyed, key=lambda kv: _key_ev(kv[1]), reverse=True)
    else:
        return None               # ttg/crs 选腿由 build_ticket 的 q 分支处理
    n = spec['n_legs']
    min_n = 1 if spec['shape'] == '单关' else 2
    if len(pool) < min_n:
        return None
    return pool[:n] if len(pool) >= n else pool   # 胶着降级：不足 N 用全部可用（spec 变更#6）


# ── 选项（had：概率序三向；ttg/crs：q 快照 top-k）──
def _had_options(leg, k):
    # 并列概率取槽位小者（主>平>客）——sorted(reverse=True) 对并列元素会倒置槽序
    order = sorted(range(3), key=lambda i: (-leg['fused'][i], i))
    return order[:k]


def _qc_options(leg, kind, qc, k):
    """q 快照 → 该场 kind 池 top-k 选项。qc 结构：{code: {kind: [(pick, q), ...] 降序}}。"""
    if not qc:
        return None
    ranked = (qc.get(leg.get('code')) or {}).get(kind) or []
    if not ranked:
        return None
    return [p for p, _ in ranked[:k]]


def _leg_picks(leg, spec, options_mode, qc):
    """→ 该腿选项 list（建票展开用）；had 腿 int 选项，ttg/crs 池键 str。"""
    kind = 'had' if spec['pool'] in ('strong', 'jiao_zhuo', 'ev_best') else spec['pool']
    if kind == 'had':
        k = {'single': 1, 'dual': 2, 'dual2': 2, 'triple': 3}[options_mode]
        return _had_options(leg, k)
    picks = _qc_options(leg, kind, qc, 2 if options_mode == 'dual' else 1)
    if picks is None and options_mode == 'dual':   # q 缺该场时降单选（诚实降级不静默弃票）
        picks = _qc_options(leg, kind, qc, 1)
    return picks


def _skeleton(shape, n):
    """形状骨架 → tleg 索引组合 list（选项组合在 bets 层展开）。"""
    from itertools import combinations
    if shape in ('8串1', '4串1', '2串1'):
        return [tuple(range(n))] if n >= 2 else None
    if shape == '4串11':
        return [c for size in (2, 3, 4) for c in combinations(range(n), size)] if n >= 2 else None
    if shape == '8串9':
        return [c for c in combinations(range(n), n - 1)] + [tuple(range(n))] if n >= 3 else None
    if shape == '全2关':
        return [c for c in combinations(range(n), 2)] if n >= 2 else None
    if shape == '单关':
        return [(i,) for i in range(n)]
    return None


def build_ticket(legs, spec, qc=None):
    """legs（shadow_all 口径：code/match/fused/odds/pools）+ PlanSpec → 票 or None。

    票结构（paper.py freeze_legs/_settle_view 消费面）：
    tlegs=[{src, kind, picks, odds}]（odds=冻结价，had 三槽 list/池键 dict——未买槽/键缺
    即 miss，odds 结构本身编码买了什么）；bets=[{legs: [(tleg_idx, pick), ...]}]（选项组合
    逐注展开，n_bets=len(bets)）；mult=max(1, BUDGET//(n_bets*BET_UNIT))；cost=乘积。
    """
    if spec['pool'] in ('ttg', 'crs'):
        if not qc:
            return None                     # 无 q 快照整族跳过（qcache 随事故丢失待重建）
        pool = sorted(enumerate(legs), key=lambda kv: _key_strong(kv[1]),
                      reverse=True)[:spec['n_legs']]
        min_n = 1 if spec['shape'] == '单关' else 2
        if len(pool) < min_n:
            return None
    else:
        pool = _pick_legs(legs, spec)
        if not pool:
            return None
    n = len(pool)
    tlegs, tleg_picks = [], []
    for i, (src_idx, leg) in enumerate(pool):
        mode = spec['options']
        if mode == 'dual2':                 # 4串1+2双选：前 2 腿单选、其余双选
            mode = 'single' if i < 2 else 'dual'
        picks = _leg_picks(leg, spec, mode, qc)
        if not picks:
            return None                     # ttg/crs 池价/q 全缺该场 → 整票放弃（不建半票）
        kind = 'had' if spec['pool'] in ('strong', 'jiao_zhuo', 'ev_best') else spec['pool']
        if kind == 'had':
            picks = [int(p) for p in picks]
            # 只冻结已买槽（未买槽 None——settle 以槽位有价判"买了且命中"，全槽透传会误判）
            odds = [leg['odds'][p] if p in picks else None for p in range(3)]
        else:
            pool_odds = (leg.get('pools') or {}).get(kind) or {}
            odds = {str(k): float(v) for k, v in pool_odds.items()}
        tlegs.append({'src': src_idx, 'kind': kind, 'picks': picks, 'odds': odds})
        tleg_picks.append(picks)
    sk = _skeleton(spec['shape'], n)
    if not sk:
        return None
    bets = []
    for combo in sk:                        # 骨架 × 各腿选项笛卡尔积 → 逐选项组合一注
        from itertools import product
        for picks_combo in product(*[tleg_picks[i] for i in combo]):
            bets.append({'legs': [(i, p) for i, p in zip(combo, picks_combo)]})
    n_bets = len(bets)
    mult = max(1, BUDGET // (n_bets * BET_UNIT))
    return {'tlegs': tlegs, 'bets': bets, 'n_bets': n_bets,
            'mult': mult, 'cost': n_bets * BET_UNIT * mult}


# ── 结算（odds 结构编码买什么：命中即取价，未买=灭）──
def _pick_price(tleg, pick, outcome, score):
    """→ 命中价 or None（未买/未命中）。"""
    kind, odds = tleg['kind'], tleg['odds']
    if kind == 'had':
        return odds[pick] if pick == outcome else None
    if score is None:
        return None
    h, a = score
    if kind == 'ttg':
        total = h + a
        key = f's{min(total, 7)}'
        return odds.get(pick) if pick == key else None
    # crs：精确键 或 方向"其他"键（高比分近似语义）
    if pick in ('胜其他', '平其他', '负其他'):
        want = {'胜其他': 0, '平其他': 1, '负其他': 2}[pick]
        return odds.get(pick) if outcome == want else None
    return odds.get(pick) if pick == f'{h}:{a}' else None


def settle(legs, ticket) -> float:
    """真实赛果 → 回款。legs=[{code, outcome, score}]（score=(h,a) 或 None）；
    ticket={tlegs, bets, mult}（paper._settle_view 口径）。命中注=全部 (腿,选项) 有价，
    回款=Σ BET_UNIT×mult×∏价。"""
    payout = 0.0
    for bet in ticket['bets']:
        odds_prod = 1.0
        for ti, pick in bet['legs']:
            tleg = ticket['tlegs'][ti]
            rec = legs[tleg['src']]
            price = _pick_price(tleg, pick, rec.get('outcome'), rec.get('score'))
            if price is None:
                odds_prod = None
                break
            odds_prod *= price
        if odds_prod is not None:
            payout += BET_UNIT * ticket['mult'] * odds_prod
    return round(payout, 2)
