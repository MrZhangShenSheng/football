# 比分端低分偏置修正 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 消除比分端 −1.62 球的系统性低分偏置，使「与市场偏离」和 EV 判据恢复可信。

**Architecture:** 六项改动分两类——(a) 频率池数据层：纳入 8 个被排除库（13229 场）、补全赛事映射、重测生存阈；(b) 分布生成层：矩阵维度统一 7×7、rho 拟合边界放宽、新增分维度锚定（TTG 锚市场、比分形状保留独立判断）。数据层先行，生成层依赖之。

**Tech Stack:** Python 3.14 / numpy / scipy.optimize(L-BFGS-B) / pytest。无新依赖。

**设计依据:** `docs/plans/2026-10-14-crs-low-score-bias-design.md`（判据见其第六节，已冻结）

---

## Pre-flight 冲突扫描

| 任务对 | 共享文件 | Produces → Consumes | 结果 |
|:---|:---|:---|:---|
| T1→T2 | `score_ev.py` | 新池键（uefa-champions 等）→ LEAGUE_MAP 映射目标 | **冲突 R1** |
| T1→T3 | `score_ev.py` + `freq_band.py` | 池样本量变化 → 生存阈剔除量重测 | OK，T3 纯读 T1 产物 |
| T1,T2→T6 | 池分布 | 池期望 → 锚定目标计算 | OK，顺序依赖 |
| T4→T6 | `goal_engine.py`/`dc_predict.py` | 矩阵维度 → 锚定作用的分布 | OK，T4 先行 |
| T5→T4 | `dc_fit.py` → `dc_predict.py` | rho 新值 → tau 计算 | **冲突 R2** |
| T2,T3 | `freq_band.py` | 两任务同改一文件 | **冲突 R3** |
| T5→全部 | 25 个 `*_dc.json` 缓存 | 重拟合覆盖缓存 | **冲突 R4** |

**单任务自洽性**：T1 测试与改动一致 / T2 两处键名别名均给改法 / T3 依赖 T1 完成后实测 / T4 两模块同步改 / T5 自带 tau 正性断言 / T6 依赖最多、放末位。

**Ruling R1**：`score_ev.py` 文件名键与 LEAGUE_MAP 目标键存在两处不一致——库文件名 `spain-liga2`/`germany-bundesliga2`，而 LEAGUE_MAP 目标 `spain-laliga2`/`germany-2-bundesliga`。T1 进池时**保留库文件名原样为键**，T2 在 LEAGUE_MAP 侧加别名表 `POOL_ALIAS` 做转换，不改库文件名。理由：改文件名会牵动 DC 缓存与 espn-history 镜像链路，别名表改动面最小。若判断错：T2 的映射测试会抓到键名对不上。

**Ruling R2**：T5 必须在 T4 之前执行。理由：T4 统一矩阵维度后要跑回归验证分布正确，而 rho 若仍撞边界则 T4 的验证基线本身是错的。若判断错：T4 需重跑验证，无数据损失。

**Ruling R3**：T2 与 T3 严格顺序执行不并行，各自用自己的 BASE。理由：两者同改 `freq_band.py` 不同区段（T2 改 map/alias、T3 改 SURVIVE_Q 常量），顺序执行则无冲突。

**Ruling R4**：T5 重拟合前**必须备份** `engine/cache/*_dc.json` 全部 25 个文件到 `engine/cache/_dc_backup_2026-10-14/`。理由：重拟合不可逆，若 tau 正性断言在某联赛失败需回退该联赛旧参数。若判断错：无，纯保险措施。

**执行顺序（Ruling 生效后）**：T1 → T2 → T3 → T5 → T4 → T6 → T7(验收)

---

## Task 1: 频率池按样本量准入（删除杯赛排除）

**Files:**
- Modify: `engine/scripts/score_ev.py:31-35`（LOCAL_POOL 注释与常量区）
- Modify: `engine/scripts/score_ev.py:57-61`（准入分支）
- Test: `engine/tests/test_score_ev_pool.py`（新建）

**Step 1: 写失败测试**

