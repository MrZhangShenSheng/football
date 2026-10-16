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
- qcache 快照由 freq_snap.py 生成（2026-09-08 重建，同 boldplay 平移链口径）：
  shadow_all 发现缺失时自动生成；生成失败/当日无场次 → ttg/crs spec 诚实跳过。
开发者 sszhang
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
QCACHE_DIR = os.path.join(HERE, 'qcache')

BET_UNIT = 2      # 单注 2 元（与实票账本/narrative.SHADOW_BET_UNIT 同源）
_HHAD_RE = re.compile(r'^([hda])([+-]\d+)$')      # 让球腿 pick：方向 + 主队让球数
BUDGET = 30       # 等成本预算上限（mult=⌊30/(n_bets*2)⌋ 至少 1）


# ── 方案族（12 spec · 2026-09-29 清理：删 B 族全部+A 族胶着池，证据=市场充分定价+博冷门证伪）──
PLAN_FAMILIES = [
    # A 族：HAD 方向（strong=市场去水强腿·保留作市场基线对照）
    {'family': 'A', 'name': '8串1',        'pool': 'strong',   'shape': '8串1',  'n_legs': 8, 'options': 'single', 'track': 'A'},
    {'family': 'A', 'name': '4串1',        'pool': 'strong',   'shape': '4串1',  'n_legs': 4, 'options': 'single', 'track': 'A'},
    {'family': 'A', 'name': '4串11',       'pool': 'strong',   'shape': '4串11', 'n_legs': 4, 'options': 'single', 'track': 'A'},
    {'family': 'A', 'name': '4串1+2双选',  'pool': 'strong',   'shape': '4串1',  'n_legs': 4, 'options': 'dual2', 'track': 'A'},
    {'family': 'A', 'name': '全2关-6',     'pool': 'strong',   'shape': '全2关', 'n_legs': 6, 'options': 'single', 'track': 'A'},
    {'family': 'A', 'name': '全2关-4',     'pool': 'strong',   'shape': '全2关', 'n_legs': 4, 'options': 'single', 'track': 'A'},
    # B 族（ev_best）2026-09-29 删除：EV = p×o - 1 ≈ 概率排序（市场充分定价），与 A 族冗余
    # A 族胶着池 2026-09-29 删除：三轮证伪了顺序/逆序/胶着，博冷门逻辑无 alpha
    # C 族：TTG（q 快照驱动 · 无 q 跳过）
    {'family': 'C', 'name': 'ttg-4串1',    'pool': 'ttg',      'shape': '4串1',  'n_legs': 4, 'options': 'single', 'track': 'A'},
    {'family': 'C', 'name': 'ttg-双选2串1', 'pool': 'ttg',     'shape': '全2关', 'n_legs': 3, 'options': 'dual', 'track': 'A'},
    {'family': 'C', 'name': 'ttg-单关',    'pool': 'ttg',      'shape': '单关',  'n_legs': 3, 'options': 'single', 'track': 'A'},
    # D 族：CRS（q 快照驱动 · 无 q 跳过）
    {'family': 'D', 'name': 'crs-4串1',    'pool': 'crs',      'shape': '4串1',  'n_legs': 4, 'options': 'single', 'track': 'A'},
    {'family': 'D', 'name': 'crs-双选2串1', 'pool': 'crs',     'shape': '全2关', 'n_legs': 3, 'options': 'dual', 'track': 'A'},
    {'family': 'D', 'name': 'crs-双选单关', 'pool': 'crs',     'shape': '单关',  'n_legs': 7, 'options': 'dual', 'track': 'A'},
    # D 族变体：剧本分层（2026-10-13 大哥拍板 A'+C' · 预注册 spec）
    # ── 预注册判据（跑前登记，禁事后改口径·铁律 14②/15）──────────────────────────
    # 机制：强主场（HAD 三向最低赔 ≤1.40）的 CRS 双选从平局族切大球族——miss 归因实证
    #   旧 spec 66 腿中 23 腿爆大球全灭（尤文5:0/水晶宫4:0/赫尔辛基6:0 皆 qcache 裸联赛
    #   模板 1:1+1:0，λ 平移对欧战弱旅降级纯模板=强弱信息入口丢失）；票型定位右尾博弈，
    #   强主场就该配右尾剧本。市场 HAD 信号只做场景开关不做选腿排序（与 CRS 融合链族
    #   闸门同性质）。均势场保持原 qcache top2 逻辑（与原版唯一差异=强主场 pick 路由）。
    # 事后重构警示：23 腿中 7~9 腿可被 3:0+4:0 覆盖的模拟是先看赛果再挑口径，只作立项
    #   动机不作收益证据。
    # 判据：50 票结算后对比原版 crs-双选2串1 同期回收率，配对差 CI 下限 >0 才留，
    #   否则关档。中途不改参数（1.40 阈值/3:0+4:0 组合冻结）。开发者 sszhang
    {'family': 'D', 'name': 'crs-双选2串1-剧本分层', 'pool': 'crs', 'shape': '全2关',
     'n_legs': 3, 'options': 'dual', 'track': 'A', 'route': 'big_home'},
    # G 族：右尾贪心（2026-09-09 大哥拍板新增·保留作闯关票对照）——概率预算背包贪心近似：
    # 预算容量 W=-ln(p_budget) 内按密度 d=ln(o)/(-ln(p)) 贪心装入（每损失 1 单位概率
    # 换回最多赔率对数收益）；候选=每场 {HAD 主选项 ∪ CRS top-1 ∪ TTG top-1}，
    # 同场取密度最高（铁律9 同场限一玩法）。关数不预设（2~n_legs 由预算自然决定——
    # 密度腿"概率重量"大 w=-ln(p)，紧预算装 4 条必超，锁关数会强制选低密度腿违背贪心本意）；
    # 装<2 腿关档不硬凑。对照位=A 族 4串1（全中~10%/合赔~5）与 D 族 crs-4串1（全中~万分之一）
    # 之间——右尾显著肥于 A、回款可观测性远好于 D（1% 预算锚）。
    {'family': 'G', 'name': 'g-tail-贪心串1', 'pool': 'tail_greedy', 'shape': 'N串1',
     'n_legs': 4, 'options': 'single', 'track': 'A', 'p_budget': 0.01},
    # V 族：v4b 比分模型影子盲测（2026-09-30 B+C 立项）——选场按快照 top1 信号降序取前 2 场
    # （v4b 文档第五章口径，非市场排序）、每场 CRS top2（pool 内有价比分）→ 2×2=4 注 2串1。
    # 快照源=engine/cache/v4b_snap/{date}.json（predictor_v4b_daily.py 生成，与 qcache 同构）；
    # 定位=样本外盲测对照（+341.4% 皆样本内回测，docs/predictor_v4b.md 收编标注），非生产概率源。
    {'family': 'V', 'name': 'v4b-双选2串1', 'pool': 'v4b', 'shape': '2串1',
     'n_legs': 2, 'options': 'dual', 'track': 'A'},
    # L 族：llm-31格选腿（2026-10-13 spec·docs/2026-10-13-llm-predict-design.html §5）——
    # 会话 LLM 预测冻结分布驱动（engine/cache/llm_preds/{销售日}.json·llm_pred.freeze 产物）：
    # 选场=右尾键 top1 概率降序取前 2、每场 crs top2 双选 → 2×2=4 注 2串1；
    # qc=llm_preds 装载（paper.load_llm_preds）：{code:{crs:[(pick,p),...] 右尾键内降序}}，
    # 右尾键=卡市场层有价键中赔率≥30 ∪ 固定 {0:3,0:4}（与 llm_eval.tail_keys_of 同口径）。
    # ── 预注册（跑前登记·禁事后改口径）────────────────────────────────────────
    # forward only：判据=llm-predict-prereg tailChannel（E_tail 聚合二项·CI 下限>0 且
    # n≥150 才转正）；主判据（31 格配对 log-loss）FAIL 不封锁本通道但封锁整体预测叙事。
    # 冻结纪律同 qcache/v4b：llm_preds 文件在即用，缺失诚实跳过（L 族当日无票，不自动生成）。
    # 开发者 sszhang
    # ⛔ 2026-10-16 大哥拍板停用（v2 淘汰）：实测判断与产出脱钩——tilt 锚定把分布推向市场
    # TVD 相对缩减 53.9%，右尾选腿与模型最看好格交集 0/22（模型 9/11 场首位 1:1 或 2:1，
    # 实选 0:3/3:3/4:1/5:1/4:0）。@30 右尾阈值按定义只取市场认为极不可能的格=规定「只准押
    # 市场认为不会发生的事」（R022）。chain_registry llm-31grid v2 epoch 已收口 to=2026-10-16。
    # 后继轨见 docs/superpowers/plans/2026-10-16-llm-context-autonomy.md（Q1 出票口径待拍板）。
    # 保留条目注释而非删除=口径断点留痕（历史 P492/P493 等票的 spec 可回溯）。
    # {'family': 'L', 'name': 'llm-31格选腿', 'pool': 'llm_crs', 'shape': '2串1',
    #  'n_legs': 2, 'options': 'dual', 'track': 'A'},
]


