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

```bash
cd $FOOTBALL_HOME
./update.sh          # Mac/Linux：git pull + 依赖同步 + 数据刷新 + 闭环学习 + 趋势报告 + 测试回归
./update.ps1         # Windows：同上（7 步）
```

> `install` 是一次性的（建 junction/设环境变量）；`update` 是高频的（只 pull+刷数据+测试）。两个都幂等可重跑。

## 日常操作（两种方式）

### 方式一：对 Claude 说一句话（推荐，全流程自动）

| 你说 | 系统自动走完 |
|:---|:---|
| **"帮我预测"** | 刷数据+联赛画像 → DC拟合+融合 → 体彩采集+本地检索+ESPN积分榜 → 战意状态机 → 分析评级 → 报告归档 → git commit |
| **"再跑一遍"** | 临场终审：赔率复扫+三定律判定 → 更新报告 → commit |
| **"回填赛果"** | 查赛果+命中率 → fd收盘价算CLV → **corpus语料汇总+trend趋势报告 → learn非fd联赛增量拟合+版本发布** → 复盘归档 → commit |
| **"XX队近况？"** | 本地知识库检索直接回答 |
| **"跑下回测"** | walk-forward 回测 + 与市场基线对比 |

### 方式二：命令行（开发/调试用）

```bash
cd engine/scripts
python3 run.py all                                  # 刷数据+画像+索引+拟合+非fd联赛学习（--auto 跳新鲜缓存）
python3 run.py update                               # 仅刷数据缓存（fd 赔率+Pinnacle收盘+xG+体彩五池）
python3 run.py learn                                # ★闭环学习：非fd联赛ESPN增量采集→本地拟合→版本发布
python3 run.py corpus                               # ★学习语料汇总+就绪度+趋势报告（trend.html：4折线/校准图/方案准确率）
python3 run.py predict spain-laliga Vallecano Alaves --market 2.05,3.4,3.9
python3 run.py predict japan kashima-antlers avispa-fukuoka   # 日职/沙特/瑞超本地模型也可用
python3 run.py backtest spain-laliga 2526
python -m pytest tests -q                           # 55 用例回归（改代码必跑）
```

## 预测日全流程

```
1. python3 run.py all                     # 数据+模型就绪（含非fd联赛学习）
2. 触发 /football-betting-prediction       # Claude 走 skill：
   体彩API(赛程/五池赔率/单关资格) + fd缓存(Pinnacle锚) + 本地球队画像
   → dc_predict 概率(含 ttg总进球/hafu半全场) → logit融合 → EV比选 → 修正系数 → 报告(03-predictions/)
3. 出票前 skill 自动走 Step 6.5 临场终审
4. 赛果出来后"回填赛果" → 02-results/ + CLV → corpus就绪度 → learn版本发布 → 04-summaries/
5. git commit（预测与赛果入库 = 可验证历史）
```

## 闭环学习（v4.5.1 · docs/2026-08-22-learning-loop-design.html）

```
回填赛果 → corpus.py 语料汇总（就绪度门槛：融合重校 n≥100 / 系数消融 n≥50 / 拟合 n≥30）
         → espn_fetch history 历史回填（日职550场/沙特404/瑞超376，25+26两季）
         → dc_fit --source local 拟合 → models/ 版本发布（holdout劣化>2%拒发+同数据幂等跳过）
```

- **非 fd 联赛 DC 模型已上线**：日职/沙特/瑞超从纯市场锚升级为模型+市场融合（此前 31% 场次无模型）
- **模型版本存档**：`engine/cache/models/{league}_dc_v{n}.json + .meta.json + latest.json`，每次升级可对比可回滚
- **胜率趋势报告**（v4.5.2，`data/04-summaries/trend.html`）：累计 log loss vs 市场基线 / 方向命中率+滚动20场 / CLV 走势 / 校准图 / 五维分桶 / **方案准确率**（全中/断关/串关惩罚量化——单场概率层与出票方案层双层统计，文献依据 arXiv:1908.08980 + 2008.03033）
- **P2 自动触发**：语料就绪度达标后实现 calibrate.py（融合重校 a≤0.6）/ablate.py（系数消融人审）——当前已回填 8/100
- 韩职 ESPN 无数据源（缺口已登记，找到源补映射即入 learn 链）