```python
# engine/tests/test_score_ev_pool.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from score_ev import build_freq_table


def _pool_expect(blob):
    n = blob["__n"]
    return sum((c / n) * sum(int(x) for x in s.split(":"))
               for s, c in blob.items() if s != "__n")


def test_uefa_champions_enters_pool():
    """欧冠 1043 场须独立进池，期望 ~3.22（此前被 '杯赛不进池' 排除）。"""
    table = build_freq_table()
    assert "uefa-champions" in table, "欧冠库未进频率池"
    blob = table["uefa-champions"]
    assert blob["__n"] >= 1000, f"欧冠样本 {blob['__n']} 不足"
    assert 3.0 <= _pool_expect(blob) <= 3.45


def test_large_excluded_leagues_enter_pool():
    """8 个样本>=800 的被排除库全部进池。"""
    table = build_freq_table()
    for key in ("spain-liga2", "germany-bundesliga2", "belgium-first-a",
                "turkey-super-lig", "greece-super", "SC0",
                "uefa-champions", "uefa-europa"):
        assert key in table, f"{key} 未进池"
        assert table[key]["__n"] >= 800, f"{key} 样本不足 800"


def test_tiny_cup_libraries_still_excluded():
    """样本<800 的小库仍不进池（libertadores 9 场 / coppa-italia 32 场）。"""
    table = build_freq_table()
    for key in ("libertadores", "coppa-italia", "dfb-pokal", "asian-u23"):
        assert key not in table, f"{key} 样本不足却进池"


def test_total_pool_size_grew():
    """池总量须从 28438 增至约 41667（+13229）。"""
    table = build_freq_table()
    total = sum(b.get("__n", 0) for b in table.values())
    assert total >= 40000, f"池总量 {total} 未达预期"
```

**Step 2: 运行测试确认失败**

Run: `cd engine && py -m pytest tests/test_score_ev_pool.py -v`
Expected: FAIL — `test_uefa_champions_enters_pool` 报 "欧冠库未进频率池"；`test_tiny_cup_libraries_still_excluded` 可能先 PASS（当前全排除）

**Step 3: 实现最小改动**

`score_ev.py:31-35` 常量区，替换原注释并新增阈值：

```python
# 本地赛果库准入（2026-10-14 低分偏置修正）：按样本量准入替代原「杯赛库不进频率池」。
# 原设计排除杯赛因「跨联赛队源混杂」，但欧冠 1043 场实测期望 3.221，而被迫套全局池
# 2.738 正是 −0.49 球主犯（见 docs/plans/2026-10-14-crs-low-score-bias-design.md）。
# 样本>=800 即独立成池；不足者由调用方回退地域近似池，不落全局平均。
POOL_MIN_N = 800
LOCAL_POOL = ("japan", "korea", "sweden", "saudi", "brazil", "denmark", "norway", "usa")
FD_BACKED = frozenset(DIVS.values())   # fd 覆盖联赛键集（镜像库仅 fd 断粮时导入防双算）
```

`score_ev.py:57-61` 准入分支，改为：

```python
        if key in FD_BACKED:
            if (table.get(key) or Counter()).get("__n", 0):
                continue                               # fd 活着 → 跳过镜像防双算
        elif key not in LOCAL_POOL:
            rows_n = _count_rows(path)
            if rows_n < POOL_MIN_N:
                continue                               # 样本不足 → 不成池（调用方回退近似池）
```

并在 `build_freq_table` 之前新增辅助函数：

```python
def _count_rows(path: str) -> int:
    """库文件场次数（准入判定用，避免重复解析整表）。"""
    try:
        data = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    rows = data.get("matches", []) if isinstance(data, dict) else (data or [])
    return len(rows)
```

**Step 4: 运行测试确认通过**

Run: `cd engine && py -m pytest tests/test_score_ev_pool.py -v`
Expected: 4 passed

**Step 5: 回归全量**

Run: `cd engine && py -m pytest tests/ -q`
Expected: 332 passed（不减少）。若有失败，大概率是依赖池总量的旧断言——记录failing测试名，不要擅自改断言，报告后再定。

**Step 6: 验证池期望缺口判据**

Run:
```bash
cd engine/scripts && py -c "import sys; sys.path.insert(0,'.'); from score_ev import build_freq_table; import freq_band as fb; ft=build_freq_table(); p=fb.global_pool(ft); n=p['__n']; tot=lambda s: sum(int(x) for x in s.split(':')); print('池总量', n); print('全局池期望 %.3f' % sum((c/n)*tot(s) for s,c in p.items() if s!='__n'))"
```
Expected: 池总量 >= 40000；全局池期望上升（原 2.738）

**Step 7: Commit**