def load_qcache(date: str):
    """当日 freq q 快照 → dict；qcache/{date}.json 缺失返回 None（ttg/crs 族诚实跳过）。
    快照由 shadow_all 调 freq_snap.snap(date) 自动生成（缺失才写=赛前冻结语义）。"""
    p = os.path.join(QCACHE_DIR, f'{date}.json')
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


V4B_SNAP_DIR = os.path.join(HERE, '..', 'cache', 'v4b_snap')   # engine/cache/v4b_snap


def load_v4b_snap(date: str):
    """v4b 快照 data 部分 → dict（与 qcache 同构 {code:{crs:[(pick,signal),...]}}）；
    缺失/空可算场返回 None（V 族诚实跳过）。快照由 predictor_v4b_daily.py 生成（冻结纪律同 qcache）。"""
    p = os.path.join(V4B_SNAP_DIR, f'{date}.json')
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding='utf-8') as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    data = (doc or {}).get('data') or {}
    return data or None
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
    kind = 'had' if spec['pool'] in ('strong', 'jiao_zhuo', 'ev_best') else ('crs' if spec['pool'] in ('v4b', 'llm_crs') else spec['pool'])
    if kind == 'had':
        k = {'single': 1, 'dual': 2, 'dual2': 2, 'triple': 3}[options_mode]
        return _had_options(leg, k)
    picks = _qc_options(leg, kind, qc, 2 if options_mode == 'dual' else 1)
    if picks is None and options_mode == 'dual':   # q 缺该场时降单选（诚实降级不静默弃票）
        picks = _qc_options(leg, kind, qc, 1)
    if spec.get('route') == 'big_home' and kind == 'crs' and options_mode == 'dual':
        picks = _route_big_home(leg, picks) or picks   # 池价缺键回退原 picks
    return picks


