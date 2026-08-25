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
| **"回填赛果"** | **run.py verify 一键闭环：backfill自动回填 → 票务结算(实票账本+boldplay推演) → corpus语料+trend断言 → calibrate重校(门槛自检) → ablate消融(人审)** → learn非fd联赛拟合+版本发布 → 复盘归档 → commit |
| **"我买了/出票了"** | **实票登记**：方案转正(冻结赔率+时间戳)或自组票手动建档 → `data/06-tickets/tickets.json` + git commit（git历史=出票凭证）→ 回填时自动结算 → tickets.html 资金曲线/玩法分解/纪律对照 |
| **"XX队近况？"** | 本地知识库检索直接回答 |
| **"跑下回测"** | walk-forward 回测 + 与市场基线对比 |

### 方式二：命令行（开发/调试用）

```bash
cd engine/scripts
python3 run.py all                                  # 刷数据+画像+索引+拟合+非fd联赛学习（--auto 跳新鲜缓存）
python3 run.py update                               # 仅刷数据缓存（fd 赔率+Pinnacle收盘+xG+体彩五池）
python3 run.py verify                               # ★回归验证闭环一键：回填→语料+断言→重校→消融
python3 run.py backfill [日期]                      # 赛果回填（v4.9 双链路：体彩编号对票主链路+ESPN兜底；未开赛自动跳过）
python3 run.py learn                                # ★闭环学习：非fd联赛ESPN增量采集→本地拟合→版本发布
python3 run.py corpus                               # ★学习语料汇总+就绪度+趋势报告（trend.html 七区块）
python3 run.py predict spain-laliga Vallecano Alaves --market 2.05,3.4,3.9
python3 run.py predict japan kashima-antlers avispa-fukuoka   # 日职/沙特/瑞超本地模型也可用
python3 run.py backtest spain-laliga 2526
python3 boldplay.py                                 # ★v5.0 阶梯出票卡（保底4/进阶6/翻身档，桂林/梅州形状轮换，月封顶240自动gate）
python3 boldplay.py settle                          # 阶梯卡推演结算：逐leg判定，legHits入层4实测库（实票走"我买了"登记账本）
python3 ticket_report.py                            # 实票账本报告（资金曲线/票务清单/玩法分解/纪律对照）
python -m pytest tests -q                           # 88 用例回归（改代码必跑）
```

## 预测日全流程

```
1. python3 run.py all                     # 数据+模型就绪（含非fd联赛学习）
2. 触发 /football-betting-prediction       # Claude 走 skill：
   体彩API(赛程/五池赔率/单关资格) + fd缓存(Pinnacle锚) + 本地球队画像
   → dc_predict 概率(含 ttg总进球/hafu半全场) → logit融合 → EV比选 → 修正系数 → 报告(03-predictions/)
3. 出票前 skill 自动走 Step 6.5 临场终审；v5.0 起默认输出 boldplay 阶梯出票卡（保底4/进阶6/翻身档，替换旧🚀/⚖️/🛡️三段式）
3.5 实际出票后说"我买了" → 实票登记入账本（git 历史=出票凭证），回填时自动结算
4. 赛果出来后"回填赛果" → run.py verify 一键闭环（回填→语料+断言→重校→消融）→ learn版本发布 → 04-summaries/
5. git commit（预测与赛果入库 = 可验证历史）
```

## 闭环学习（v4.7 · docs/2026-08-22-learning-loop-design.html）

```
回填赛果 → ① backfill.py 自动回填（v4.9 双链路：体彩编号对票主链路+ESPN 兜底；未开赛场自动跳过，完赛重跑即回填）
         → ①.5 票务结算（v4.10/v5.0：settle_tickets 实票账本按形状算派彩+重刷tickets.html；boldplay settle 阶梯卡推演legHits入层4实测库）
         → ② corpus.py 语料汇总（门槛：融合重校 n≥100 / 消融 n≥50 / 断言 n≥15）
         → ③ trend_report ⑦回归断言（A1校准/A2星级/A3系数/A4 DC价值——触发即出结论+动作）
         → ④ calibrate.py 融合重校（自动：网格 a∈[0.05,0.6] 最小RPS；护栏改善<1%不动+历史可回滚）
         → ⑤ ablate.py 系数消融（人审：chain触发vs未触发；负增益>10pp出diff建议）
         → ⑥ learn 非fd联赛拟合 + models/ 版本发布（holdout劣化>2%拒发）
```

