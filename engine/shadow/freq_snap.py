#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""freq_snap.py — 影子层 qcache 快照生成器（2026-09-08 重建 · 2026-10-13 换代 V3W）。

【链代断代】大哥拍板（2026-10-12 回溯机制四件套+切换）：
  - v1（2026-09-08~2026-10-12）：freq_band λ平移链（联赛模板+球队平移）——已被生产端
    09-26/10-06 两度换代甩开，影子测死链、对照意义流失（当日实证：影子 crs 票连中
    出自旧链而非新模型）。链籍 engine/cache/chain_registry.json shadow_qcache。
  - v2（2026-10-13 起）：V3W-v2 实力链（strength_chain_eval._predict_match·HST 链+
    去水 v2·与生产比分端同源）——影子票回归"系统当天实际会出的候选"。
  - --engine=legacy 保留对照（跑法：python freq_snap.py <date> --engine legacy --force
    另存对照；勿覆盖冻结快照——默认路径文件在即返回的冻结语义不变）。

产出契约（shapes.load_qcache/_qc_options 消费面，不得发明）：
  {code: {'ttg': [[pick, q], ...] 降序, 'crs': [[pick, q], ...] 降序}}
  - ttg pick='s0'~'s7'（体彩 8 键·引擎 9 档的 s8 开桶并入 s7）；crs pick='h:a' 精确键
    （体彩池键 's01s00' 换算，'胜其他'等方向键无模型 q 永不产出）；
  - q 仅用于排序，赔率由 shadow_all 从体彩当刻池价冻结；
  - 顶层只允许 code 键，禁元数据键；价格闸门：该场池缺失 → 该 kind 不出条目；
  - V3W 覆盖=30 生产联赛队名映射，映射不到/无联赛 → 不出条目（诚实跳过，D/C 族
    该场缺候选时整票关档并打印原因——覆盖较 v1 缩水属已知代价，设计档已录）；
  - 冻结：文件缺失才生成，--force 才重写（赛前快照语义）；当日零可用场次不落盘。
