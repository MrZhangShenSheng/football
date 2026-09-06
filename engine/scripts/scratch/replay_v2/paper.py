#!/usr/bin/env python3
"""paper.py — paper trading 影子账本（Task 8，阶段 2 备用）。

真实购买的票走正账 data/06-tickets/tickets.json（testGroup="complex-test" 标记，
由会话内实票登记流程完成）——本工具只管影子票：测试方案的纯影子对照形状（不购买）。

口径：
  - 票结构同 build_ticket 输出（bets/mult/cost 逐字段透传），账本条目 =
    {id, spec_name, track, date, legs, bets, n_bets, mult, cost, result, payout, settledAt}
    （track=A|N 影子票来源轨道·设计§七兼容面④：新票强制带 track，旧票无 track 默认 A）
  - 腿冻结格式 = {code, match, market, pick, odds}（market=had|ttg|crs，赔率建票时冻结进票）；
    had 腿 odds 冻结为三槽 list（JSON 无 int dict 键，str 化后 settle 的 int 下标会 KeyError——
    list 直取下标天然免疫）；ttg/crs 腿键本就是字符串，存 str 键 dict
  - 结算 100% 复用 shapes.settle（Task 7 交接：不重造）。settle_all 只做三件事：
    code 匹配赛果 → settle 腿视图透传 → 写回 payout/settledAt
  - 可结算条件：票腿 code 全部 ∈ round_results 且非 had 腿有 score；任一腿缺赛果 → 整票
    保持 pending（诚实保态，不半结算）。date 匹配（final-review I-3，默认=票自身 date）：
    体彩"周XNNN"编号按周重复——显式传 date 时须票 date == date；不传 date 时按票自身
    date 做跨周歧义守卫（同 code 落在不同 date 的 pending 票存在 → 这些票保持 pending，
    须显式 date 消歧），消除不传 date 时跨周串号风险
  - 幂等：result=="settled" 的票不再重算（重复调用/矛盾赛果均不改动 payout/settledAt）
  - 账本读写（final-review I-3 转正三修）：损坏（JSON 解析失败/非 list 结构）→ 先备份
    原文件为 <path>.corrupt-{时间戳} 再 raise（不静默清账——影子票凭证优先）；_save 原子写
    （同目录临时文件 + os.replace，中断不毁账）
开发者 sszhang"""
import datetime
import datetime as _datetime   # L247/L310 无参调用 str(_datetime.date.today()) 的别名修复
import json
import os
import shutil

import shapes

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..', '..'))
PAPER_FILE = os.path.join(HERE, 'paper_tickets.json')
ID_PREFIX = 'P'   # 影子票前缀（正账 TNNN，影子 PNNN）
TRACK_A = 'A'     # 轨道A=EV版（默认轨：旧影子票无 track 与未标轨 spec 均归此；N=叙事版）


# ── 账本读写（I-3：损坏备份+raise / 原子写）──
def _backup_corrupt(path):
    """损坏账本备份（I-3）：copy 为 <path>.corrupt-{YYYYmmdd-HHMMSS-ffffff}（微秒防同秒
    二次损坏互覆）；备份失败不遮蔽主异常"""
    ts = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    try:
        shutil.copy2(path, f'{path}.corrupt-{ts}')
    except OSError:
        pass


def load_tickets(path=None):
    """→ list[票]；文件缺失 → []（首次登记自动建账）；损坏（JSON 解析失败/非 list 结构）→
    先备份原文件为 <path>.corrupt-{ts} 再 raise（I-3：不静默清账——影子票凭证优先）"""
    p = path or PAPER_FILE
    try:
        with open(p, encoding='utf-8') as fh:
            tickets = json.load(fh)
    except FileNotFoundError:
        return []
    except json.JSONDecodeError as e:
        _backup_corrupt(p)
        raise RuntimeError(f'影子账本损坏（JSON 解析失败），已备份 {p}.corrupt-*，'
                           f'人工核验后再重建，不静默清账: {e}') from e
    if not isinstance(tickets, list):
        _backup_corrupt(p)
        raise RuntimeError(f'影子账本结构损坏（顶层须为 list，实得 {type(tickets).__name__}），'
                           f'已备份 {p}.corrupt-*')
    return tickets


