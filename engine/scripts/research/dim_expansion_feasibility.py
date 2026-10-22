# engine/scripts/research/dim_expansion_feasibility.py
"""补维度可行性盘点：数据可得性 × 赛前可观测性 双筛。

== 为什么先盘点再动手 ==

t5 的教训（见 memory/prereg-gate-needs-observability-check）：统计显著但条件变量
赛后才有 ⟹ 永不可用。补维度若不先过可观测闸，等于按同一条路再走一遍。

两道筛子，顺序不可换：
  筛1 数据可得性：fd CSV / 本仓既有档案里有没有这个字段，覆盖多少场。
  筛2 赛前可观测性：该维度算出的"条件变量"在开赛前能否取到。
      注意区分两种用法 ——
        (a) 赛后归因用：进账单解释"这个比分怎么来的"，赛后变量正当。
        (b) 赛前校准用：进频次表当 on 条件,再回头校准 v3.6 系数,必须赛前可得。
      只有同时过两筛的维度,补了才可能拿到校准授权。

== 判据（跑数前冻结）==

对每个候选维度输出四元组：(数据可得, 覆盖场数, 赛前可观测, 用途档)
用途档三值：
  calibratable —— 两筛全过,补了可进频次表争取校准授权
  bill_only    —— 数据有但赛后才得,只能进账单做归因,不进频次表
  no_source    —— 数据不可得,不编造

排序原则：calibratable 优先补,bill_only 次之(提升账单解释力但不解锁系数),
no_source 如实标注。不按"实现难度"排序,按"能否解锁校准"排序。

开发者 sszhang
"""
import csv
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FD_DIR = ROOT / "engine" / "cache" / "shape_backtest"
HIST_DIR = ROOT / "data" / "04-summaries" / "score-attribution" / "hist"
TEAMS_DIR = ROOT / "data" / "01-teams"
LEAGUES_DIR = ROOT / "data" / "00-leagues"

# 候选维度 → (设计表名, fd字段或本仓源, 赛前可观测, 理由)
CANDIDATES = [
    ("A2.xg", "攻防画像(近10场xG滚动)", ["HxG", "AxG"], True,
     "用的是【历史场次】的 xG 滚动均值,赛前可算(与 E5 用【本场】xG 不同)"),
    ("A3.env", "联赛进球环境", ["__league_profile__"], True,
     "联赛场均/主胜率,静态画像赛前可查(已部分用于 league_avg_total)"),
    ("A4.style", "风格对位(射门射正滚动)", ["HS", "AS", "HST", "AST"], True,
     "历史场次射门风格滚动,赛前可算;本场射门=赛后(E4 用的那个)"),
    ("A4.discipline", "纪律特性(犯规/黄牌滚动)", ["HF", "AF", "HY", "AY"], True,
     "历史滚动犯规率,赛前可算;本场犯规=赛后"),
    ("R4.motivation", "战意档(争冠/保级)", ["__standings__"], True,
     "积分榜位置+剩余轮次,赛前可查"),
    ("referee.profile", "裁判画像(判罚倾向滚动)", ["Referee"], True,
     "裁判人选赛前通常已公布;其历史判罚率为滚动统计,赛前可算"),
    ("E3.setpiece", "定位球/点球占比", ["__espn_only__"], False,
     "本场进球方式,赛后才有;且 fd 无此字段"),
    ("E4.gk", "门将背离(本场扑救vs射正)", ["HST", "AST"], False,
     "本场射正,赛后才有(现 dim_shots 已在用,属 bill_only)"),
    ("R2.rotation", "轮换幅度(首发变动N人)", ["__lineup_track__"], True,
     "首发赛前约1小时公布,lineup_watch 已在采;历史场次无首发存档=历史窗缺口"),
    ("weather", "天气", ["__no_source__"], True,
     "赛前可查但本仓无采集源,也无历史存档"),
]


def _fd_coverage() -> tuple[dict, int]:
    """各 fd 字段非空行数 + 总行数。"""
    cnt = defaultdict(int)
    total = 0
    for f in sorted(FD_DIR.glob("*.csv")):
        with open(f, encoding="latin-1") as fh:
            for row in csv.DictReader(fh):
                if not (row.get("FTHG") or "").strip():
                    continue
                total += 1
                for k, v in row.items():
                    if (v or "").strip():
                        cnt[k] += 1
    return cnt, total


def _bill_universe() -> int:
    n = 0
    for p in sorted(HIST_DIR.glob("*-attribution.json")):
        n += sum(1 for b in json.loads(p.read_text(encoding="utf-8")) if "baseline" in b)
    return n


def main() -> None:
    cov, total = _fd_coverage()
    bills = _bill_universe()
    print(f"fd 有赛果行 {total} · 账单出账 {bills}\n")

    rows = []
    for dim, label, fields, pre, why in CANDIDATES:
        if fields and fields[0].startswith("__"):
            tag = fields[0].strip("_")
            if tag == "league_profile":
                n = len(list(LEAGUES_DIR.glob("*.json")))
                avail, note = True, f"00-leagues {n} 档画像"
            elif tag == "standings":
                n = len(list(LEAGUES_DIR.glob("*.json")))
                avail, note = True, f"00-leagues standings({n} 档)"
            elif tag == "lineup_track":
                n = len(list((ROOT / "data" / "05-trends").glob("*-lineups.json")))
                avail, note = n > 0, f"05-trends 首发轨 {n} 天(仅 forward)"
            else:
                avail, note = False, "无源"
            c = None
        else:
            c = min(cov.get(f, 0) for f in fields)
            avail = c > 0
            note = f"fd 覆盖 {c}/{total} ({c/total*100:.0f}%)" if total else "—"

        bucket = ("no_source" if not avail else
                  "calibratable" if pre else "bill_only")
        rows.append((bucket, dim, label, note, pre, why))

    order = {"calibratable": 0, "bill_only": 1, "no_source": 2}
    rows.sort(key=lambda r: (order[r[0]], r[1]))
    cur = None
    for bucket, dim, label, note, pre, why in rows:
        if bucket != cur:
            cur = bucket
            head = {"calibratable": "可争取校准授权（两筛全过）",
                    "bill_only": "仅进账单（赛后变量·不进频次表）",
                    "no_source": "数据不可得（如实标缺·不编造）"}[bucket]
            print(f"── {head} ──")
        print(f"  {dim:<18} {label}")
        print(f"       数据：{note}")
        print(f"       赛前：{'可观测' if pre else '赛后才有'} —— {why}")
    print()
    cal = [r[1] for r in rows if r[0] == "calibratable"]
    print(f"结论：{len(cal)} 个维度两筛全过 → {'、'.join(cal)}")
    print("      这些补进去才可能解锁系数校准；其余补了只提升账单解释力。")


if __name__ == "__main__":
    main()
