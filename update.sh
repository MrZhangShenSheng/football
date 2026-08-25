#!/usr/bin/env bash
# update.sh — Football 知识库更新（Mac/Linux，git pull 后或独立执行）
# 与 install 的区别：不建软链接/不设环境变量，增加测试回归
# 幂等：可重复执行

set -e
HOME_DIR="$(cd "$(dirname "$0")" && pwd)"
export FOOTBALL_HOME="$HOME_DIR"

echo "=== Football 知识库更新 ==="
echo "项目根: $HOME_DIR"
echo ""

# 1. git pull（skill + scripts + tests 可能有更新）
echo "[1/7] git pull..."
cd "$HOME_DIR"
git pull --ff-only || echo "    git pull 失败（检查本地改动）"

# 2. Python 依赖同步
echo "[2/7] 同步 Python 依赖..."
python3 -m pip install -r "$HOME_DIR/engine/requirements.txt" --quiet
echo "    OK"

# 3. 数据刷新（赔率 + 联赛画像 + DC 重拟合 --auto）
echo "[3/7] 数据刷新（run.py all）..."
cd "$HOME_DIR/engine/scripts"
python3 run.py all || echo "    run.py all 部分失败（数据源可能不可达，稍后重试）"

# 4. 体彩五池采集（v4.5：赛程 + crs/ttg/hafu 赔率 + 单关资格）
echo "[4/7] 体彩五池采集（sporttery_fetch.py）..."
python3 sporttery_fetch.py || echo "    sporttery_fetch 失败（体彩 API 可能限流，预测时自动重试）"

# 5. 闭环学习（v4.5.1：非fd联赛 espn 增量采集 → 本地拟合 → 版本发布）
echo "[5/7] 闭环学习（run.py learn）..."
python3 run.py learn || echo "    learn 部分失败（ESPN 可能不可达，下次 update 重试）"

# 6. 回归验证闭环（v4.7：自动回填→语料+断言→重校→消融，门槛自检跳过；v4.10 起含实票账本票务结算）
#    v5.1 起 boldplay settle 已入 run.py verify 编排（幂等安静），无需单独补跑
echo "[6/7] 回归验证闭环（run.py verify，含票务结算+阶梯卡settle）..."
python3 run.py verify || echo "    verify 部分失败（ESPN 缓存延迟常见，下轮 update 自动补）"

# 7. 测试回归（验证代码改动没破坏任何东西）
echo "[7/7] 测试回归..."
cd "$HOME_DIR/engine"
python3 -m pytest tests -q || echo "    ❌ 测试失败——先检查改动再预测"

echo ""
echo "=== 更新完成 ==="
echo "就绪：对 Claude 说'帮我预测'即可"
# 学习语料就绪度 + 胜率摘要（v4.5.1/v4.7）
if [ -f "$HOME_DIR/data/04-summaries/corpus.json" ]; then
    python3 - "$HOME_DIR/data/04-summaries/corpus.json" << 'PYEOF'
import json, sys
c = json.load(open(sys.argv[1]))
rd = c["readiness"]
print(f"    学习语料: {c['n_total']} 条 / {c.get('n_rounds', '?')} 轮（已回填 {rd['n_result']} · CLV {rd['n_clv']}）"
      f" | 融合重校 {'✅' if rd['calibrateReady'] else '差' + str(rd['calibrateGap']) + '条'}"
      f" | 系数消融 {'✅' if rd['ablateReady'] else '观察中'}")
# 方案层摘要（trend.html 的核心数字）
plans = c.get("plans", {})
n_plans = sum(1 for v in plans.values() if isinstance(v, (list, dict)))
print(f"    出票方案: {n_plans} 个已记录 → 详情见 data/04-summaries/trend.html（①logloss ②命中率 ③CLV ④校准 ⑤分桶 ⑥方案准确率 ⑦回归断言）")
PYEOF
fi

# 知识库新鲜度摘要（与 update.ps1 对齐）
echo ""
echo "知识库就绪度："
cd "$HOME_DIR"
fd_count=$(grep -l '"computedFrom": "fd"' data/00-leagues/*.json 2>/dev/null | wc -l | tr -d ' ')
espn_count=$(grep -l '"standingsSource": "espn"' data/00-leagues/*.json 2>/dev/null | wc -l | tr -d ' ')
cn_count=$(grep -l '"standingsSource": "titan007"' data/00-leagues/*.json 2>/dev/null | wc -l | tr -d ' ')
echo "    fd 自动覆盖: ${fd_count} 个联赛"
echo "    ESPN 直连: ${espn_count} 个联赛"
echo "    titan007 兜底: ${cn_count} 个联赛"
if [ -f "$HOME_DIR/engine/cache/sporttery_matches.json" ]; then
    python3 - "$HOME_DIR/engine/cache/sporttery_matches.json" << 'PYEOF'
import json, sys
d = json.load(open(sys.argv[1]))
pools = {}
for m in d.get("matches", []):
    for p, s in (m.get("poolSingle") or {}).items():
        st = pools.setdefault(p, [0, 0])
        st[0] += 1
        if str(s) == "1":
            st[1] += 1
print(f"    体彩五池: {d.get('count', 0)} 场在售, " + ", ".join(
    f"{p} 售{v[0]}/单关{v[1]}" for p, v in sorted(pools.items())))
PYEOF
fi
# 模型版本存档摘要（v4.5.1）
# 实票账本摘要（v4.10：票数/本金/净利，实票=有结算记录的票）
if [ -f "$HOME_DIR/data/06-tickets/tickets.json" ]; then
    python3 - "$HOME_DIR/data/06-tickets/tickets.json" << 'PYEOF'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
m, ts = d.get("meta", {}), d.get("tickets", [])
n_pending = sum(1 for t in ts if t.get("settled", {}).get("status") == "pending")
print(f"    实票账本: {len(ts)} 张（待结算 {n_pending}）· 本金 {m.get('totalStake', 0):.0f} 元"
      f" · 净利 {m.get('totalNet', 0):+.1f} 元 → data/06-tickets/tickets.html")
PYEOF
fi
if [ -f "$HOME_DIR/engine/cache/models/latest.json" ]; then
    python3 - "$HOME_DIR" << 'PYEOF'
import json, sys, pathlib
root = pathlib.Path(sys.argv[1])
latest = json.load(open(root / "engine/cache/models/latest.json"))
parts = []
for lg, ver in sorted(latest.items()):
    mp = root / f"engine/cache/models/{lg}_dc_v{ver}.meta.json"
    n = json.load(open(mp))["nTrain"] if mp.exists() else "?"
    parts.append(f"{lg} v{ver}({n}场)")
print("    本地DC模型: " + ", ".join(parts))
PYEOF
fi