BIG_HOME_ODDS_CAP = 1.40   # 强主场阈值（预注册冻结·2026-10-13）


def _route_big_home(leg, fallback_picks):
    """剧本分层路由（预注册 spec 专用）：HAD 最低赔 ≤1.40 的一方 → CRS 双选切大球族。

    强主=['3:0','4:0'] / 强客=['0:3','0:4']；体彩 CRS 池两键均有价才切换
    （缺价回退 fallback_picks 防静默错算——冻结价只取 picks 内键，缺键=结算 None）。
    非强场返回 None（调用方保持原 qcache top2）。开发者 sszhang"""
    had_o = leg.get('odds') or []
    if len(had_o) != 3 or any(not x or x <= 1.0 for x in had_o):
        return None
    lo = min(had_o)
    if lo > BIG_HOME_ODDS_CAP:
        return None
    side = had_o.index(lo)
    want = ['3:0', '4:0'] if side == 0 else ['0:3', '0:4']
    pool = (leg.get('pools') or {}).get('crs') or {}
    # 体彩池键 s03s00/s04s00（shadow_all 透传原始格式）；两键都有有效价才切换
    keys = ('s03s00', 's04s00') if side == 0 else ('s00s03', 's00s04')
    if all(_is_price(pool.get(k)) for k in keys):
        return want
    return None


def _is_price(v):
    try:
        return float(v) > 1.0
    except (TypeError, ValueError):
        return False


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


