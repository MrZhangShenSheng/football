#!/usr/bin/env bash
# install.sh — Football 竞彩预测知识库 一键安装（Mac/Linux）
# 用法：clone 后 cd 仓库根目录，执行 ./install.sh
# 幂等：可重复执行

set -e
HOME_DIR="$(cd "$(dirname "$0")" && pwd)"
export FOOTBALL_HOME="$HOME_DIR"

echo "=== Football 知识库安装 ==="
echo "项目根: $HOME_DIR"
echo ""

# 1. Python 依赖
echo "[1/4] 安装 Python 依赖..."
python3 -m pip install -r "$HOME_DIR/engine/requirements.txt" --quiet
echo "    OK"

# 2. skill 软链接（Claude Code 发现入口）
echo "[2/4] 建立 skill 软链接..."
mkdir -p "$HOME/.claude/skills"
ln -sfn "$HOME_DIR/skill" "$HOME/.claude/skills/football-betting-prediction"
test -f "$HOME/.claude/skills/football-betting-prediction/SKILL.md" && echo "    OK" || { echo "    失败"; exit 1; }

# 3. 持久化 FOOTBALL_HOME
echo "[3/4] 设置环境变量 FOOTBALL_HOME..."
SHELL_RC="$HOME/.zshrc"
[ -n "$BASH_VERSION" ] && SHELL_RC="$HOME/.bashrc"
grep -q "FOOTBALL_HOME=" "$SHELL_RC" 2>/dev/null || echo "export FOOTBALL_HOME=\"$HOME_DIR\"" >> "$SHELL_RC"
echo "    OK（重开终端或 source $SHELL_RC 生效）"

# 4. 数据初始化
echo "[4/4] 数据初始化（run.py all + 体彩五池 + 非fd联赛历史回填）..."
cd "$HOME_DIR/engine/scripts"
python3 run.py all || echo "    run.py all 部分失败（数据源可能不可达，稍后重试）"
python3 sporttery_fetch.py || echo "    sporttery_fetch 失败（体彩 API 可能限流，预测时自动重试）"
# 非fd联赛 ESPN 历史回填 + 本地 DC 拟合（幂等：models/ 已有版本则按门槛跳过）
python3 espn_fetch.py history jpn.1 2025 || echo "    日职历史回填失败（可稍后 run.py learn 重试）"
python3 espn_fetch.py history ksa.1 2025 || echo "    沙特历史回填失败"
python3 espn_fetch.py history swe.1 2025 || echo "    瑞超历史回填失败"
# 韩职走体彩 league-results 口径（v4.8 接入；ESPN 无此联赛数据）
python3 sporttery_fetch.py league-results korea || echo "    韩职历史回填失败（可稍后重跑）"
python3 dc_fit.py japan --source local --publish || echo "    日职本地拟合失败"
python3 dc_fit.py saudi --source local --publish || echo "    沙特本地拟合失败"
python3 dc_fit.py sweden --source local --publish || echo "    瑞超本地拟合失败"
python3 dc_fit.py korea --source local --publish || echo "    韩职本地拟合失败"

echo ""
echo "=== 安装完成 ==="
echo "验证测试：cd $HOME_DIR/engine/scripts; python3 -m pytest ../tests -q"
echo "开始使用：新终端里对 Claude 说'帮我预测'"
echo "日常更新：cd $HOME_DIR; ./update.sh"

# 5. 知识库新鲜度摘要（与 install.ps1 对齐）
echo ""
echo "知识库就绪度："
cd "$HOME_DIR"
fd_count=$(grep -l '"computedFrom": "fd"' data/00-leagues/*.json 2>/dev/null | wc -l | tr -d ' ')
espn_count=$(grep -l '"standingsSource": "espn"' data/00-leagues/*.json 2>/dev/null | wc -l | tr -d ' ')
cn_count=$(grep -l '"standingsSource": "titan007"' data/00-leagues/*.json 2>/dev/null | wc -l | tr -d ' ')
echo "    fd 自动覆盖: ${fd_count} 个联赛"
echo "    ESPN 直连: ${espn_count} 个联赛（espn_fetch.py）"
echo "    titan007 兜底: ${cn_count} 个联赛（cn_fetch.py）"
echo "    → 非 fd 联赛首次'预测'时经 Step 2.5 冷启动初始化"
# 体彩五池采集验证（v4.5：crs/ttg/hafu + 单关资格）
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
else
    echo "    体彩五池: 未采集（sporttery_matches.json 缺失，预测时 sporttery_fetch.py 自动补）"
fi
# 模型版本存档（v4.5.1 闭环学习）+ 胜率趋势（v4.5.2）
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
else
    echo "    本地DC模型: 未发布（install 步骤4的 espn history 回填可重试）"
fi
# 首次跑回归验证闭环（自动回填 + 语料 + 趋势报告；换机后语料随 git 历史就位）
cd "$HOME_DIR/engine/scripts" && python3 run.py verify >/dev/null 2>&1
[ -f "$HOME_DIR/data/04-summaries/trend.html" ] && echo "    胜率趋势: data/04-summaries/trend.html 已生成（run.py verify 全链路就绪）" || echo "    胜率趋势: 生成失败（run.py verify 可重跑）"