```bash
git add engine/scripts/score_ev.py engine/tests/test_score_ev_pool.py
git commit -m "feat(pool): 频率池按样本量准入，纳入欧冠等8库13229场"
```

---

## Task 2: 赛事映射表补全

**Files:**
- Modify: `engine/scripts/score_ev.py:8-23`（LEAGUE_MAP 与 map_league）
- Test: `engine/tests/test_score_ev_pool.py`（追加）

**Step 1: 写失败测试**

```python
# 追加到 engine/tests/test_score_ev_pool.py
from score_ev import map_league, resolve_pool_key


def test_uefa_champions_mapped():
    """欧冠须映射到自己的池，不再返回 None 落全局池。"""
    assert map_league("欧冠") == "uefa-champions"


def test_cup_fallback_to_regional_pool():
    """样本不足的杯赛回退地域近似池，而非全局池。"""
    table = build_freq_table()
    assert resolve_pool_key("解放者杯", table) == "brazil"
    assert resolve_pool_key("亚冠精英", table) in ("japan", "korea")


def test_pool_alias_resolves_keyname_mismatch():
    """库文件名与 LEAGUE_MAP 目标键的两处不一致须由别名表消解（Ruling R1）。"""
    table = build_freq_table()
    assert resolve_pool_key("西乙", table) in table
    assert resolve_pool_key("德乙", table) in table


def test_unmapped_still_returns_none():
    """未知联赛仍返回 None（调用方落全局池，行为不变）。"""
    assert map_league("火星联赛") is None
```

**Step 2: 运行测试确认失败**

Run: `cd engine && py -m pytest tests/test_score_ev_pool.py -k "mapped or fallback or alias or unmapped" -v`
Expected: FAIL — `ImportError: cannot import name 'resolve_pool_key'`

**Step 3: 实现**

`score_ev.py` LEAGUE_MAP 字典内追加（第 18 行 `"巴甲"` 那行之后）：

```python
    # 10-14 低分偏置修正：杯赛映射（此前返回 None → 全局池 2.738，欧冠实测 3.221 差 0.48）
    "欧冠": "uefa-champions", "欧联": "uefa-europa", "欧罗巴": "uefa-europa",
```

并把第 19 行注释改为：

```python
}                       # 无对应 → None → 调用方 resolve_pool_key 回退近似池
```

`map_league` 之后新增别名表与解析函数：

```python
# 库文件名 → 频率池键的别名（Ruling R1：不改库文件名，在此消解两处不一致）
POOL_ALIAS = {"spain-laliga2": "spain-liga2", "germany-2-bundesliga": "germany-bundesliga2"}

# 样本不足独立成池的赛事 → 地域近似池（优先级降序，取首个在表中者）
POOL_FALLBACK = {
    "解放者杯": ("brazil",),
    "亚冠精英": ("japan", "korea"),
    "亚冠乙": ("japan", "korea"),
    "日联赛杯": ("japan",),
}


def resolve_pool_key(name: str, table: dict):
    """体彩联赛名 → 实际可用的频率池键。

    顺序：LEAGUE_MAP 直接命中（经 POOL_ALIAS 消解键名差异）→ POOL_FALLBACK 地域近似
    → None（调用方落全局池）。回退近似池而非全局平均：今日 013 解放者杯市场期望 2.22,
    巴西池 2.489 比全局 2.738 近得多。
    """
    key = LEAGUE_MAP.get(name)
    if key:
        key = POOL_ALIAS.get(key, key)
        if key in table:
            return key
    for cand in POOL_FALLBACK.get(name, ()):
        if cand in table:
            return cand
    return None
```

**Step 4: 运行测试确认通过**

Run: `cd engine && py -m pytest tests/test_score_ev_pool.py -v`
Expected: 8 passed

**Step 5: 验证当日 14 场缺口判据**