## 玩法体系（v4.5 官方规则实锤）

| 玩法 | 关数上限 | 池抽水率* | 单关资格* | 推荐用法 |
|:---|:---:|:---:|:---|:---|
| 胜平负 HAD | 8 | 12.9% | 部分场次 | **长串骨架唯一材料** |
| 让球 HHAD | 8 | 12.9% | ❌ 永不单关 | 长串骨架 |
| 总进球 TTG | 6 | 20.4% | ✅ 全场次 | 单关/2串小关（EV>0才买） |
| 半全场 HAFU | 4 | 20.4% | ✅ 全场次 | 单关（剧本极清晰时） |
| 比分 CRS | 4 | 33.9% | ✅ 全场次 | **只做单关**（4串期望返还仅19%） |

> *实测 2026-08-22（64 场均值）。核心纪律：**同场次不同玩法不可混串**（官方规则第七条）；抽水率=期望亏损率，串N关期望=(1-抽水)^N——高抽水池玩法串越长亏越快。

数据落盘：`engine/cache/sporttery_matches.json` 每场含 `crs`(31选项)/`ttg`(8档)/`hafu`(9组合) 全赔率 + `poolSingle` 单关资格；`dc_predict.py` 输出模型侧 ttg/hafu 概率，skill 用 `EV = p_model×体彩赔率 - 1` 比选（与市场分歧>5pp 弃选）。

## 目录结构（= 四层架构）

```
football/
├── .claude/CLAUDE.md      ← ④ 记忆层：项目约定，进入项目自动加载
├── data/                  ← ① 数据层（编号 = 数据生命周期）
│   ├── 01-teams/          #    球队画像 + _aliases.json 多源命名映射 + _index.json 路由
│   ├── 02-results/        #    赛果回填（YYYY-MM-DD.json，含 CLV）+ league/ 本地赛果库（ESPN回填）
│   ├── 03-predictions/    #    预测报告 HTML（仅用户输出用 HTML+SVG）
│   ├── 04-summaries/      #    五维统计 + 回测结果 + corpus.json 学习语料
│   └── 05-trends/         #    趋势发现
├── engine/                ← ② 计算层
│   ├── scripts/           #    run.py(入口) / dc_fit / dc_predict / backtest / corpus / odds_fetch / elo_fetch / xg_fetch / espn_fetch / cn_fetch / sporttery_fetch / build_index
│   └── cache/             #    DC 参数 / models/ 版本化存档 / fusion.json / fd 赔率缓存 / sporttery_matches.json(五池+单关资格)
├── skill/                 ← ③ 检索入口：SKILL.md v4.5.1
└── docs/                  #    设计文档
```

## 数据源（2026-08-22 实测）

| 源 | 状态 | 用途 |
|:---|:---|:---|
| 体彩官方 API | ✅ | 赛程 + **五池赔率（胜平负/让球/比分31/总进球8/半全场9）+ 单关资格**（sporttery_fetch.py） |
| football-data.co.uk | ✅ | **Pinnacle 收盘价（概率锚）+ xG + 比分**，主流联赛 |
| ESPN | ✅ | 赛果、排名、**整季历史回填**（espn_fetch.py 直连，日职/沙特/瑞超 DC 数据源；韩职无数据） |
| titan007 | ✅ | 积分榜国内兜底 + 中英队名对照（cn_fetch.py） |
| clubelo.com | ⚠️ api 被墙，主域 HTML 兜底 | Elo（elo_fetch.py 双链路自动切换） |
| 搜索引擎 | ⏳ 配额 8-25 恢复 | 补充检索 |

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