def _save(tickets, path):
    """原子写（I-3）：先写同目录临时文件 → os.replace 原子覆盖（写盘中断不毁账）"""
    tmp = f'{path}.tmp-{os.getpid()}'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(tickets, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _next_id(tickets):
    nums = [int(t['id'][1:]) for t in tickets
            if isinstance(t.get('id'), str) and t['id'].startswith(ID_PREFIX)
            and t['id'][1:].isdigit()]
    return f"{ID_PREFIX}{max(nums, default=0) + 1:03d}"


# ── 腿冻结（赔率/选项建票时点定格；had 三槽 list / ttg·crs str 键 dict）──
def _norm_leg(leg):
    """tlegs 风格（kind/picks）或冻结风格（market/pick）→ 冻结格式（深拷贝，与源 dict 断链）"""
    market = leg.get('market') or leg.get('kind')
    pick = leg.get('pick', leg.get('picks'))
    if isinstance(pick, (set, frozenset, tuple)):
        pick = sorted(pick)
    pick = [int(p) if market == 'had' else str(p) for p in pick]
    raw = leg.get('odds') or {}
    if isinstance(raw, (list, tuple)):
        raw = {i: v for i, v in enumerate(raw) if v is not None}
    if market == 'had':
        kv = {int(k): float(v) for k, v in raw.items()}
        odds = [kv.get(k) for k in (0, 1, 2)]      # 未买槽 None（bets 只含 picks 内键，永不触及）
    else:
        odds = {str(k): float(v) for k, v in raw.items()}
    return {'code': leg.get('code'), 'match': leg.get('match'),
            'market': market, 'pick': pick, 'odds': odds}


def freeze_legs(round_legs, ticket):
    """build_ticket 票腿 → 影子票冻结腿：补 code/match（源腿按 tleg['src'] 透传），赔率冻结"""
    out = []
    for t in ticket['tlegs']:
        src = round_legs[t['src']]
        out.append(_norm_leg({**t, 'code': src.get('code'), 'match': src.get('match')}))
    return out


# ── 登记（赔率冻结进票，写账本）──
def register(spec_name, date, legs, bets, mult, cost, path=None, track=None):
    """→ 票 dict（已写账本）。legs=冻结腿（freeze_legs 产物或手工同构）；bets/mult/cost
    = build_ticket 输出对应字段透传（bets 元组展开为 list，JSON 可序列化）；
    track=A|N（缺省 A：未标轨 spec/旧调用方兼容口径·设计§七兼容面④）。"""
    path = path or PAPER_FILE
    tickets = load_tickets(path)
    ticket = {
        'id': _next_id(tickets),
        'spec_name': spec_name,
        'track': track or TRACK_A,
        'date': date,
        'legs': [_norm_leg(l) for l in legs],
        'bets': [{'legs': [list(pair) for pair in b['legs']]} for b in bets],
        'n_bets': len(bets),
        'mult': mult,
        'cost': cost,
        'result': 'pending',
        'payout': None,
        'settledAt': None,
    }
    tickets.append(ticket)
    _save(tickets, path)
    return ticket


# ── 结算（shapes.settle 全复用）──
def _settle_view(ticket, round_results):
    """可结算 → (legs, settle_ticket_view)；任一腿赛果缺失 → None（保持 pending）。
    视图只做键位适配（market→kind / src 恒等重锚），结算逻辑全在 shapes.settle。"""
    legs_out, tlegs = [], []
    for leg in ticket['legs']:
        r = round_results.get(leg['code'])
        if r is None:
            return None
        outcome, score = r
        score = tuple(score) if score else None
        if leg['market'] != 'had' and score is None:
            return None                    # ttg/crs 腿缺比分不可结算（shapes._hit_of 需 score）
        legs_out.append({'code': leg['code'], 'outcome': outcome, 'score': score})
        tlegs.append({'src': len(legs_out) - 1, 'kind': leg['market'], 'odds': leg['odds']})
    return legs_out, {'tlegs': tlegs, 'bets': ticket['bets'], 'mult': ticket['mult']}


# ── track 分轨（设计§七·兼容面④：EV版A 与 叙事版N 影子禁混口径）──
def _track_of(ticket: dict) -> str:
    """影子票来源轨道(设计§七·兼容面④): 旧票无track默认A(EV版). 开发者 sszhang"""
    return ticket.get('track') or TRACK_A


def _by_track(tickets: list) -> dict:
    """→ {track: {n, payout, cost}} 全账本分轨统计（旧票经 _track_of 归轨A）. 开发者 sszhang"""
    stats = {}
    for t in tickets:
        k = _track_of(t)
        s = stats.setdefault(k, {'n': 0, 'payout': 0.0, 'cost': 0.0})
        s['n'] += 1
        s['payout'] += t.get('payout') or 0
        s['cost'] += t.get('cost') or 0
    return stats


def settle_all(round_results, date=None, path=None):
    """真实赛果结算影子票 → 本次结算的票 list。
    round_results = {code: (outcome, score)}（outcome=0/1/2，score=(h,a) 或 None）；
    按 code（+可选 date=方案日）匹配票腿；已 settled 跳过（幂等）。
    date 匹配（I-3，默认=票自身 date）：显式传 date → 票 date == date 才入结算窗；
    不传 date → 按票自身 date 做跨周歧义守卫——体彩"周XNNN"编号按周重复，若某 code
    落在不同 date 的 pending 票上（跨周同编号歧义形态），涉歧票据全部保持 pending，
    须显式传 date 消歧后分周结算（扁平 round_results 无日期标注，无法自动归属）。"""
    path = path or PAPER_FILE
    tickets = load_tickets(path)
    dates_by_code = {}
    for t in tickets:
        if t.get('result') == 'settled':
            continue
        for leg in t['legs']:
            dates_by_code.setdefault(leg['code'], set()).add(t.get('date'))
    ambiguous = {code for code, ds in dates_by_code.items() if len(ds) > 1}
    settled = []
    for t in tickets:
        if t.get('result') == 'settled':
            continue                        # 幂等：已结算不重算
        if date is not None and t.get('date') != date:
            continue
        if date is None and any(leg['code'] in ambiguous for leg in t['legs']):
            continue                        # 跨周同编号歧义 → 保 pending，显式 date 消歧
        view = _settle_view(t, round_results)
        if view is None:
            continue                        # 缺赛果保持 pending
        legs, ticket_view = view
        t['payout'] = shapes.settle(legs, ticket_view)
        t['result'] = 'settled'
        t['settledAt'] = datetime.datetime.now().isoformat(timespec='seconds')
        settled.append(t)
    if settled:
        _save(tickets, path)
    for k, s in _by_track(tickets).items():
        print(f"[shadow] track {k}: {s['n']}票 回款{s['payout']:.0f}/{s['cost']:.0f}元")
    return settled


if __name__ == '__main__':
    ledger = load_tickets()
    n_pending = sum(1 for t in ledger if t.get('result') != 'settled')
    total_cost = sum(t['cost'] for t in ledger)
    total_pay = sum(t['payout'] or 0.0 for t in ledger)
    print(f'影子票 {len(ledger)} 张（pending {n_pending} / settled {len(ledger) - n_pending}）| '
          f'投入 {total_cost:.0f} 元 | 回款 {total_pay:.2f} 元 | 账本 {PAPER_FILE}')
    for t in ledger:
        pay = '—' if t.get('payout') is None else f"{t['payout']:.2f}"
        print(f"  {t['id']} {t['date']} {t['spec_name']:14s} cost={t['cost']:>3} "
              f"payout={pay:>9} [{t['result']}]")


# ───── 阶段2 影子层（2026-09-05 大哥拍板：全方案影子票）─────
def _isnum(v):
    try:
        float(v); return True
    except (TypeError, ValueError):
        return False


def shadow_all(date=None, out=None):
    """当轮在售场次 → 全部 PlanSpec 影子票登记（冻结当刻体彩五池真实价）。

    选腿：市场去水口径（体彩 had 1/o 归一）——与回放 spec #15 口径一致；
    TTG/CRS/HAFU 池直接用当刻池价（影子层独有：历史无池价，从今天积累）；
    幂等：同日同 spec 已登记则跳过。
    """
    import glob as _glob
    import shapes as _shapes
    date = date or str(_datetime.date.today())
    sm = json.load(open(os.path.join(REPO, 'engine', 'cache', 'sporttery_matches.json')))
    # 当日轮腿构造（HAD 三向去水 + 池价透传）
    legs = []
    for m in sm.get('matches', []):
        if m.get('matchDate') != date:
            continue
        had = m.get('had') or {}
        try:
            o = [float(had['h']), float(had['d']), float(had['a'])]
        except (KeyError, TypeError, ValueError):
            continue
        if any(x <= 1.0 for x in o):
            continue
        inv = [1.0 / x for x in o]
        s = sum(inv)
        legs.append({'code': m['code'], 'match': f"{m.get('home','')} vs {m.get('away','')}",
                     'league': m.get('league', '?'),
                     'fused': [x / s for x in inv], 'odds': o,
                     'exact': True, 'outcome': None,
                     'half': None, 'dual_source': False, 'score': None,
                     'pools': {'crs': m.get('crs'), 'ttg': m.get('ttg'), 'hafu': m.get('hafu')}})
    if len(legs) < 4:
        return {'error': f'当日({date})可用场 {len(legs)} <4，不成轮', 'registered': 0}
    qc = _shapes.load_qcache(date)
    tickets = load_tickets(out)
    done = {(t.get('spec_name'), t.get('date')) for t in tickets}
    n = 0
    for spec in _shapes.PLAN_FAMILIES:
        if (spec['name'], date) in done:
            continue
        tk = _shapes.build_ticket(legs, spec, qc=qc)
        if tk is None:
            continue
        legs_frozen = freeze_legs(legs, tk)
        # 影子层核心：TTG/CRS 腿用体彩当刻真实池价覆盖反推近似价（pools 冻结）
        for tl, fl in zip(tk['tlegs'], legs_frozen):
            if tl['kind'] in ('ttg', 'crs'):
                src_leg = legs[tl['src']]
                pool = (src_leg.get('pools') or {}).get(tl['kind']) or {}
                if tl['kind'] == 'crs':          # 体彩原始键 s01s00 → '1:0'
                    pool = {(f"{int(k[1:3])}:{int(k[4:6])}" if k.startswith('s') and k[3] == 's' else k): v
                            for k, v in pool.items()}
                real = {k: float(v) for k, v in pool.items()
                        if k in tl['picks'] and _isnum(v)}
                if real:
                    fl['odds'] = real
                    fl['odds_src'] = 'tiyu-live'
        register(spec['name'], date, legs_frozen, tk['bets'], tk['mult'], tk['cost'],
                 path=out, track=spec.get('track', TRACK_A))
        n += 1
    return {'date': date, 'legs': len(legs), 'registered': n,
            'pools_field': '五池价冻结（crs31/ttg8/hafu9——影子层独有历史缺口补采）'}


if __name__ == '__main__' and len(__import__('sys').argv) > 1 and __import__('sys').argv[1] == 'shadow-all':
    print(json.dumps(shadow_all(*(__import__('sys').argv[2:3])), ensure_ascii=False, indent=1))


if __name__ == '__main__' and len(__import__('sys').argv) > 1 and __import__('sys').argv[1] == 'settle-all':
    # settle-all <比赛日>：影子票对真实赛果结算（赛果自 02-results 主文件/体彩缓存自动对票）
    import data as _data, glob as _glob, os as _os
    import sys as _sys
    day = _sys.argv[2] if len(_sys.argv) > 2 else str(_datetime.date.today())
    rounds = {r['date']: r['legs'] for r in _data.load_rounds()}
    # 体彩开奖缓存补充（覆盖 02-results 未回填日）
    for f in _glob.glob(_os.path.join(REPO, 'engine/cache/sporttery_results_*.json')):
        try: d = json.load(open(f))
        except Exception: continue
        for m in d.get('matches', []):
            dt = m.get('matchDate')
            sc = str(m.get('score') or '')
            if m.get('status') == 'Played' and ':' in sc and dt:
                h, a = sc.split(':')
                rounds.setdefault(dt, []).append({
                    'code': m.get('code'), 'match': f"{m.get('home','')} vs {m.get('away','')}",
                    'outcome': 0 if int(h) > int(a) else (1 if int(h) == int(a) else 2),
                    'score': (int(h), int(a)),
                    'half': (tuple(int(x) for x in str(m.get('halfScore') or '0:0').split(':')) if ':' in str(m.get('halfScore') or '') else None),
                })
    # rounds[date]=legs list（load_rounds 口径）→ settle_all 期望 {code:(outcome,score)}
    legs_raw = rounds.get(day) or []
    round_results = {l['code']: (l['outcome'], l.get('score')) for l in legs_raw}
    done = paper_settle = settle_all(round_results, date=day) if round_results else []
    print(json.dumps({'date': day, 'settled': len(paper_settle),
                      'note': '无该日赛果则影子票保持 pending'}, ensure_ascii=False))