Run:
```bash
cd engine/scripts && py -c "
import sys, json, statistics as st; sys.path.insert(0,'.')
from score_ev import build_freq_table, resolve_pool_key
import freq_band as fb
ft = build_freq_table(); pool = fb.global_pool(ft)
tot = lambda s: sum(int(x) for x in s.split(':'))
pe = lambda b: sum((c/b['__n'])*tot(s) for s,c in b.items() if s!='__n')
sp = json.load(open('../cache/sporttery_matches.json', encoding='utf-8'))
gaps = []
for m in sp['matches']:
    k = resolve_pool_key(m.get('league',''), ft)
    e = pe(ft[k]) if k else pe(pool)
    ttg = {kk: float(v) for kk,v in (m.get('ttg') or {}).items()}
    if not ttg: continue
    t = sum(1/v for v in ttg.values())
    mexp = sum((7 if kk=='s7' else int(kk[1:]))*((1/ttg[kk])/t) for kk in ttg)
    gaps.append(e-mexp)
    print('%s %-10s pool=%-20s %.2f vs mkt %.2f  %+.2f' % (m['code'], m['league'], str(k)[:20], e, mexp, e-mexp))
print('缺口均值 %+.3f (判据 |均值| <= 0.20)' % st.mean(gaps))
"
```
Expected: 缺口均值 |x| <= 0.20（原 −0.49）

**Step 6: Commit**

```bash
git add engine/scripts/score_ev.py engine/tests/test_score_ev_pool.py
git commit -m "feat(pool): 补全杯赛映射与地域回退，消除全局池错配"
```

---

## Task 3: 生存阈重测与调整

**Files:**
- Modify: `engine/scripts/freq_band.py:15`（SURVIVE_Q，仅在重测确认需要时）
- Test: `engine/tests/test_freq_band_survive.py`（新建）

**Step 1: 先重测，数据说话**

Run:
```bash
cd engine/scripts && py -c "
import sys; sys.path.insert(0,'.')
import freq_band as fb
from score_ev import build_freq_table
ft = build_freq_table(); pool = fb.global_pool(ft); n = pool['__n']
tot = lambda s: sum(int(x) for x in s.split(':'))
rows = sorted(((c/n, s) for s,c in pool.items() if s!='__n'), reverse=True)
for q in (0.01, 0.005):
    surv = [r for r in rows if r[0] >= q]
    dead = [r for r in rows if r[0] < q]
    w = sum(p for p,s in surv)
    print('阈 %.1f%%: 存活 %d 格 覆盖 %.1f%% 存活池期望 %.3f | 剔 %d 格占 %.1f%%' % (
        q*100, len(surv), w*100, sum(p*tot(s) for p,s in surv)/w, len(dead), (1-w)*100))
print('全池期望 %.3f' % sum(p*tot(s) for p,s in rows))
"
```

**Step 2: 判定**

若 1% 阈下「存活池期望 vs 全池期望」差值已 <= 0.10 球 → **本任务跳过，SURVIVE_Q 不改**，在计划中记录实测数据与跳过理由。
若差值仍 > 0.10 → 继续 Step 3 降阈。

T1 纳入 13229 场后部分格子频率会自然超阈，此判定须用实测而非设计阶段的旧数（旧数：1% 剔 43 格占 7.7%，期望差 0.27）。

**Step 3: 写失败测试（仅在需降阈时）**

```python
# engine/tests/test_freq_band_survive.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import freq_band as fb
from score_ev import build_freq_table


def test_survive_threshold_expectation_loss_bounded():
    """生存阈造成的期望进球损失须 <= 0.10 球。"""
    pool = fb.global_pool(build_freq_table())
    n = pool["__n"]
    tot = lambda s: sum(int(x) for x in s.split(":"))
    rows = [(c / n, s) for s, c in pool.items() if s != "__n"]
    surv = [r for r in rows if r[0] >= fb.SURVIVE_Q]
    w = sum(p for p, _ in surv)
    surv_exp = sum(p * tot(s) for p, s in surv) / w
    full_exp = sum(p * tot(s) for p, s in rows)
    assert abs(surv_exp - full_exp) <= 0.10, f"损失 {full_exp - surv_exp:.3f} 球超限"


def test_high_score_cells_survive():
    """2:2/3:1/3:2/4:1 等高分格须存活（低分偏置修正的核心诉求）。"""
    pool = fb.global_pool(build_freq_table())
    n = pool["__n"]
    for s in ("2:2", "3:1", "3:2", "1:3", "2:3", "4:1"):
        assert pool[s] / n >= fb.SURVIVE_Q, f"{s} 被生存阈剔除"
```

**Step 4: 运行确认失败 → 降阈 → 确认通过**

Run: `cd engine && py -m pytest tests/test_freq_band_survive.py -v`
若 FAIL，改 `freq_band.py:15`：

```python
SURVIVE_Q = 0.005           # 生存阈值：平移后 q < 0.5% 视为噪声（10-14 低分偏置修正：
                            # 1% 阈剔 43 格占真实概率 7.7%，高分格受损最重）
```

