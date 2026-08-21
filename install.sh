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
echo "[4/4] 数据初始化（run.py all）..."
cd "$HOME_DIR/engine/scripts"
python3 run.py all || echo "    run.py all 部分失败（数据源可能不可达，稍后重试）"

echo ""
echo "=== 安装完成 ==="
echo "验证测试：cd engine/scripts; python3 -m pytest tests -q"
echo "开始使用：新终端里对 Claude 说'帮我预测'"
echo "日常更新：cd $HOME_DIR; git pull; python3 engine/scripts/run.py all"
