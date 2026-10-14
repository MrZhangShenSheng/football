#!/usr/bin/env python3
"""paper.py — paper trading 影子账本（Task 8，阶段 2 备用）。

真实购买的票走正账 data/06-tickets/tickets.json（testGroup="complex-test" 标记，
由会话内实票登记流程完成）——本工具只管影子票：测试方案的纯影子对照形状（不购买）。

2026-09-07 迁址事故处置（大哥拍板）：原居 engine/scripts/scratch/replay_v2/（gitignore
不入库），同目录 shapes.py（回放引擎）/data.py/qcache 与账本 paper_tickets.json 被
scratch 清理误删且无 git 副本——本文件迁 engine/shadow/ 纳管 git；shapes 改懒加载
（缺失时报明确错误），账本丢失后从空账重新积累；不凭记忆重建 shapes（口径漂移比
缺数据更糟）。历史影子票仅 commit message 层面可追溯。

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
import datetime as _datetime   # shadow_all/settle-all CLI 无参调用 str(_datetime.date.today()) 的别名修复
import json
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))   # engine/shadow → 仓库根（迁移前 replay_v2 4 层）
PAPER_FILE = os.path.join(HERE, 'paper_tickets.json')
ID_PREFIX = 'P'   # 影子票前缀（正账 TNNN，影子 PNNN）
TRACK_A = 'A'     # 轨道A=EV版（默认轨：旧影子票无 track 与未标轨 spec 均归此；N=叙事版）
SHADOW_LOST_NOTE = ('shapes.py 已于 2026-09-07 确认丢失（scratch 清理误删·未进 git）——'
                    '影子层引擎待重建，见本文件头部迁址记录')


def _require_shapes():
    """懒加载 shapes（迁址后引擎文件丢失——缺失时报明确错误而非裸 ImportError 崩全文件）。"""
    try:
        import shapes
    except ImportError:
        raise RuntimeError(SHADOW_LOST_NOTE) from None
    return shapes


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
        if isinstance(tickets, dict) and "tickets" in tickets:
            tickets = tickets["tickets"]   # 兼容旧 {"tickets": [...]} 格式
        else:
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


# ── 登记（赔率冻结进票，写账本；chain=链籍 stamp·2026-10-12 回溯机制）──
def register(spec_name, date, legs, bets, mult, cost, path=None, track=None, chain=None):
    """→ 票 dict（已写账本）。legs=冻结腿（freeze_legs 产物或手工同构）；bets/mult/cost
    = build_ticket 输出对应字段透传（bets 元组展开为 list，JSON 可序列化）；
    track=A|N（缺省 A）；chain=provenance.stamp_for_spec 产物（票.chain 字段，
    回溯归因单一事实源——腿出自哪条链哪一代，读票即知）。"""
    path = path or PAPER_FILE
    tickets = load_tickets(path)
    ticket = {
        'id': _next_id(tickets),
        'spec_name': spec_name,
        'track': track or TRACK_A,
        'date': date,
        'chain': chain,
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
        if outcome == 'void':
            legs_out.append({'code': leg['code'], 'void': True, 'outcome': None, 'score': None})
        else:
            if leg['market'] != 'had' and score is None:
                return None                # ttg/crs 腿缺比分不可结算（shapes._hit_of 需 score）
            legs_out.append({'code': leg['code'], 'outcome': outcome, 'score': score})
        tlegs.append({'src': len(legs_out) - 1, 'kind': leg['market'], 'odds': leg['odds']})
        # 体彩无效场次：腿按 1.00 计（shapes.settle void 分支），先于缺比分判定
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


def _annotate_legs(ticket, legs_view, shapes):
    """结算视图 → 票腿写回 result/actual/hitPrice（回溯机制·幂等：已有 result 的腿不动）。
    腿级命中口径：该腿任一 pick 在 shapes._pick_price 有价（与票级 settle 同判）。"""
    for leg, lv in zip(ticket['legs'], legs_view):
        if leg.get('result'):
            continue
        if lv.get('void'):
            leg['result'] = 'void'
            leg['actual'] = None
            continue
        sc = lv.get('score')
        leg['actual'] = f"{sc[0]}:{sc[1]}" if sc else None
        tleg = {'kind': leg['market'], 'odds': leg['odds']}
        hit = False
        for p in leg['pick']:
            price = shapes._pick_price(tleg, p, lv.get('outcome'), sc)
            if price is not None:
                hit = True
                leg['hitPrice'] = price
                break
        leg['result'] = 'hit' if hit else 'miss'


def settle_all(round_results, date=None, path=None):
    """真实赛果结算影子票 → 本次结算的票 list。
    round_results = {code: (outcome, score)}（outcome=0/1/2，score=(h,a) 或 None）；
    按 code（+可选 date=方案日）匹配票腿；已 settled 跳过（幂等）。
    结算成功时腿级 result/actual 同步落盘（_annotate_legs·2026-10-12 回溯机制）。
    date 匹配（I-3，默认=票自身 date）：显式传 date → 票 date == date 才入结算窗；
    不传 date → 按票自身 date 做跨周歧义守卫——体彩"周XNNN"编号按周重复，若某 code
    落在不同 date 的 pending 票上（跨周同编号歧义形态），涉歧票据全部保持 pending，
    须显式传 date 消歧后分周结算（扁平 round_results 无日期标注，无法自动归属）。"""
    path = path or PAPER_FILE
    shapes = _require_shapes()
    tickets = load_tickets(path)
    dates_by_code = {}
    for t in tickets:
        if t.get('result') == 'settled':
            continue
        for leg in t['legs']:
            dates_by_code.setdefault(leg['code'], set()).add(t.get('date'))
    ambiguous = {code for code, ds in dates_by_code.items() if len(ds) > 1}
    settled = []
    dirty = False
    for t in tickets:
        if t.get('result') == 'settled':
            # 幂等补算：已结算但腿级明细缺失（2026-10-12 之前的历史票）→ 顺带补齐。
            # 仅显式 date 且票 date==date 时做（防跨周同编号拿错周赛果；全量补齐走 annotate）
            if (date is not None and t.get('date') == date and round_results
                    and any(not lg.get('result') for lg in t['legs'])):
                view = _settle_view(t, round_results)
                if view is not None:
                    _annotate_legs(t, view[0], shapes)
                    dirty = True
            continue                        # 幂等：已结算不重算 payout
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
        _annotate_legs(t, legs, shapes)
        settled.append(t)
        dirty = True
    if dirty:
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


# ── L 族装载（2026-10-13 spec·llm-predict 设计 §5）───────────────────────
LLM_PREDS_DIR = os.path.join(REPO, 'engine', 'cache', 'llm_preds')
LLM_CARDS_DIR = os.path.join(REPO, 'engine', 'cache', 'llm_cards')
LLM_TAIL_ODDS_FLOOR = 30.0        # 右尾键赔率下限（llm-predict-prereg frozenParams 冻结值）
LLM_TAIL_FIXED = ('0:3', '0:4')   # 强客零封固定右尾格（第一约束：跨窗 84%/97% 最稳档）


def _llm_pick_key(k):
    """市场层 crs 键三态归一 → pick 键（与 llm_pred.freeze/llm_eval._pick_key 同逻辑·勿 import 私有代码）。"""
    if ':' in k:
        return k
    if k.startswith('s') and len(k) == 6 and k[3] == 's':
        return f"{int(k[1:3])}:{int(k[4:6])}"
    return ('other_h' if k in ('s1sh', '胜其他') else 'other_d' if k in ('s1sd', '平其他')
            else 'other_a' if k in ('s1sa', '负其他') else None)


def load_llm_preds(date: str):
    """llm 冻结预测 → L 族 qc：{code: {'crs': [(pick, p), ...] 右尾键内按冻结 p 降序}}。
    右尾键=该场卡市场层有价键中赔率≥30 ∪ 固定 {0:3,0:4}（与 llm_eval.tail_keys_of 同口径，
    赔率读数据卡 market 层·freeze 已保证 p_crs 键有价，此处独立复核防卡事后漂移）；
    llm_preds/{date}.json 缺失 → None（L 族诚实跳过——llm 预测由会话 llm_pred.freeze 预先
    冻结，本函数只消费不生成）。开发者 sszhang"""
    p = os.path.join(LLM_PREDS_DIR, f'{date}.json')
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding='utf-8') as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    qc = {}
    for pred in (doc or {}).get('preds') or []:
        code = pred.get('code')
        p_crs = pred.get('p_crs') or {}
        if not code or not p_crs:
            continue
        odds = {}                      # 该场卡市场层 {pick 键: 赔率}
        try:
            with open(os.path.join(LLM_CARDS_DIR, date, f'{code}.json'), encoding='utf-8') as fh:
                card = json.load(fh)
            for k, v in (((card.get('layers') or {}).get('market') or {}).get('crs') or {}).items():
                kk = _llm_pick_key(k)
                if kk and _isnum(v) and float(v) > 1.0:
                    odds[kk] = float(v)
        except (OSError, json.JSONDecodeError, AttributeError):
            pass                       # 卡缺失/脏 → odds 空 → 该场右尾键全缺（诚实不选）
        ranked = []
        for k, v in sorted(p_crs.items(), key=lambda kv: -float(kv[1])):
            o = odds.get(k)
            tail = (o is not None) if k in LLM_TAIL_FIXED else (o is not None and o >= LLM_TAIL_ODDS_FLOOR)
            if tail:
                ranked.append((k, float(v)))
        if ranked:
            qc[code] = {'crs': ranked}
    return qc or None


def shadow_all(date=None, out=None):
    """当轮在售场次 → 全部 PlanSpec 影子票登记（冻结当刻体彩五池真实价）。

    选腿：市场去水口径（体彩 had 1/o 归一）——与回放 spec #15 口径一致；
    TTG/CRS/HAFU 池直接用当刻池价（影子层独有：历史无池价，从今天积累）；
    幂等：同日同 spec 已登记则跳过。
    """
    import glob as _glob
    _shapes = _require_shapes()
    date = date or str(_datetime.date.today())
    sm = json.load(open(os.path.join(REPO, 'engine', 'cache', 'sporttery_matches.json'),
                        encoding='utf-8'))
    # 当日轮腿构造（HAD 三向去水 + 池价透传）
    legs = []
    for m in sm.get('matches', []):
        # matchDate 两态兼容（纯 'YYYY-MM-DD' / 全 'YYYY-MM-DD HH:MM:SS'·盘中刷新后为全格式）
        # ——精确 == 在全格式日会零腿空转，整轮影子票静默不登记（freq_snap 同病同修）
        if str(m.get('matchDate') or '')[:10] != date:
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
    if qc is None:                              # 快照缺失 → freq_snap 自动生成（2026-09-08 重建接线）
        try:
            import freq_snap
            freq_snap.snap(date)
            qc = _shapes.load_qcache(date)
        except Exception:
            qc = None                           # 诚实降级：ttg/crs 族跳过（build_ticket 收 None 同现状）
    tickets = load_tickets(out)
    done = {(t.get('spec_name'), t.get('date')) for t in tickets}
    n = 0
    for spec in _shapes.PLAN_FAMILIES:
        if (spec['name'], date) in done:
            continue
        spec_qc = qc
        if spec.get('pool') == 'v4b':
            # V 族盲测（2026-09-30 B+C）：qc 换 v4b 快照；缺失时 predictor_v4b_daily 自动生成
            spec_qc = _shapes.load_v4b_snap(date)
            if spec_qc is None:
                try:
                    import subprocess as _sp
                    import sys as _sys
                    _sp.run([_sys.executable,
                             os.path.join(REPO, 'engine', 'scripts', 'predictor_v4b_daily.py'),
                             date, '--snap-only'],
                            check=True, cwd=REPO)
                    spec_qc = _shapes.load_v4b_snap(date)
                except Exception:
                    spec_qc = None           # 诚实降级：v4b spec 跳过（映射失败/历史不足皆打印原因）
        if spec.get('pool') == 'llm_crs':
            # L 族 forward（2026-10-13 spec）：qc=llm 冻结预测装载（右尾键降序）；无自动生成
            # ——llm 预测由会话 llm_pred.freeze 预先冻结，文件缺失诚实跳过（L 族当日无票）
            spec_qc = load_llm_preds(date)
        tk = _shapes.build_ticket(legs, spec, qc=spec_qc)
        if tk is None:
            continue
        legs_frozen = freeze_legs(legs, tk)
        # 影子层核心：TTG/CRS 腿用体彩当刻真实池价覆盖反推近似价（pools 冻结）
        for tl, fl in zip(tk['tlegs'], legs_frozen):
            if tl['kind'] in ('ttg', 'crs'):
                src_leg = legs[tl['src']]
                pool = (src_leg.get('pools') or {}).get(tl['kind']) or {}
                if tl['kind'] == 'crs':          # 体彩原始键三态归一：s01s00→'1:0'、s1sh/s1sd/s1sa
                    pool = {(_llm_pick_key(k) or k): v         # →other_*（review fix·L族other腿冻价）
                            for k, v in pool.items()}          # 复用 _llm_pick_key 与 L 族装载同口径
                real = {k: float(v) for k, v in pool.items()
                        if k in tl['picks'] and _isnum(v)}
                if real:
                    fl['odds'] = real
                    fl['odds_src'] = 'tiyu-live'
        # 链籍 stamp（回溯机制·fail-open：registry 缺失不炸登记）
        try:
            import provenance
            chain_stamp = provenance.stamp_for_spec(spec, date)
        except Exception:
            chain_stamp = None
        register(spec['name'], date, legs_frozen, tk['bets'], tk['mult'], tk['cost'],
                 path=out, track=spec.get('track', TRACK_A), chain=chain_stamp)
        n += 1
    return {'date': date, 'legs': len(legs), 'registered': n,
            'pools_field': '五池价冻结（crs31/ttg8/hafu9——影子层独有历史缺口补采）'}


if __name__ == '__main__' and len(__import__('sys').argv) > 1 and __import__('sys').argv[1] == 'shadow-all':
    print(json.dumps(shadow_all(*(__import__('sys').argv[2:3])), ensure_ascii=False, indent=1))


def settle_all_cli(day):
    """settle-all <比赛日> CLI 主体（赛果自 02-results 主文件/体彩缓存自动对票）。"""
    # 邻日轮合并：登记按体彩 matchDate（完赛自然日）、load_rounds 按销售日文件，两口径对晚场
    # 错位一天（如 09-09 18:30 完赛的江原 matchDate=09-09 而销售日=09-10）——单日轮会让
    # 跨口径票（P047：date=09-09 + 江原腿在 09-10 轮）结构性缺赛果永远 pending。合并
    # day±1 后由 dict 后写覆盖保证 day 本日优先，跨周同编号歧义仍由 settle_all 守卫兜底
    rounds = _all_rounds_by_day()
    _d0 = _datetime.date.fromisoformat(day)
    legs_raw = []
    for _dd in ((_d0 - _datetime.timedelta(days=1)).isoformat(),
                (_d0 + _datetime.timedelta(days=1)).isoformat(),
                day):                     # day 最后灌入 → dict 后写覆盖 → 本日轮优先
        legs_raw.extend(rounds.get(_dd) or [])
    round_results = {l['code']: (l['outcome'], l.get('score')) for l in legs_raw}
    paper_settle = settle_all(round_results, date=day) if round_results else []
    print(json.dumps({'date': day, 'settled': len(paper_settle),
                      'note': '无该日赛果则影子票保持 pending'}, ensure_ascii=False))


# ───── 回溯机制（2026-10-12 大哥拍板四件套：链籍/腿级明细/why/annotate）─────
def _all_rounds_by_day():
    """全量轮次 → {销售日: legs}（load_rounds + 体彩开奖缓存逐日归并；settle-all 同源）。"""
    import glob as _glob
    try:
        import data as _data
        rounds = {r['date']: r['legs'] for r in _data.load_rounds()}
    except ImportError:
        rounds = {}
    for f in _glob.glob(os.path.join(REPO, 'engine/cache/sporttery_results_*.json')):
        try:
            d = json.load(open(f, encoding='utf-8'))
        except Exception:                  # noqa: BLE001 —— 脏缓存文件跳过不炸
            continue
        for m in d.get('matches', []):
            dt = m.get('matchDate')
            sc = str(m.get('score') or '')
            if m.get('status') == 'Played' and sc.startswith('无效') and dt:
                rounds.setdefault(dt, []).append({'code': m.get('code'), 'outcome': 'void', 'score': None})
            elif m.get('status') == 'Played' and ':' in sc and dt:
                h, a = sc.split(':')
                rounds.setdefault(dt, []).append({
                    'code': m.get('code'),
                    'outcome': 0 if int(h) > int(a) else (1 if int(h) == int(a) else 2),
                    'score': (int(h), int(a))})
    return rounds


def annotate(path=None):
    """历史已结算票 → 腿级 result/actual 幂等补齐（按票自身 date±1 取赛果，跨周同编号
    天然不错配）。→ 补写票数。"""
    path = path or PAPER_FILE
    shapes = _require_shapes()
    tickets = load_tickets(path)
    rounds = _all_rounds_by_day()
    n = 0
    for t in tickets:
        if t.get('result') != 'settled' or all(lg.get('result') for lg in t['legs']):
            continue
        d0 = _datetime.date.fromisoformat(t['date'])
        mini = {}
        for dd in ((d0 - _datetime.timedelta(days=1)).isoformat(),
                   (d0 + _datetime.timedelta(days=1)).isoformat(),
                   t['date']):              # 本日最后灌入优先
            for l in rounds.get(dd) or []:
                mini[l['code']] = (l['outcome'], l.get('score'))
        view = _settle_view(t, mini)
        if view is not None:
            _annotate_legs(t, view[0], shapes)
            n += 1
    if n:
        _save(tickets, path)
    return n


def restamp(path=None):
    """历史无 chain 字段的影子票 → 按票 date+spec 反查 registry 幂等补 stamp。
    spec 先按 PLAN_FAMILIES 名反查（pool 路径），miss 再按 spec_name 直查
    （独立执行器 spec·provenance.SPEC_NAME_CHAIN）。→ 补写票数。"""
    import provenance
    path = path or PAPER_FILE
    tickets = load_tickets(path)
    spec_by_name = {s['name']: s for s in _require_shapes().PLAN_FAMILIES}
    n = 0
    for t in tickets:
        if t.get('chain'):
            continue
        name = t.get('spec_name')
        spec = spec_by_name.get(name) or {'name': name}
        t['chain'] = provenance.stamp_for_spec(spec, t.get('date') or '')
        if t['chain']:
            n += 1
    if n:
        _save(tickets, path)
    return n


def _fmt_leg_odds(leg):
    """冻结腿 → '价/价' 摘要（had 三槽 list 取买槽；池腿 dict 取买键；实票数值直出）。"""
    o = leg.get('odds')
    p = leg.get('pick')
    picks = p if isinstance(p, list) else ([p] if p is not None else [])
    if isinstance(o, (int, float)):
        return '/'.join(str(o) for _ in picks) or str(o)
    vals = []
    for x in picks:
        try:
            vals.append(str(o[int(x)] if leg.get('market') == 'had' and isinstance(o, list)
                            else o.get(str(x), o.get(x, '?'))))
        except (TypeError, ValueError, IndexError):
            vals.append('?')
    return '/'.join(vals)


def spec_chain_stats(tickets):
    """已结算票 → {(spec, engine): {n, cost, payout}}（分链战绩·why 尾注用）。"""
    agg = {}
    for t in tickets:
        if t.get('result') != 'settled':
            continue
        key = (t.get('spec_name'), (t.get('chain') or {}).get('engine') or '?')
        s = agg.setdefault(key, {'n': 0, 'cost': 0.0, 'payout': 0.0})
        s['n'] += 1
        s['cost'] += t.get('cost') or 0
        s['payout'] += t.get('payout') or 0
    return agg


_MARK = {'hit': '✓', 'miss': '✗', 'void': '∮', None: '…'}


def _render_ticket(t):
    """票 → 控制台回溯块（票头/链籍/逐腿/分链战绩）。影子票与实票（tickets.json）双 schema 兼容。"""
    ch = t.get('chain') or {}
    if 'stake' in t and 'spec_name' not in t:      # 实票 schema（settled 未结=False/结算=dict）
        settled = t.get('settled')
        result = settled.get('status') if isinstance(settled, dict) else 'pending'
        pay = settled.get('payout') if isinstance(settled, dict) else None
        cost = t.get('stake')
        spec = t.get('shape') or t.get('spec')
        day = (t.get('matchDays') or [None])[0]
    else:                                          # 影子票 schema
        result = t.get('result')
        pay = t.get('payout')
        cost = t.get('cost')
        spec = t.get('spec_name')
        day = t.get('date')
    net = (pay - cost) if isinstance(pay, (int, float)) and isinstance(cost, (int, float)) else None
    print(f"=== {t.get('id')} {spec} {day} [{result}] ===")
    if ch:
        print(f"  链籍: {ch.get('chain')} · {ch.get('engine')} · since {ch.get('since')}")
    else:
        print("  链籍: 无 stamp（历史票·restamp 可补）")
    print(f"  投入 {cost} · 回款 {pay if pay is not None else '—'}"
          + (f" · 净 {net:+.2f}" if net is not None else ''))
    for lg in t.get('legs', []):
        r = lg.get('result')
        actual = lg.get('actual')
        price = lg.get('hitPrice')
        tail = f" → {actual} {_MARK.get(r, '?')}" + (f" @{price}" if price else '') if actual or r else ''
        print(f"    {lg.get('code')} {str(lg.get('match'))[:22]:24s} {lg.get('market') or lg.get('kind')}"
              f" {str(lg.get('pick'))[:18]:20s} @{_fmt_leg_odds(lg)}{tail}")
    return t


def why(target, path=None):
    """回溯入口：P票号=影子票 | T票号=实票 | 日期=当日全部影子票。"""
    if str(target).startswith('T'):
        tp = os.path.join(REPO, 'data', '06-tickets', 'tickets.json')
        doc = json.load(open(tp, encoding='utf-8'))
        for t in doc.get('tickets', []):
            if t.get('id') == target:
                _render_ticket(t)
                return t
        print(f'[why] 实票账本无 {target}')
        return None
    tickets = load_tickets(path or PAPER_FILE)
    if str(target).startswith('P'):
        for t in tickets:
            if t.get('id') == target:
                _render_ticket(t)
                stats = spec_chain_stats(tickets)
                s = stats.get((t.get('spec_name'), (t.get('chain') or {}).get('engine') or '?'))
                if s:
                    print(f"  分链战绩: {t.get('spec_name')} × {(t.get('chain') or {}).get('engine')}: "
                          f"{s['n']}票 投{s['cost']:.0f} 回{s['payout']:.2f} 净{s['payout'] - s['cost']:+.2f}")
                return t
        print(f'[why] 影子账本无 {target}')
        return None
    sel = [t for t in tickets if t.get('date') == str(target)]
    if not sel:
        print(f'[why] 无 {target} 的影子票（P票号/T票号/日期三口径）')
        return None
    for t in sel:
        _render_ticket(t)
    return sel


if __name__ == '__main__' and len(__import__('sys').argv) > 1:
    _argv = __import__('sys').argv[1:]
    if _argv[0] == 'why':
        why(_argv[1])
    elif _argv[0] == 'annotate':
        print(f'[annotate] 腿级明细补齐 {annotate()} 张')
    elif _argv[0] == 'restamp':
        print(f'[restamp] 链籍补 stamp {restamp()} 张')
    elif _argv[0] == 'spec-stats':
        tickets = load_tickets()
        for (spec, eng), s in sorted(spec_chain_stats(tickets).items(),
                                     key=lambda kv: -(kv[1]['payout'] - kv[1]['cost'])):
            print(f"  {spec:24s} × {eng:36s} {s['n']:>3}票 投{s['cost']:>6.0f} "
                  f"回{s['payout']:>9.2f} 净{s['payout'] - s['cost']:+9.2f}")
    elif _argv[0] == 'settle-all':
        settle_all_cli(_argv[1] if len(_argv) > 1 else str(_datetime.date.today()))
