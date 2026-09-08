#!/usr/bin/env python3
"""freq_snap.py — 影子层 qcache 快照生成器（2026-09-08 重建版）。

原版随 scratch/replay_v2 清理误删（未进 git），本版重建使 shapes 19 spec 中
C 族（ttg×3）+ D 族（crs×3）恢复影子票登记。重建口径声明（与原版回放规格
docs/superpowers/plans/2026-09-05-complex-ticket-test.md Task 3 的已知差异）：
- q 口径升级为与 boldplay 主链同源的 λ 平移链（_q_map_for 复刻·非原版纯频率）：
  大哥要影子观察的是"系统当天实际出的比分预测"，pick 必须与主链 pools_card
  同口径才有对照意义；
- 不做原版 date<before_date 截断：原版为回放历史轮防泄漏设计；活水登记场景
  本地库只含已回填历史（回填在赛后才跑），整库=自然截断，无泄漏；
- 位置 engine/shadow/qcache/ 纳管 git（09-07 scratch 事故教训：持久生成器与
  凭证快照禁放 scratch）。

产出契约（shapes.load_qcache/_qc_options 消费面，不得发明）：
  {code: {'ttg': [[pick, q], ...] 降序, 'crs': [[pick, q], ...] 降序}}
  - ttg pick='s0'~'s7'；crs pick='h:a' 精确键（体彩池键 's01s00' 换算，
    's1sh/s1sd/s1sa' 等方向"其他"键无模型 q 永不产出）；
  - q 仅用于排序，赔率由 shadow_all 从体彩当刻池价冻结；
  - 顶层只允许 code 键（load_qcache 整文件当 qc dict 消费），禁元数据键；
  - 价格闸门：该场池缺失（crs/ttg 键为空）→ 该 kind 不出条目（无价不成票）；
  - 冻结：文件缺失才生成，--force 才重写（赛前快照语义）；当日零可用场次
    不落盘（空 {} 会让 build_ticket 的 not qc 恒真且 shadow_all 的
    qc is None 判断失效，卡死重生成）。
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

from freq_band import (build_team_form, global_pool, league_base_rates,
                       lambdas, shifted_q, team_strength, ttg_agg, _norm)
from score_ev import build_freq_table, map_league


def _zh_map() -> dict:
    """_aliases.json → {中文队名: 规范tid}（common.load_aliases 平铺口径，
    与 boldplay._zh_map 同款自实现——避免 import 1300 行 boldplay CLI 模块的耦合）。"""
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


def _q_map_for(m, freq_table, form, zh, pool) -> dict:
    """逐场平移链（boldplay._q_map_for 同链复刻）：map_league→联赛模板→base rates
    →λ→shifted_q。与主链 pools_card 一致的降级口径：无联赛模板/模板空 → 全局池
    纯模板（lam=None，跨联赛混合基准无意义；freq_legs 的全局池带 λ 不采）。
    team_strength 传联赛 ID 启用上季全季先验（2026-09-04 拍板·开季段判断力恢复）。"""
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
    """体彩比分池键 's01s00' → 模型 q 键 '1:0'（paper.shadow_all 冻结池价同款换算）；
    's1sh/s1sd/s1sa' 等方向"其他"键返回 None（无模型 q，永不产出）。"""
    mo = _CRS_RE.match(pool_key)
    return f'{int(mo.group(1))}:{int(mo.group(2))}' if mo else None


def _ranked(items) -> list:
    """[(pick, q)] → [[pick, round(q,4)]] 过滤 q<=0 降序（qcache 契约格式）。"""
    rows = [(p, q) for p, q in items if q > 0]
    rows.sort(key=lambda kv: -kv[1])
    return [[p, round(q, 4)] for p, q in rows]


def snap(date_str=None, *, matches=None, matches_path=None, freq_table=None,
         form=None, zh=None, force=False, out_dir=None):
    """当日体彩销售日场次 → qcache/{date}.json（缺失才写，返回 qc dict or None）。

    依赖注入（build_team_form 的 fetch_rows_fn 同款模式，测试隔离网络）：
    matches=场次列表 / freq_table / form / zh；缺省走真实源（sporttery_matches.json
    + fd 多季频率模板 + 本地库镜像 + 别名表）。零可用场次返回 None 不落盘。
    """
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
    ms = [m for m in matches if m.get('matchDate') == date_str and m.get('code')]
    if not ms:
        return None
    freq_table = freq_table if freq_table is not None else build_freq_table()
    form = form if form is not None else build_team_form()
    zh = zh if zh is not None else _zh_map()
    pool = global_pool(freq_table)
    qc = {}
    for m in ms:
        q_map = _q_map_for(m, freq_table, form, zh, pool)
        if not q_map:
            continue
        entry = {}
        crs_pool = m.get('crs') or {}
        ttg_pool = m.get('ttg') or {}
        if crs_pool:                            # 价格闸门：池缺 → 该 kind 不出条目
            # 池键 s01s00 → 1:0 换算后查 q_map（真实体彩池键非 'h:a'，2026-09-08 实证）
            rows = _ranked((k, q_map.get(k, 0.0))
                           for k in (p for p in map(_crs_pick, crs_pool) if p))
            if rows:                            # 换算后与模型 q 无交集 → 不出条目
                entry['crs'] = rows
        if ttg_pool:
            ttg_q = ttg_agg(q_map)
            rows = _ranked((k, ttg_q.get(k, 0.0)) for k in ttg_pool)
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
    ap = argparse.ArgumentParser(description='影子层 qcache 快照生成器（freq 平移链）')
    ap.add_argument('date', nargs='?', default=None, help='体彩销售日 2026-MM-DD（默认今天）')
    ap.add_argument('--force', action='store_true', help='已有快照也重算重写')
    a = ap.parse_args()
    qc = snap(a.date, force=a.force)
    if qc is None:
        print(json.dumps({'error': f'当日({a.date or date.today()})无可用场次/模板，未落盘'},
                         ensure_ascii=False))
    else:
        print(json.dumps({'date': a.date or str(date.today()), 'codes': len(qc),
                          'ttg_n': sum('ttg' in v for v in qc.values()),
                          'crs_n': sum('crs' in v for v in qc.values())},
                         ensure_ascii=False))
