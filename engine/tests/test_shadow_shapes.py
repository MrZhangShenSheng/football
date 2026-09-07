# engine/tests/test_shadow_shapes.py
# 影子层票型引擎重建版（engine/shadow/shapes.py）——等成本/结算/复式/胶着降级/q 缺失跳过
# 断言对齐设计文档 Task 4 + 回放报告 19 spec 实况成本。开发者 sszhang
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "shadow"))
import shapes


def _had_leg(i, fuse_h, fuse_d, fuse_a):
    """shadow_all 口径假腿：fused 去水三向 + 体彩三向价 + 空池。"""
    return {'code': f'周日{i:03d}', 'match': f'主{i} vs 客{i}', 'league': '测试',
            'fused': [fuse_h, fuse_d, fuse_a],
            'odds': [round(1 / fuse_h * 1.1, 2), round(1 / fuse_d * 1.1, 2), round(1 / fuse_a * 1.1, 2)],
            'exact': True, 'outcome': None, 'half': None, 'dual_source': False, 'score': None,
            'pools': {}}


def _strong8():
    """8 场主胜强腿（fused 主概率降序 0.75→0.435，客概率升序无并列），前 4/6/8 供各 spec 取用。"""
    return [_had_leg(i, 0.75 - i * 0.045, 0.2, 0.05 + i * 0.045) for i in range(8)]


SPEC = {s['name']: s for s in shapes.PLAN_FAMILIES}


def test_equal_cost_matches_report():
    legs = _strong8()
    assert shapes.build_ticket(legs, SPEC['4串11'])['cost'] == 22
    assert shapes.build_ticket(legs, SPEC['8串1'])['cost'] == 30
    assert shapes.build_ticket(legs, SPEC['4串1'])['cost'] == 30
    assert shapes.build_ticket(legs, SPEC['全2关-6'])['cost'] == 30
    assert shapes.build_ticket(legs, SPEC['全2关-4'])['cost'] == 24
    assert shapes.build_ticket(legs, SPEC['4串1+2双选'])['cost'] == 24
    assert shapes.build_ticket(legs, SPEC['ev-8串9'])['cost'] == 18
    assert shapes.build_ticket(legs, SPEC['胶着-三选2串1'])['cost'] == 18
    assert shapes.build_ticket(legs, SPEC['胶着-双选全2关'])['cost'] == 48
    assert shapes.build_ticket(legs, SPEC['胶着-双选单关'])['cost'] == 28


def test_settle_all_win_vs_one_miss():
    legs = _strong8()
    t = shapes.build_ticket(legs, SPEC['8串1'])
    # 赛果全主胜（票买主胜）→ 回款=2×15×∏价 > 0
    res = [{'code': l['code'], 'outcome': 0, 'score': (2, 0)} for l in legs]
    win = shapes.settle(res, {'tlegs': t['tlegs'], 'bets': t['bets'], 'mult': t['mult']})
    assert win > 0
    # 一场客胜 → 8串1 全灭
    res[3] = {'code': legs[3]['code'], 'outcome': 2, 'score': (0, 1)}
    miss = shapes.settle(res, {'tlegs': t['tlegs'], 'bets': t['bets'], 'mult': t['mult']})
    assert miss == 0


def test_settle_4串11_partial_win():
    legs = _strong8()
    t = shapes.build_ticket(legs, SPEC['4串11'])
    res = [{'code': l['code'], 'outcome': 0, 'score': (2, 0)} for l in legs]
    res[3] = {'code': legs[3]['code'], 'outcome': 2, 'score': (0, 1)}   # 第4腿爆
    pay = shapes.settle(res, {'tlegs': t['tlegs'], 'bets': t['bets'], 'mult': t['mult']})
    # 断第4腿：不含腿3的组合存活（含腿0,1,2 的 2串1×1 + 3串1×1）
    from itertools import combinations
    alive = [c for size in (2, 3, 4) for c in combinations(range(4), size) if 3 not in c]
    expect = sum(2 * 1 * __import__('math').prod(legs[i]['odds'][0] for i in c) for c in alive)
    assert pay == round(expect, 2) and pay > 0