再跑：Expected: 2 passed

**Step 5: 回归全量**

Run: `cd engine && py -m pytest tests/ -q`
Expected: 不减少

**Step 6: Commit**

```bash
git add engine/scripts/freq_band.py engine/tests/test_freq_band_survive.py
git commit -m "fix(freq-band): 生存阈重测$(若未改则写 '重测确认无需调整，补回归测试')"
```

---

## Task 4: rho 拟合边界放宽（Ruling R2：须在 T5 矩阵统一之前）

**Files:**
- Modify: `engine/scripts/dc_fit.py:164`
- Test: `engine/tests/test_dc_fit_rho.py`（新建）
- Backup: `engine/cache/_dc_backup_2026-10-14/`（Ruling R4）

**Step 1: 备份 DC 缓存（Ruling R4，不可跳过）**

Run:
```bash
cd engine/cache && mkdir -p _dc_backup_2026-10-14 && cp *_dc.json _dc_backup_2026-10-14/ && ls _dc_backup_2026-10-14 | wc -l
```
Expected: 26（25 联赛 + tonight）

**Step 2: 写失败测试**

```python
# engine/tests/test_dc_fit_rho.py
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from dc_predict import dc_tau

CACHE = Path(__file__).resolve().parents[1] / "cache"
OLD_BOUNDS = (-0.2, 0.1)


def _params():
    for p in sorted(CACHE.glob("*_dc.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        if d.get("teams") and d.get("rho") is not None:
            yield p.stem.replace("_dc", ""), d


def test_no_league_pinned_at_old_bounds():
    """放宽后不应再有联赛撞死在旧边界（此前 6/25 撞界：英超/希超/沙特=-0.2、日法德乙=+0.1）。"""
    pinned = [lg for lg, d in _params()
              if abs(d["rho"] - OLD_BOUNDS[0]) < 1e-6 or abs(d["rho"] - OLD_BOUNDS[1]) < 1e-6]
    assert not pinned, f"仍撞旧边界: {pinned}"


def test_tau_positive_for_all_leagues():
    """所有联赛在典型 lambda 下 tau 四格须为正（放宽 rho 的核心风险）。"""
    for lg, d in _params():
        for lh, la in ((1.2, 1.0), (1.8, 1.5), (2.5, 2.0)):
            for x, y in ((0, 0), (1, 0), (0, 1), (1, 1)):
                tau = dc_tau(x, y, lh, la, d["rho"])
                assert tau > 0, f"{lg} rho={d['rho']} lh={lh} la={la} ({x},{y}) tau={tau}"
```

**Step 3: 运行确认失败**

Run: `cd engine && py -m pytest tests/test_dc_fit_rho.py -v`
Expected: FAIL — `test_no_league_pinned_at_old_bounds` 列出 6 个撞界联赛

**Step 4: 放宽边界**

`dc_fit.py:164`：

```python
    # rho 域 10-14 放宽 (-0.2,0.1)→(-0.35,0.25)：原域下 6/25 联赛撞死边界
    # （英超/希超/沙特=-0.2000 整、日本/法甲/德乙=+0.1000 整），真实最优在界外。
    # tau 正性由 test_dc_fit_rho 守护；若某联赛放宽后产生负 tau 须回退该联赛旧参数。
    bounds = [(None, None)] * (2 * n) + [(0.0, 0.8), (-0.35, 0.25)]
```

**Step 5: 全联赛重拟合**

Run: `cd engine/scripts && py dc_fit.py --all 2>&1 | tail -30`
（若无 `--all` 开关，先 `py dc_fit.py --help` 查实际用法，按实际逐联赛跑）
Expected: 25 联赛全部重拟合成功，无异常退出

**Step 6: 运行测试确认通过**

Run: `cd engine && py -m pytest tests/test_dc_fit_rho.py -v`
Expected: 2 passed

**若 `test_tau_positive_for_all_leagues` 失败**：该联赛的最优 rho 导致负 tau。回退该联赛：
```bash
cp engine/cache/_dc_backup_2026-10-14/<联赛>_dc.json engine/cache/<联赛>_dc.json
```
并在计划中记录哪个联赛回退、rho 值、负 tau 出现的 (lh,la,x,y)。**不要**靠 `max(...,1e-12)` 掩盖。

**Step 7: 回归 + 基准对照**

