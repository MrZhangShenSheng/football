# 🏟️ Football — 竞彩足球预测知识库

本地足球知识图谱 + Dixon-Coles/Elo 统计模型 + 检索增强预测（Agentic RAG）+ 趋势挖掘引擎。
设计文档：`docs/2026-08-21-rag-design.html`

## 快速开始（新环境安装）

```powershell
# 1. 克隆仓库
git clone <repo-url> football && cd football

# 2. 一键安装（装依赖 + 建 skill junction + 设 FOOTBALL_HOME + 数据初始化）
./install.ps1        # Windows
./install.sh         # Mac/Linux

# 3. 重开终端，对 Claude 说"帮我预测"即可触发
```

## 更新（日常/换机后）

```powershell
cd $FOOTBALL_HOME
./update.ps1         # git pull + 依赖同步 + 数据刷新 + 测试回归
```

> `install` 是一次性的（建 junction/设环境变量）；`update` 是高频的（只 pull+刷数据+测试）。两个都幂等可重跑。

## 日常操作（两种方式）

### 方式一：对 Claude 说一句话（推荐，全流程自动）

| 你说 | 系统自动走完 |
|:---|:---|
| **"帮我预测"** | 刷数据+联赛画像 → DC拟合+融合 → 体彩采集+本地检索+ESPN积分榜 → 战意状态机 → 分析评级 → 报告归档 → git commit |
| **"再跑一遍"** | 临场终审：赔率复扫+三定律判定 → 更新报告 → commit |
| **"回填赛果"** | 查赛果+命中率 → fd收盘价算CLV → 重拟合 → 复盘归档 → commit |
| **"XX队近况？"** | 本地知识库检索直接回答 |
| **"跑下回测"** | walk-forward 回测 + 与市场基线对比 |

### 方式二：命令行（开发/调试用）

```powershell
cd engine\scripts
python run.py all                                   # 刷数据+画像+索引+拟合（--auto 跳新鲜缓存）
python run.py update                                # 仅刷数据缓存（fd 赔率+Pinnacle收盘+xG）
python run.py predict spain-laliga Vallecano Alaves --market 2.05,3.4,3.9
python run.py backtest spain-laliga 2526
python -m pytest tests -q                           # 34 用例回归（改代码必跑）
```

## 预测日全流程

```
1. python run.py all                      # 数据+模型就绪
2. 触发 /football-betting-prediction       # Claude 走 skill：
   体彩API(赛程/可买性) + fd缓存(Pinnacle锚) + 本地球队画像
   → dc_predict 概率 → logit融合 → 修正系数 → 报告(03-predictions/)
3. 出票前 skill 自动走 Step 6.5 临场终审
4. 赛果出来后"回填赛果" → 02-results/ + CLV → 04-summaries/
5. git commit（预测与赛果入库 = 可验证历史）
```

## 目录结构（= 四层架构）

```
football/
├── .claude/CLAUDE.md      ← ④ 记忆层：项目约定，进入项目自动加载
├── data/                  ← ① 数据层（编号 = 数据生命周期）
│   ├── 01-teams/          #    球队画像 + _aliases.json 多源命名映射 + _index.json 路由
│   ├── 02-results/        #    赛果回填（YYYY-MM-DD.json，含 CLV）
│   ├── 03-predictions/    #    预测报告 HTML（仅用户输出用 HTML+SVG）
│   ├── 04-summaries/      #    五维统计 + 回测结果
│   └── 05-trends/         #    趋势发现
├── engine/                ← ② 计算层
│   ├── scripts/           #    run.py(入口) / dc_fit / dc_predict / backtest / odds_fetch / elo_fetch / xg_fetch / build_index
│   └── cache/             #    DC 参数 / fusion.json / fd 赔率缓存（Pinnacle 收盘价 + xG）
├── skill/                 ← ③ 检索入口：SKILL.md v4.1
└── docs/                  #    设计文档
```

## 数据源（2026-08-21 实测）

| 源 | 状态 | 用途 |
|:---|:---|:---|
| 体彩官方 API | ✅ | 赛程 + 赔率（可买性） |
| football-data.co.uk | ✅ | **Pinnacle 收盘价（概率锚）+ xG + 比分**，主流联赛 |
| ESPN | ✅ | 赛果、排名 |
| clubelo.com | ❌ 当前网络不可达 | Elo（elo_fetch.py 就绪，恢复即用） |
| 搜索引擎 | ⏳ 配额 8-31 恢复 | 补充检索 |

## 核心约定（详见 .claude/CLAUDE.md）

- 概率锚 = Pinnacle 收盘价去水；体彩赔率仅用于可买性/奖金/CLV
- 内部文件 JSON/纯文本；仅用户报告用 HTML+内嵌 SVG
- H2H 交锋仅叙事参考不进权重；多源命名经 _aliases.json 规范 ID 打通
- 评估主指标：CLV + RPS + log loss（命中率仅展示）；修正系数须消融验证

## 回测基准（西甲 2025-26，159 场 walk-forward）

| 模型 | RPS | 准确率 |
|:---|:---:|:---:|
| 纯市场（Pinnacle 收盘） | 0.1850 | 58.5% |
| 纯 DC | 0.2045 | 50.9% |
| 融合 a=0.4 | 0.1856 | 57.9% |

> 市场是最强基线（学术共识实测验证）；融合要产生增量需给模型喂赔率外信息（xG/伤停/休息天数）——见 docs 设计文档 P3。
