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
echo "[1/5] git pull..."
cd "$HOME_DIR"
git pull --ff-only || echo "    git pull 失败（检查本地改动）"

# 2. Python 依赖同步
echo "[2/5] 同步 Python 依赖..."
python3 -m pip install -r "$HOME_DIR/engine/requirements.txt" --quiet
echo "    OK"

# 3. 数据刷新（赔率 + 联赛画像 + DC 重拟合 --auto）
echo "[3/5] 数据刷新（run.py all）..."
cd "$HOME_DIR/engine/scripts"
python3 run.py all || echo "    run.py all 部分失败（数据源可能不可达，稍后重试）"

# 4. 体彩五池采集（v4.5：赛程 + crs/ttg/hafu 赔率 + 单关资格）
echo "[4/5] 体彩五池采集（sporttery_fetch.py）..."
python3 sporttery_fetch.py || echo "    sporttery_fetch 失败（体彩 API 可能限流，预测时自动重试）"

# 5. 测试回归（验证代码改动没破坏任何东西）
echo "[5/5] 测试回归..."
cd "$HOME_DIR/engine"
python3 -m pytest tests -q || echo "    ❌ 测试失败——先检查改动再预测"

echo ""
echo "=== 更新完成 ==="
echo "就绪：对 Claude 说'帮我预测'即可"

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