Run: `cd engine && py -m pytest tests/ -q && cd scripts && py goal_engine.py --compare 2>&1 | tail -20`
Expected: 测试不减少；`--compare` 四线对照中 corrected 线不低于 bareDc（生死线）

**Step 8: Commit**

```bash
git add engine/scripts/dc_fit.py engine/tests/test_dc_fit_rho.py engine/cache/*_dc.json
git commit -m "fix(dc): rho 拟合域放宽至 (-0.35,0.25)，解除6联赛撞界"
```

注：`_dc_backup_2026-10-14/` 不入 git（临时回退用），验收通过后删除。

---

## Task 5: 矩阵维度统一到 7×7

**Files:**
- Modify: `engine/scripts/dc_predict.py`（`SCORE_RANGE = range(0, 6)` → `range(0, 7)`）
- Test: `engine/tests/test_matrix_dim.py`（新建）

**Step 1: 写失败测试**

```python
# engine/tests/test_matrix_dim.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import dc_predict as dp
import goal_engine as ge


def _expect(p):
    return sum((x + y) * p[x, y] for x in range(p.shape[0]) for y in range(p.shape[1]))


def test_two_modules_same_dimension():
    """两模块矩阵维度须一致（此前 dc_predict 6×6 vs goal_engine 7×7）。"""
    a = dp.score_matrix(1.8, 1.5, -0.07)
    b = ge.score_matrix(1.8, 1.5, -0.07)
    assert a.shape == b.shape, f"维度不一致: {a.shape} vs {b.shape}"


def test_two_modules_same_distribution():
    """同参数下两模块产出的分布须一致（容差 1e-9）。"""
    a = dp.score_matrix(1.8, 1.5, -0.07)
    b = ge.score_matrix(1.8, 1.5, -0.07)
    assert abs(_expect(a) - _expect(b)) < 1e-9


def test_truncation_loss_bounded():
    """高 lambda 下截断造成的期望损失须 <= 0.10 球（6×6 时为 0.16）。"""
    for lh, la in ((2.0, 1.6), (2.2, 1.8)):
        p = dp.score_matrix(lh, la, -0.07)
        assert (lh + la) - _expect(p) <= 0.10, f"lh={lh} la={la} 损失过大"
```

**Step 2: 运行确认失败**

Run: `cd engine && py -m pytest tests/test_matrix_dim.py -v`
Expected: FAIL — 维度 (6,6) vs (7,7)

**Step 3: 改 SCORE_RANGE**

`dc_predict.py` 常量区：

```python
SCORE_RANGE = range(0, 7)   # 7×7（10-14 统一口径）：原 6×6 与 goal_engine.score_matrix
                            # 的 range(7) 不一致，同参数产出不同分布；且体彩 CRS 有三个
                            # other 兜底格对应总进球>5，6×6 会把该部分错误归一进 5 球内
                            # （实测 lh=2.0/la=1.6 少算 0.16 球，7×7 降至约 0.05）。
```

同时检查 `dc_predict.py` 内所有依赖 `SCORE_RANGE` 长度的硬编码（如 `p[:3,:3]`、`range(7)` 混用处），
`dc_fit.py:195-198` 已用 `range(7)` 与新口径一致，无需改。

**Step 4: 运行确认通过**

Run: `cd engine && py -m pytest tests/test_matrix_dim.py -v`
Expected: 3 passed

**Step 5: 回归全量 + 看 other 格**

Run: `cd engine && py -m pytest tests/ -q`
Expected: 不减少。若 ttg 桶或 other 格相关测试失败，这是真实行为变化——记录差异数值后再定是否调断言（须大哥确认，不擅改）。

**Step 6: Commit**

```bash
git add engine/scripts/dc_predict.py engine/tests/test_matrix_dim.py
git commit -m "fix(matrix): 统一矩阵维度至7×7，消除两模块口径不一致"
```

---

## Task 6: 分维度锚定（TTG 锚市场 / 比分形状保留独立）

**Files:**
- Create: `engine/scripts/ttg_anchor.py`
- Test: `engine/tests/test_ttg_anchor.py`（新建）
- Modify: 调用接入点（T1-T5 完成后按实际确定，须先读 `freq_band.py:235-250` 选格链路）

**Step 1: 写失败测试**