开发者 sszhang
"""
import json
import os
import re
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))                # engine/shadow → 仓库根
SCRIPTS = os.path.join(REPO, 'engine', 'scripts')
QCACHE_DIR = os.path.join(HERE, 'qcache')
MATCHES_PATH = os.path.join(REPO, 'engine', 'cache', 'sporttery_matches.json')

if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

ENGINE_V3W = 'v3w'
ENGINE_LEGACY = 'legacy'


def _zh_map() -> dict:
    """_aliases.json → {中文队名: 规范tid}（common.load_aliases 平铺口径）。"""
    from common import load_aliases
    out = {}
    for tid, srcs in load_aliases().items():
        if not isinstance(srcs, dict):
            continue
        for v in srcs.get("variants") or []:
            out[v] = tid
        if srcs.get("zh"):
            out[srcs["zh"]] = tid   # 主名后写，冲突时优先
    return out


# ── v1 旧链（legacy 对照保留，逻辑不动）──────────────────────────
def _q_map_for(m, freq_table, form, zh, pool) -> dict:
    """逐场平移链（boldplay._q_map_for 同链复刻）：map_league→联赛模板→base rates
    →λ→shifted_q。team_strength 传联赛 ID 启用上季全季先验。"""
    from freq_band import (lambdas, league_base_rates, shifted_q, team_strength, _norm)
    from score_ev import map_league
    lg = map_league(m.get('league', ''))
    blob = freq_table.get(lg) if lg else None
    if not (blob and blob.get('__n', 0)):
        blob, lg = pool, None
    if not blob.get('__n', 0):
        return {}                               # 全局池也空 → 无数据不产条目
    if lg is None:
        lam = None
    else:
        lam = lambdas(league_base_rates(blob),
                      team_strength(form, _norm(zh.get(m.get('home', ''), '')), lg),
                      team_strength(form, _norm(zh.get(m.get('away', ''), '')), lg))
    return shifted_q(blob, lam)


_CRS_RE = re.compile(r'^s(\d{2})s(\d{2})$')


def _crs_pick(pool_key):
    """体彩比分池键 's01s00' → 模型 q 键 '1:0'；'胜其他'等方向键返回 None。"""
    mo = _CRS_RE.match(pool_key)
    return f'{int(mo.group(1))}:{int(mo.group(2))}' if mo else None


def _ranked(items) -> list:
    """[(pick, q)] → [[pick, round(q,4)]] 过滤 q<=0 降序（qcache 契约格式）。"""
    rows = [(p, q) for p, q in items if q > 0]
    rows.sort(key=lambda kv: -kv[1])
    return [[p, round(q, 4)] for p, q in rows]


# ── v2 V3W 实力链（2026-10-13 起默认）────────────────────────────
def _v3w_qs(ms, date_str, zh, predict_fn=None):
    """逐场 V3W → {code: {'crs_q': {'h:a': q}, 'ttg_q': {'s0'..'s7': q}}}。
    predict_fn 缺省=strength_chain_eval._predict_match（测试可注入 fake）。"""
    import strength_chain_prod as scp
    import strength_chain_eval as sce
    from strength_loaders import build_ctx
    pred = predict_fn or sce._predict_match
    ctx = build_ctx(scp.LEAGUES)                # 生产同源 30 联赛
    memo = {}
    out = {}
    as_of = date.fromisoformat(date_str)
    for m in ms:
        hid, aid = zh.get(m.get('home', '')), zh.get(m.get('away', ''))
        if not (hid and aid):
            continue                            # 映射不到 → 不出条目（诚实跳过）
        try:
            t = pred(hid, aid, as_of, ctx, memo, beta=0.05)
        except Exception:
            continue                            # 单场失败不炸整快照（fail-open）
        if not t or not t.get('matrix'):
            continue
        matrix = t['matrix']                    # 39 键 's{i:02d}s{j:02d}' + 三方向其他
        crs_q = {}
        for pk in map(_crs_pick, (m.get('crs') or {})):
            if not pk:
                continue
            h, a = pk.split(':')
            crs_q[pk] = matrix.get(f's{int(h):02d}s{int(a):02d}', 0.0)
        ttg_raw = dict(t.get('ttg') or {})      # s0..s8（9 档）
        ttg_raw['s7'] = ttg_raw.get('s7', 0.0) + ttg_raw.pop('s8', 0.0)   # s8 开桶并入 s7
        out[m['code']] = {'crs_q': crs_q, 'ttg_q': ttg_raw}
    return out


def snap(date_str=None, *, matches=None, matches_path=None, freq_table=None,
         form=None, zh=None, force=False, out_dir=None,
         engine=ENGINE_V3W, predict_fn=None):
    """当日体彩销售日场次 → qcache/{date}.json（缺失才写，返回 qc dict or None）。
    engine='v3w'（默认·2026-10-13 起链代）| 'legacy'（freq_band 对照）。
    依赖注入：matches/freq_table/form/zh（legacy）；predict_fn（v3w·测试隔离）。"""
    date_str = date_str or str(date.today())
    qpath = os.path.join(out_dir or QCACHE_DIR, f'{date_str}.json')
    if not force and os.path.exists(qpath):
        try:
            with open(qpath, encoding='utf-8') as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            pass                                # 损坏快照自愈：当缺失重建
    if matches is None:
        try:
            with open(matches_path or MATCHES_PATH, encoding='utf-8') as fh:
                matches = json.load(fh).get('matches', [])
        except (OSError, json.JSONDecodeError):
            return None
    # matchDate 兼容两种体彩格式：纯 'YYYY-MM-DD' / 全 'YYYY-MM-DD HH:MM:SS'（盘中刷新后
    # 观测到全格式，精确 == 会空转——10-12 冒烟实证）；[:10] 截断两格式通吃
    ms = [m for m in matches if str(m.get('matchDate') or '')[:10] == date_str and m.get('code')]
    if not ms:
        return None
    zh = zh if zh is not None else _zh_map()
    # 按引擎产逐场 q 源
    if engine == ENGINE_V3W:
        qs = _v3w_qs(ms, date_str, zh, predict_fn=predict_fn)
    else:
        from freq_band import build_team_form, global_pool, ttg_agg
        from score_ev import build_freq_table
        freq_table = freq_table if freq_table is not None else build_freq_table()
        form = form if form is not None else build_team_form()
        pool = global_pool(freq_table)
        qs = {}
        for m in ms:
            q_map = _q_map_for(m, freq_table, form, zh, pool)
            if q_map:
                qs[m['code']] = {'crs_q': q_map,
                                 'ttg_q': ttg_agg(q_map)}
    # 共用落盘：池键交集 + 价格闸门 + 降序
    qc = {}
    for m in ms:
        src = qs.get(m['code'])
        if not src:
            continue
        entry = {}
        crs_pool = m.get('crs') or {}
        ttg_pool = m.get('ttg') or {}
        if crs_pool:
            rows = _ranked((k, src['crs_q'].get(k, 0.0))
                           for k in (p for p in map(_crs_pick, crs_pool) if p))
            if rows:
                entry['crs'] = rows
        if ttg_pool:
            rows = _ranked((k, src['ttg_q'].get(k, 0.0)) for k in ttg_pool)
            if rows:
                entry['ttg'] = rows
        if entry:
            qc[m['code']] = entry
    if not qc:
        return None                             # 零可用场次不落盘（防 {} 卡死重生成）
    os.makedirs(os.path.dirname(qpath), exist_ok=True)
    tmp = qpath + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(qc, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, qpath)                      # 原子写（paper._save 同纪律）
    return qc


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='影子层 qcache 快照生成器（v3w 默认·legacy 对照）')
    ap.add_argument('date', nargs='?', default=None, help='体彩销售日 2026-MM-DD（默认今天）')
    ap.add_argument('--force', action='store_true', help='已有快照也重算重写（慎用·冻结语义）')
    ap.add_argument('--engine', choices=(ENGINE_V3W, ENGINE_LEGACY), default=ENGINE_V3W,
                    help='v3w=V3W-v2 实力链（默认）| legacy=freq_band 对照')
    ap.add_argument('--out-suffix', default=None,
                    help='对照输出后缀（如 legacy → qcache/{date}.legacy.json，防覆盖冻结快照）')
    a = ap.parse_args()
    out_dir = QCACHE_DIR
    if a.out_suffix:
        # 对照另存：临时改目录内文件名约定——out_dir 不支持后缀，借 force+专用目录实现
        out_dir = os.path.join(QCACHE_DIR, f'_compare_{a.out_suffix}')
        os.makedirs(out_dir, exist_ok=True)
    qc = snap(a.date, force=a.force, engine=a.engine, out_dir=out_dir)
    if qc is None:
        print(json.dumps({'error': f'当日({a.date or date.today()})无可用场次/候选，未落盘'},
                         ensure_ascii=False))
    else:
        print(json.dumps({'date': a.date or str(date.today()), 'engine': a.engine,
                          'codes': len(qc),
                          'ttg_n': sum('ttg' in v for v in qc.values()),
                          'crs_n': sum('crs' in v for v in qc.values())},
                         ensure_ascii=False))