- **`run.py verify` = ①~⑤ 一键串联**，门槛未达自动跳过并提示差距
- **非 fd 联赛 DC 模型已上线**：日职/沙特/瑞超/韩职 四联赛（日职550场/沙特404/瑞超376 经 ESPN 回填；韩职 597 场经体彩 league-results 口径回填——ESPN 无此联赛，体彩源补位）
- **模型版本存档**：`engine/cache/models/` 版本链可对比可回滚；fusion_history.json 记录每次重校
- **胜率趋势报告**（`data/04-summaries/trend.html` 七区块）：log loss vs 市场 / 命中率+滚动20场 / CLV / 校准图 / 五维分桶 / 方案准确率 / **回归断言**
- ~~韩职无数据源~~ ✅ 2026-08-23 已解决：体彩 `league-results korea` 口径回填 597 场，`run.py learn` 已接（SPORTTERY_LEAGUES）

## 玩法体系（v4.5 官方规则实锤）

| 玩法 | 关数上限 | 池抽水率* | 单关资格* | 推荐用法 |
|:---|:---:|:---:|:---|:---|
| 胜平负 HAD | 8 | 12.9% | 部分场次 | **长串骨架唯一材料** |
| 让球 HHAD | 8 | 12.9% | ❌ 永不单关 | 长串骨架 |
| 总进球 TTG | 6 | 20.4% | ✅ 全场次 | 单关/2串小关（EV>0才买） |
| 半全场 HAFU | 4 | 20.4% | ✅ 全场次 | 单关（剧本极清晰时） |
| 比分 CRS | 4 | 33.9% | ✅ 全场次 | **只做单关**（4串期望返还仅19%） |

> *实测 2026-08-22（64 场均值）。核心纪律（2026-08-25 实票口径修正）：**同一场次在同一注串内限一种玩法，不同场次可用不同玩法混串**（混合过关本义）；混串总关数 ≤ 最低玩法上限（含 CRS 腿 ≤4）；同场次跨票分池允许；抽水率=期望亏损率，串N关期望=(1-抽水)^N——高抽水池玩法串越长亏越快。

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
│   ├── 05-trends/         #    趋势发现
│   └── 06-tickets/        #    ★实票账本（票=顶层实体：形状/腿/赔率冻结/结算/纪律事件 + tickets.html 报告）——实票=有结算记录的票，其余全是方案推演
├── engine/                ← ② 计算层
│   ├── scripts/           #    run.py(入口) / dc_fit / dc_predict / backtest / corpus / trend_report / backfill / calibrate / ablate / odds_fetch / elo_fetch / xg_fetch / espn_fetch / cn_fetch / sporttery_fetch / band_calibration / score_ev / live_odds_probe / build_index / boldplay(阶梯出票卡+settle) / ticket_report(实票账本报告)
│   └── cache/             #    DC 参数 / models/ 版本化存档 / fusion.json / fd 赔率缓存 / sporttery_matches.json(五池+单关资格) / score_odds(体彩全玩法赔率日存档) / live_odds_feasibility.json
├── skill/                 ← ③ 检索入口：SKILL.md v5.0（阶梯出票卡为默认输出）+ references/ 外置参考（系数详表/官方规则/教训档案，按需加载）
└── docs/                  #    设计文档
```

## 数据源（2026-08-22 实测）

| 源 | 状态 | 用途 |
|:---|:---|:---|
| 体彩官方 API | ✅ | 赛程 + **五池赔率（胜平负/让球/比分31/总进球8/半全场9）+ 单关资格** + 单场情报 insight + **赛果回填双口径**（league-results 联赛历史含韩职 / results 开奖对票，sporttery_fetch.py） |
| football-data.co.uk | ✅ | **Pinnacle 收盘价（概率锚）+ xG + 比分**，主流联赛 |
| ESPN | ⚠️ 赛果接口 8/22+ 停摆（backfill 已切体彩主链路） | 排名、整季历史回填（espn_fetch.py 直连，日职/沙特/瑞超 DC 数据源；韩职走体彩 league-results） |
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