def _tail_candidates(legs, qc):
    """右尾贪心候选展开：每场 {HAD 主选项 ∪ CRS top-1 ∪ TTG top-1} → 同场取密度最高。
    概率口径混合（HAD=体彩去水单锚 / CRS·TTG=freq q 模板，与 D/C 族同源）；
    密度 d=ln(o)/(-ln(p))——CRS 高赔腿天然占优，正是右尾贪心要的排序。"""
    import math
    cands = []          # [ {src, kind, pick, p, o, d} ]
    for i, leg in enumerate(legs):
        best = None
        # HAD 主选项
        try:
            k = max(range(3), key=lambda j: leg['fused'][j])
            p, o = leg['fused'][k], leg['odds'][k]
            if p > 0 and o > 1.0:
                best = {'src': i, 'kind': 'had', 'pick': k, 'p': p, 'o': o,
                        'd': math.log(o) / -math.log(p)}
        except (KeyError, IndexError, ValueError):
            pass
        # CRS/TTG top-1（q 快照 + 体彩真实池价）
        for kind in ('crs', 'ttg'):
            ranked = (qc.get(leg.get('code')) or {}).get(kind) or []
            if not ranked:
                continue
            pick, q = ranked[0]
            pool_odds = dict((leg.get('pools') or {}).get(kind) or {})
            if kind == 'crs':                 # 体彩原始键 s01s01 → '1:1'
                pool_odds = {(f"{int(k[1:3])}:{int(k[4:6])}" if k.startswith('s') and k[3] == 's' else k): v
                             for k, v in pool_odds.items()}
            try:
                o = float(pool_odds.get(pick))
            except (TypeError, ValueError):
                continue
            if q <= 0 or o <= 1.0:
                continue
            c = {'src': i, 'kind': kind, 'pick': pick, 'p': q, 'o': o,
                 'd': math.log(o) / -math.log(q)}
            if best is None or c['d'] > best['d']:
                best = c
        if best:
            cands.append(best)
    return cands


def _pick_tail(legs, spec, qc):
    """G 族贪心装包：密度降序装入直到 n_legs 上限/预算耗尽（0-1 背包贪心近似，诚实标注）。
    关数自适应：装≥2 腿即出票（<2 关档不硬凑）；超预算的腿跳过不回填（贪心不做交换优化）。"""
    import math
    cands = sorted(_tail_candidates(legs, qc), key=lambda c: -c['d'])
    W = -math.log(spec['p_budget'])
    used, chosen = 0.0, []
    for c in cands:
        w = -math.log(c['p'])
        if used + w > W or c['src'] in (x['src'] for x in chosen):
            continue
        chosen.append(c)
        used += w
        if len(chosen) >= spec['n_legs']:
            break
    return chosen if len(chosen) >= 2 else None