```python
# engine/tests/test_ttg_anchor.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from ttg_anchor import solve_tilt, tilt_dist, dist_expect

BASE = {"0:0": 0.07, "1:0": 0.10, "0:1": 0.075, "1:1": 0.119, "2:1": 0.091,
        "1:2": 0.072, "2:0": 0.070, "2:2": 0.057, "3:1": 0.043, "3:2": 0.026,
        "0:2": 0.045, "3:0": 0.040, "1:3": 0.028, "0:3": 0.023, "4:1": 0.015,
        "4:0": 0.017, "2:3": 0.021, "3:3": 0.012}


def test_tilt_zero_is_identity():
    """k=0 须退化为原分布（零破坏保证）。"""
    out = tilt_dist(BASE, 0.0)
    for s in BASE:
        assert abs(out[s] - BASE[s] / sum(BASE.values())) < 1e-12


def test_solve_tilt_hits_target():
    """解出的 k 须使期望命中目标（容差 1e-6）。"""
    for target in (2.5, 3.0, 3.42, 3.8):
        k = solve_tilt(BASE, target)
        assert abs(dist_expect(tilt_dist(BASE, k)) - target) < 1e-6


def test_shape_preserved_within_same_total():
    """同总进球的格子之间，相对比例须不变（只动 TTG 维度不动形状）。"""
    k = solve_tilt(BASE, 3.42)
    out = tilt_dist(BASE, k)
    # 2:1 与 1:2 同为 3 球，比例须守恒
    assert abs(out["2:1"] / out["1:2"] - BASE["2:1"] / BASE["1:2"]) < 1e-9
    # 3:1 / 1:3 / 2:2 同为 4 球
    assert abs(out["3:1"] / out["1:3"] - BASE["3:1"] / BASE["1:3"]) < 1e-9


def test_monotonic_in_k():
    """期望须随 k 单调递增（二分法可解的前提）。"""
    es = [dist_expect(tilt_dist(BASE, k)) for k in (-0.2, 0.0, 0.2, 0.4)]
    assert es == sorted(es)


def test_low_score_cells_shrink_when_anchoring_up():
    """上锚时低分格须收缩（修正低分偏置的直接体现）。"""
    k = solve_tilt(BASE, 3.42)
    out = tilt_dist(BASE, k)
    z = sum(BASE.values())
    for s in ("0:0", "1:0", "0:1"):
        assert out[s] < BASE[s] / z, f"{s} 未收缩"
```

**Step 2: 运行确认失败**

Run: `cd engine && py -m pytest tests/test_ttg_anchor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ttg_anchor'`

**Step 3: 实现**

```python
# engine/scripts/ttg_anchor.py
"""分维度锚定（2026-10-14 低分偏置修正，大哥裁决 C 方案）。

TTG 维度锚定市场——该维度实测系统性偏低 −0.78 球、14 场无一偏高，是确证缺陷。
比分形状维度（主客分配）完全不锚——该维度无偏差证据，是独立信息来源，须保留
以维持冷门能力（高比分主胜等格子正来自此维度）。

机制：p'(s) ∝ p(s)·exp(k·t(s))，t(s)=该比分总进球，k 由二分法解出使期望命中目标。
同总进球格子间的相对比例守恒（形状不变），k=0 退化为原分布（零破坏）。
"""
import math

K_RANGE = (-2.0, 2.0)      # 二分域：覆盖期望 0.5~8 球，超出即盘面异常
K_ITERS = 80               # 二分次数：(4.0/2^80) 远小于 1e-6 容差


def _total(score: str) -> int:
    h, a = (int(x) for x in score.split(":"))
    return h + a


def dist_expect(dist: dict) -> float:
    """分布期望总进球（须已归一）。"""
    return sum(p * _total(s) for s, p in dist.items())


def tilt_dist(dist: dict, k: float) -> dict:
    """按总进球指数倾斜并归一。k=0 → 仅归一化，形状与水平均不变。"""
    w = {s: p * math.exp(k * _total(s)) for s, p in dist.items()}
    z = sum(w.values())
    return {s: v / z for s, v in w.items()} if z else {}


def solve_tilt(dist: dict, target: float) -> float:
    """解 k 使 tilt 后期望 = target。期望随 k 单调递增，二分法必收敛。

    target 超出分布可达范围时返回域端点——此时调用方应判为盘面异常而非静默接受。
    """
    lo, hi = K_RANGE
    for _ in range(K_ITERS):
        mid = (lo + hi) / 2
        if dist_expect(tilt_dist(dist, mid)) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def anchor_to_market(dist: dict, market_ttg: float) -> tuple:
    """池分布 → 锚定市场 TTG 后的分布。返回 (新分布, k, 原期望)。"""
    z = sum(dist.values())
    base = {s: p / z for s, p in dist.items()} if z else {}
    if not base:
        return {}, 0.0, 0.0
    before = dist_expect(base)
    k = solve_tilt(base, market_ttg)
    return tilt_dist(base, k), k, before
```

