# engine/tests/test_freq_snap.py
# freq_snap 重建测试（2026-09-08）：纯模板排序/λ平移/池价闸门/全局池兜底/冻结幂等
# +零场不落盘/端到端 C-D 族 6 spec。注入 matches/freq_table/form/zh 隔离网络。
# 开发者 sszhang
import json
import sys
from collections import Counter
from pathlib import Path

SHADOW = Path(__file__).parent.parent / "shadow"
SCRIPTS = Path(__file__).parent.parent / "scripts"
for p in (str(SHADOW), str(SCRIPTS)):
    if p not in sys.path:
        sys.path.insert(0, p)

import freq_snap
import shapes
from band_calibration import CURRENT_SEASON
from freq_band import global_pool, league_base_rates, lambdas, shifted_q, ttg_agg

TPL = Counter({'1:0': 28, '2:0': 16, '2:1': 26, '1:1': 38, '0:0': 18, '0:1': 20,
               '1:2': 14, '2:2': 16, '3:1': 10, '0:2': 10, '3:0': 8,
               '3:2': 8, '2:3': 6, '4:1': 3, '1:3': 6, '0:3': 4, '3:3': 2,
               '__n': 233})          # t_mean=2.39（λ 护栏可通过的拟真分布）

CRS_KEYS = frozenset(f'{h}:{a}' for h in range(4) for a in range(4))   # 16 精确键
# 池 fixture 必须照真实体彩键格式造（'s01s00' sXXsYY 编码——臆造 'h:a' 键会让测试
# 全绿而真实数据 crs 恒空的教训，2026-09-08 实证）；嵌套推导字典序确定（frozenset
# 迭代序随哈希种子漂移，会晃动并列 q 的稳定排序断言）
CRS_POOL = {f's{h:02d}s{a:02d}': 8.0 for h in range(4) for a in range(4)}
CRS_POOL.update({'s1sh': 50.0, 's1sd': 60.0, 's1sa': 70.0})   # 方向"其他"键无模型 q
TTG_POOL = {f's{i}': float(3 + i) for i in range(8)}
DATE = '2026-09-08'


def _match(code, league='瑞超', crs=True, ttg=True):
    return {'code': code, 'matchNumStr': code, 'home': '主队', 'away': '客队',
            'league': league, 'matchDate': DATE,
            'crs': dict(CRS_POOL) if crs else None,
            'ttg': dict(TTG_POOL) if ttg else None}


def _snap(tmp_path, matches, date=DATE, **kw):
    base = dict(freq_table={'sweden': TPL}, form={}, zh={}, out_dir=str(tmp_path))
    base.update(kw)
    return freq_snap.snap(date, matches=matches, **base)


def test_pure_template_ranking(tmp_path):
    """空 form → λ=None 纯模板：q=c/n 手算锁定；其他键被滤；ttg 档聚合降序。"""
    qc = _snap(tmp_path, [_match('周二001')])
    crs = qc['周二001']['crs']
    assert crs == sorted(crs, key=lambda kv: -kv[1])          # 降序
    assert all(k in CRS_KEYS for k, _ in crs)                 # '胜其他'等无模型 q 永不产出
    assert crs[0] == ['1:1', round(38 / 233, 4)]              # 38 唯一最大
    assert [crs[i][0] for i in (1, 2, 3, 4)] == ['1:0', '2:1', '0:1', '0:0']
    assert '4:1' not in {k for k, _ in crs}                   # 池外键被价格闸门滤除
    ttg = qc['周二001']['ttg']
    assert ttg == sorted(ttg, key=lambda kv: -kv[1])
    # 桶=按 h+a 分桶（1:1∈s2、3:2/2:3∈s5；Σ=233 全收）：s2=2:0(16)+1:1(38)+0:2(10)
    # _ranked 输出的 q 已是归一化概率（勿再除 n——0.2747/233=0.0012 的教训）
    assert [[p, q] for p, q in ttg] == [
        ['s2', round(64 / 233, 4)], ['s3', round(52 / 233, 4)],
        ['s1', round(48 / 233, 4)], ['s4', round(32 / 233, 4)],
        ['s0', round(18 / 233, 4)], ['s5', round(17 / 233, 4)],
        ['s6', round(2 / 233, 4)]]                      # s7=0 被滤