def build_ticket(legs, spec, qc=None):
    """legs（shadow_all 口径：code/match/fused/odds/pools）+ PlanSpec → 票 or None。

    票结构（paper.py freeze_legs/_settle_view 消费面）：
    tlegs=[{src, kind, picks, odds}]（odds=冻结价，had 三槽 list/池键 dict——未买槽/键缺
    即 miss，odds 结构本身编码买了什么）；bets=[{legs: [(tleg_idx, pick), ...]}]（选项组合
    逐注展开，n_bets=len(bets)）；mult=max(1, BUDGET//(n_bets*BET_UNIT))；cost=乘积。
    """
    if spec['pool'] == 'tail_greedy':         # G 族：贪心装包自构 tlegs（绕过池排序/选项展开）
        if not qc:
            return None
        chosen = _pick_tail(legs, spec, qc)
        if not chosen:
            return None
        tlegs, tleg_picks = [], []
        for c in chosen:
            if c['kind'] == 'had':
                odds = [leg if idx == c['pick'] else None
                        for idx, leg in enumerate(legs[c['src']]['odds'])]
                pick_v = int(c['pick'])
            else:
                pool_odds = dict((legs[c['src']].get('pools') or {}).get(c['kind']) or {})
                if c['kind'] == 'crs':         # 体彩原始键 s01s01 → '1:1'（paper.freeze 同口径）
                    pool_odds = {(f"{int(k[1:3])}:{int(k[4:6])}" if k.startswith('s') and k[3] == 's' else k): v
                                 for k, v in pool_odds.items()}
                odds = {str(c['pick']): float(pool_odds[c['pick']])}
                pick_v = c['pick']
            tlegs.append({'src': c['src'], 'kind': c['kind'], 'picks': [pick_v], 'odds': odds})
            tleg_picks.append([pick_v])
        from itertools import product
        sk = [tuple(range(len(tlegs)))]      # G 族 N串1 自适应关数（2~4 由预算决定）
        bets = [{'legs': [(i, p) for i, p in zip(combo, picks)]}
                for combo in sk for picks in product(*[tleg_picks[i] for i in combo])]
        n_bets = len(bets)
        mult = max(1, BUDGET // (n_bets * BET_UNIT))
        return {'tlegs': tlegs, 'bets': bets, 'n_bets': n_bets,
                'mult': mult, 'cost': n_bets * BET_UNIT * mult}
    if spec['pool'] == 'v4b':
        # V 族：选场按 v4b 快照 top1 信号降序（模型口径，非市场排序）——qc=v4b快照（paper 特供）
        if not qc:
            return None
        by_signal = {}
        for code, pools in qc.items():
            ranked = pools.get('crs') or []
            if ranked:
                by_signal[code] = ranked[0][1]
        idx_of = {l['code']: i for i, l in enumerate(legs)}
        ordered = [idx_of[c] for c in sorted(by_signal, key=by_signal.get, reverse=True)
                   if c in idx_of]
        pool = [(i, legs[i]) for i in ordered[:spec['n_legs']]]
        if len(pool) < 2:
            return None
    elif spec['pool'] == 'llm_crs':
        # L 族（2026-10-13 spec·forward only）：选场按 llm 冻结预测右尾键 top1 概率降序
        # （llm 口径，非市场排序）——qc=llm_preds 装载（paper.load_llm_preds 特供），
        # picks 已是 pick 键，池价冻结/结算复用 v4b 同款 crs 路径（kind 归 'crs'）。
        if not qc:
            return None
        by_top = {}
        for code, pools in qc.items():
            ranked = pools.get('crs') or []
            if ranked:
                by_top[code] = ranked[0][1]
        idx_of = {l['code']: i for i, l in enumerate(legs)}
        ordered = [idx_of[c] for c in sorted(by_top, key=by_top.get, reverse=True)
                   if c in idx_of]
        pool = [(i, legs[i]) for i in ordered[:spec['n_legs']]]
        if len(pool) < 2:
            return None
    elif spec['pool'] in ('ttg', 'crs'):
        if not qc:
            return None                     # 无 q 快照整族跳过（qcache 随事故丢失待重建）
        # 仅在"该池有模型候选"的场次内按 strong 排序选腿：先取 strong 前 N 再查候选的老口径，
        # 遇一场无候选就整票关档——V3W 换代后未映射场成常态（国家队/未收录队），2026-10-13
        # 实证九场中未映射的周一010 恰排 strong 第三，C/D 两族六 spec 整日静默消失。
        # 候选不足 min_n 时仍关档（真·无票可建），口径=诚实跳过不可算场，非降级建半票。
        cands = [(i, l) for i, l in enumerate(legs)
                 if (qc.get(l.get('code')) or {}).get(spec['pool'])]
        pool = sorted(cands, key=lambda kv: _key_strong(kv[1]),
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
        kind = 'had' if spec['pool'] in ('strong', 'jiao_zhuo', 'ev_best') else ('crs' if spec['pool'] in ('v4b', 'llm_crs') else spec['pool'])
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
    if kind == 'hhad':
        # 让球盘：让球结果 =(主队得分 + goalLine) vs 客队得分（体彩口径·主队视角，
        # 与 boldplay._hhad_odds/_leg_hit 同源）。让球线取腿上 goalLine 为准——pick
        # 字面的数字曾按客队视角记（P493 买 goalLine=-1 却记 'a+1'），不可用作让球线。
        m = _HHAD_RE.match(str(pick))
        if not m:
            return None
        side = m.group(1)
        gl = tleg.get('goalLine')
        if gl is None:
            gl = int(m.group(2))            # 旧腿无 goalLine → 退回 pick 字面（口径自担）
        diff = (h + float(gl)) - a
        got = 'h' if diff > 0 else ('d' if diff == 0 else 'a')
        return odds.get(pick) if side == got else None
    if kind == 'hafu':
        return None                # 半全场需半场比分，结算视图未提供 → 保守判灭（账本无在用腿）
    # crs：精确键 或 方向"其他"键（高比分近似语义）
    if pick in ('胜其他', '平其他', '负其他'):
        want = {'胜其他': 0, '平其他': 1, '负其他': 2}[pick]
        return odds.get(pick) if outcome == want else None
    if pick in ('other_h', 'other_d', 'other_a'):   # L 族归一 other 键（review fix·2026-10-13）
        want = {'other_h': 0, 'other_d': 1, 'other_a': 2}[pick]
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
            if rec.get('void'):
                continue               # 体彩无效场次腿按 1.00 计（官方兑付=等价剔除，剩余腿照常过关）
            price = _pick_price(tleg, pick, rec.get('outcome'), rec.get('score'))
            if price is None:
                odds_prod = None
                break
            odds_prod *= price
        if odds_prod is not None:
            payout += BET_UNIT * ticket['mult'] * odds_prod
    return round(payout, 2)