def test_dual_second_option_pays():
    """双选腿次选项命中应派彩（复式核心：未买槽 None 不误伤已买槽）。"""
    legs = _strong8()
    t = shapes.build_ticket(legs, SPEC['4串1+2双选'])
    # 后2腿为双选（主+平）；赛果后2腿出平局 → 双选次选项命中仍派彩
    res = []
    for i, l in enumerate(legs[:4]):
        res.append({'code': l['code'],
                    'outcome': 0 if i < 2 else 1,
                    'score': (2, 0) if i < 2 else (1, 1)})
    pay = shapes.settle(res, {'tlegs': t['tlegs'], 'bets': t['bets'], 'mult': t['mult']})
    assert pay > 0


def test_had_odds_frozen_only_bought_slots():
    legs = _strong8()
    t = shapes.build_ticket(legs, SPEC['4串1'])
    for tleg in t['tlegs']:
        assert tleg['odds'][1] is None and tleg['odds'][2] is None  # 只买主胜槽
        assert tleg['odds'][0] > 1


def test_jiao_pool_prefers_tight_legs():
    """胶着池=三向极差升序：极差小的场优先入池。"""
    legs = [_had_leg(0, 0.7, 0.2, 0.1),      # 极差 0.6（强腿）
            _had_leg(1, 0.35, 0.33, 0.32),   # 极差 0.03（胶着）
            _had_leg(2, 0.34, 0.33, 0.33)]   # 极差 0.01（最胶着）
    t = shapes.build_ticket(legs, SPEC['胶着-三选2串1'])
    srcs = [tl['src'] for tl in t['tlegs']]
    assert set(srcs) == {2, 1}               # 最胶着两场入选


def test_jiao_short_pool_degrades():
    """胶着池腿<N 降级用全部可用（spec 变更#6）。"""
    legs = [_had_leg(i, 0.34, 0.33, 0.33) for i in range(2)]   # 仅2场胶着
    t = shapes.build_ticket(legs, SPEC['胶着-双选全2关'])       # n_legs=4 → 降级2场
    assert t is not None and len(t['tlegs']) == 2
    assert t['n_bets'] == 4                   # C(2,2)=1骨架 × 2×2 双选
    assert t['cost'] == 24                    # 4注×2元×mult=⌊30/8⌋=3


def test_ttg_crs_skip_without_qcache():
    """q 快照缺失 → ttg/crs 整族跳过（qcache 随事故丢失待重建的诚实降级）。"""
    legs = _strong8()
    assert shapes.build_ticket(legs, SPEC['ttg-4串1'], qc=None) is None
    assert shapes.build_ticket(legs, SPEC['crs-双选单关'], qc=None) is None


def test_ttg_build_and_settle_with_qcache():
    legs = _strong8()
    for l in legs:                            # 补 ttg 池价
        l['pools']['ttg'] = {'s0': 12.0, 's1': 6.0, 's2': 4.0, 's3': 4.2}
    qc = {l['code']: {'ttg': [('s2', 0.28), ('s3', 0.24), ('s1', 0.2)]} for l in legs}
    t = shapes.build_ticket(legs, SPEC['ttg-单关'], qc=qc)
    assert t is not None and t['cost'] == 30  # 3注×2×5
    res = [{'code': l['code'], 'outcome': 0, 'score': (1, 1)} for l in legs[:3]]  # 2球→s2 全中
    pay = shapes.settle(res, {'tlegs': t['tlegs'], 'bets': t['bets'], 'mult': t['mult']})
    assert pay == 2 * 5 * 4.0 * 3


def test_legs_too_few_returns_none():
    legs = [_had_leg(0, 0.7, 0.2, 0.1)]
    assert shapes.build_ticket(legs, SPEC['4串1']) is None     # 串关最少2腿
    t = shapes.build_ticket(legs, SPEC['ttg-单关'], qc=None)   # 无 q 仍跳过
    assert t is None