def test_lambda_shift_lifts_home_scores(tmp_path):
    """强主弱客近况 → λ 平移生效：主胜向比分(1:0/0:1)相对概率高于纯模板。"""
    lam = lambdas(league_base_rates(TPL), (1.2, 0.8), (1.0, 1.4))
    assert lam is not None and lam[0] > lam[1]                # T 轴护栏通过（13%<40%）
    season = CURRENT_SEASON
    form = {'test-home': [(2, 0, season, 'sweden'), (1, 1, season, 'sweden'),
                          (2, 1, season, 'sweden'), (1, 1, season, 'sweden'),
                          (0, 1, season, 'sweden')],
            'test-away': [(1, 2, season, 'sweden'), (2, 1, season, 'sweden'),
                          (1, 1, season, 'sweden'), (0, 1, season, 'sweden'),
                          (1, 2, season, 'sweden')]}
    zh = {'主队': 'test-home', '客队': 'test-away'}
    qc = _snap(tmp_path, [_match('周二002')], form=form, zh=zh)
    got = {p: q for p, q in qc['周二002']['crs']}
    pure = shifted_q(TPL, None)
    assert got['1:0'] / got['0:1'] > pure['1:0'] / pure['0:1']


def test_pools_gate(tmp_path):
    """价格闸门：池缺失的 kind 不出条目；两池全缺 → 整场无条目且不落盘。"""
    qc = _snap(tmp_path, [_match('周二003', ttg=False)])
    assert 'ttg' not in qc['周二003'] and 'crs' in qc['周二003']
    sub = tmp_path / 'gate'
    qc2 = _snap(sub, [_match('周二004', crs=False, ttg=False)])
    assert qc2 is None and not sub.exists()                   # 零可用条目不落盘


def test_global_pool_fallback(tmp_path):
    """无联赛映射（欧冠）→ global_pool 纯模板兜底，q 与原语直算一致（pools_card 口径）。
    断言顺序无关（dict 比较）：_ranked 输入序=池键序 vs 原语=q_map 序，并列 q 组内序
    两链天然不同，逐位比较会脆。"""
    ft = {'sweden': TPL, 'japan': Counter({'0:0': 50, '1:1': 40, '1:0': 20, '__n': 150})}
    qc = _snap(tmp_path, [_match('周二005', league='欧冠')], freq_table=ft)
    # _ranked 输出 round(q,4)，want 同口径 round4 才能逐位对上
    want = {p: round(q, 4) for p, q in shifted_q(global_pool(ft), None).items()
            if p in CRS_KEYS}
    assert dict(qc['周二005']['crs']) == want
    picks = [p for p, _ in qc['周二005']['crs']]
    # 并列 q 组内序两链天然不同（got 按池键序 vs 原语按计数键序），只断非升序性质
    assert all(want[a] >= want[b] for a, b in zip(picks, picks[1:]))


def test_freeze_idempotent_and_zero_match(tmp_path):
    """冻结纪律：文件在 → 幂等返回不重算；--force 才重写；零场次不落盘。"""
    qpath = tmp_path / f'{DATE}.json'
    qpath.write_text(json.dumps({'哨兵': 1}, ensure_ascii=False), encoding='utf-8')
    got = _snap(tmp_path, [_match('周二006')])
    assert got == {'哨兵': 1}
    assert json.loads(qpath.read_text(encoding='utf-8')) == {'哨兵': 1}
    forced = _snap(tmp_path, [_match('周二006')], force=True)
    assert '周二006' in forced and json.loads(qpath.read_text(encoding='utf-8')) == forced
    zero = tmp_path / 'zero'
    assert _snap(zero, [_match('周二007')], date='2026-09-09') is None
    assert not zero.exists()


def _leg(i, fuse_h):
    """shadow_all 口径假腿：fused 主概率降序互异（strong 选腿确定）+ 体彩两池价。"""
    return {'code': f'周二{i:03d}', 'match': f'主{i} vs 客{i}', 'league': '瑞超',
            'fused': [fuse_h, 0.2, 1.0 - fuse_h - 0.2],
            'odds': [round(1 / fuse_h * 1.1, 2), round(1 / 0.2 * 1.1, 2),
                     round(1 / (1.0 - fuse_h - 0.2) * 1.1, 2)],
            'pools': {'crs': dict(CRS_POOL), 'ttg': dict(TTG_POOL)}}


def test_end_to_end_c_d_specs_non_none(tmp_path, monkeypatch):
    """端到端：snap 产出 → load_qcache → C 族×3 + D 族×3 全部建票成功（恢复铁证）。"""
    legs = [_leg(i, 0.75 - i * 0.045) for i in range(8)]
    _snap(tmp_path, [_match(l['code']) for l in legs])
    monkeypatch.setattr(shapes, 'QCACHE_DIR', str(tmp_path))
    qc = shapes.load_qcache(DATE)
    assert qc and len(qc) == 8
    c_d = [s for s in shapes.PLAN_FAMILIES if s['family'] in ('C', 'D')]
    assert len(c_d) == 6
    for spec in c_d:
        tk = shapes.build_ticket(legs, spec, qc=qc)
        assert tk is not None, f"{spec['name']} 仍被跳过"
        assert tk['tlegs'] and tk['bets']