**Step 4: 运行确认通过**

Run: `cd engine && py -m pytest tests/test_ttg_anchor.py -v`
Expected: 6 passed

**Step 5: 接入点调查（先读后改，不要猜）**

Run: `cd engine && py -m pytest tests/ -q` 确认新模块未破坏既有
然后读 `engine/scripts/freq_band.py:217-260`（freq_legs 选格链路）与 `boldplay.py:659`（_q_map_for），
确定锚定应插在「shifted_q 产出 q_map 之后、生存阈筛选之前」还是「生存阈之后」。

**判定依据**：锚定改变各格概率，若在生存阈之前锚定，则高分格有机会越过阈值（这是修正的诉求）；
若在之后，则被阈剔除的高分格无法复活。**建议锚定先于生存阈**，但须实测两种顺序对存活格数的影响后再定，
并把实测数据写入本计划。

**Step 6: 接入并验证 31 格判据**

接入后跑当日盘面，验证设计文档第六节判据：
```bash
cd engine/scripts && py -c "<按实际接入点写验证脚本，算 31 格期望 vs 市场 TTG 缺口均值>"
```
Expected: 缺口均值 |x| <= 0.25（原 −0.78）

**Step 7: Commit**

```bash
git add engine/scripts/ttg_anchor.py engine/tests/test_ttg_anchor.py <接入点文件>
git commit -m "feat(anchor): 分维度锚定——TTG锚市场、比分形状保留独立判断"
```

---

## Task 7: 验收（判据对照，不改代码）

**Files:** 无改动，仅验证与记录

**Step 1: 判据全项对照**

逐项跑并记录实测值，对照设计文档第六节冻结判据：

| 指标 | 现状 | 通过线 | 实测 |
|:--|:--|:--|:--|
| 池期望 vs 在盘实况（1103 场 3.233）缺口 | −0.50 | \|缺口\| ≤ 0.15 | ? |
| 当日 14 场池 vs 市场 TTG 缺口均值 | −0.49 | \|均值\| ≤ 0.20 | ? |
| 31 格期望 vs 市场 TTG 缺口均值 | −0.78 | \|均值\| ≤ 0.25 | ? |
| walk-forward ttg3 命中 | BASELINE_REFS | 不低于现状 | ? |
| 全量测试 | 332 passed | 不减少 | ? |

**Step 2: walk-forward 基准**

Run: `cd engine/scripts && py goal_engine.py --walk-forward --league spain-laliga 2>&1 | tail -20`
对照 `goal_engine.py:163` 的 `BASELINE_REFS`：designTtg3=0.624 / reviewNaiveStaticTtg3_2526=0.648 / reviewBareDcTtg3_2526=0.624

**Step 3: 任一判据未达线的处理**

**不要**调整判据（已冻结）。记录未达项、实测值、差距，报告大哥决定是继续修正还是接受部分达标。
判据冻结的意义正在于此——事后改判据等于没有判据。

**Step 4: 清理与收尾**

```bash
rm -rf engine/cache/_dc_backup_2026-10-14    # 验收通过后才删
git add docs/plans/2026-10-14-crs-low-score-bias.md
git commit -m "docs: 低分偏置修正计划与验收实测记录"
```

**Step 5: forward 观察项登记**

以下不作通过线，仅登记待累积（设计文档第六节）：
- 形状维度独立判断 vs 市场，谁更准 → 决定是否扩大不锚范围
- CRS 精确命中率是否从 6.2% 实际上升
- **比分盘一律只走影子不实购，直至 forward 给出结论**

---

## 注意事项

- **判据已冻结**，禁止实施中修改。未达线就报告，不要调线。
- **T4 重拟合前必须备份** DC 缓存（Ruling R4）。
- **测试失败不要擅改断言**——旧断言失败可能是真实行为变化，记录数值后报告。
- **T6 接入点须先读后改**，`freq_band.py` 选格链路有生存阈顺序依赖，猜错会让高分格无法复活。
- `max(..., 1e-12)` 是掩盖不是修复，T4 若出负 tau 须回退该联赛参数。
