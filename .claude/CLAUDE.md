# Football 足球预测知识库

## 使用协议（一句话触发，全流程自动）★ v4.2

| 用户说 | 系统自动走完 | 产出 |
|:---|:---|:---|
| **"帮我预测"** | run.py update（刷数据+联赛画像）→ **data_check 体检+冷启动初始化（缺联赛/球队画像先补）** → dc_fit --auto → 体彩采集 → 本地检索+ESPN 实时积分榜 → DC 融合 → 战意状态机 → 分析评级 → 报告+预测JSON 归档 → git commit | 摘要卡（方案/赔率/仓位） |
| **"再跑一遍"** | 体彩赔率复扫 → 信息边际三定律判定 → 增量更新报告 → git commit | 终审结论（维持/修订） |
| **"回填赛果"** | 查赛果 → 回填命中率 → fd 刷收盘价 → 算 CLV → **票务结算（backfill.settle_tickets：pending票对赛果→按形状算派彩→重刷 tickets.html）** → **run.py corpus（语料+就绪度+趋势报告）→ run.py attribute（错题归因→attribution.json）→ run.py learn（非fd联赛增量拟合+版本发布）** → dc_fit 重拟合。回填时自动增补 `pinClose/pinSource`（fd Pinnacle 收盘三键匹配·日±1+比分+联赛→去水三向，ambiguous 诚实降级；F3/F4 市场锚判别地基），v4.7 起新轮 matches[] 带 `lambdaHome/lambdaAway/fusedPre/chainSteps`（归因 F1/F5 精确判别） → 复盘报告归档 → git commit | 复盘摘要（方向/比分/CLV/教训+学习就绪度）+ tickets.html 实票账本（资金曲线/玩法分解）+ trend.html 胜率趋势（4折线+校准图+方案准确率）+ attribution.json（偏差归因账本：F5/F9/F10 因子分布） |
| **"我买了/出票了"** | 方案转正（复制当轮方案legs冻结赔率+时间戳）或手动建档（自组票报票面）→ 写 `data/06-tickets/tickets.json` + 日期JSON挂指针 → git commit（**git 历史=出票凭证**；体彩出票后票面不可改写） | 实票登记确认 |
| **"XX队近况？"** | 本地知识库检索（00-leagues/01-teams/04-summaries）→ 直接回答 | 纯文本答案 |
| **"跑下回测"** | run.py backtest → 与市场基线对比 | RPS 对照表 |
| **"帮我复核这场 XX"** | football-live-assessment skill（单场临场评估：数据核验→伤停首发→概率重估→玩法比较→风险结论，**只分析不下注**；读仓库缓存，旧缓存须标注数据模式，与主 skill"临场复核"分工=单场深挖 vs 全流程出票） | 临场评估报告（证据等级+概率区间+EV 门槛） |

约定：预测命令自带数据刷新（用户永远不用手动 run.py）；每次流程结束自动 commit（git 历史=预测锁定凭证）。
**命令路径铁律**：run.py 不在仓库根目录，主入口一律 `python engine/scripts/run.py <子命令>`（update/verify/corpus/learn/backtest）；其余脚本同在 `engine/scripts/`。
**单写会话纪律（2026-08-25 两次 21 提交大冲突教训 47e1f87/8f481c5）**：预测/回填写会话开始先 `git pull`；同一时间只开一个写会话，开新会话前确认旧会话已 commit。

## 项目定位

本地足球知识图谱 + Dixon-Coles/Elo 统计模型 + 检索增强预测（Agentic RAG）+ 趋势挖掘引擎。
设计文档：`docs/2026-08-21-rag-design.html`（唯一权威版本）。

## 目录结构（= 四层架构）

- `data/00-leagues/`: 联赛画像 JSON（积分榜/场均进球/主胜率/冷门率/TOP比分/争冠保级格局；league_profile.py 自动生成，预测时 ESPN 实时覆盖）
- `data/01-teams/`: 球队画像 JSON（Elo/xG/近况/伤停/主客场/休息天数）+ `_aliases.json` 实体映射表 + `_index.json` 路由索引
- `data/02-results/`: 赛果回填 JSON（**主文件=出票冻结终审版，`-rN`=过程快照，corpus 同场覆盖以主文件为准**；`_archive/` 空壳归档）+ `league/` 本地赛果库（espn history 回填，供非fd联赛 DC 拟合；韩职走体彩 league-results）+ `_h2h_index.json`（仅叙事参考）
- `data/03-predictions/`: 预测报告 HTML（仅用户输出用 HTML+SVG）
- `data/04-summaries/`: 五维统计 `_stats.json` + 复盘 HTML + `corpus.json` 语料就绪度 / `attribution.json` 偏差归因账本 / `ablate-report.json` 系数消融 / `goal-engine-report.json` 进球引擎对照统计（09-27 评审数据底子：四线对照+消融+walkForward+bypassPool 四节；读口径警示见 notes）
- `data/05-trends/`: 赛前情报时序库 intel-timeline（odds 五池 diff 链/intel 摘要/livescan 扫描事件；刷新自动落盘+回填挂 preSnapshots 桥；设计=docs/2026-08-30-intel-timeline-design.html）
- `data/06-tickets/`: 实票账本 `tickets.json`（票=顶层实体不按日切：形状/腿/出票赔率冻结/结算/纪律事件；**实票=有结算记录的票，其余全是方案推演**；派彩按形状算，4串11中2关只回1注2串1）+ `preference.json` 实票偏好档案（静态手写：腿数→形状映射/玩法带/注金/对冲；**skill 组票与实票登记时读，代码不读**；与 SKILL.md 偏好表双源分工=形状查表·口味查档案；设计=docs/2026-08-27-ticket-preference-design.html）+ `tickets.html` 报告（结算时重刷：资金曲线/票务清单/玩法分解/纪律对照；设计=docs/2026-08-25-tickets-design.html）
- `engine/scripts/`: Python 脚本（**run.py 主入口**：update/verify/corpus/learn/backtest 一键子命令，update 含 dump-odds 存档留档——boldplay 已改读实时清单，score_odds 存档仅历史档；dc_fit/dc_predict/elo_fetch/xg_fetch/odds_fetch/backfill/calibrate/build_index/band_calibration 概率带校准/score_ev 比分EV审计/live_odds_probe 临场价源探测/**boldplay 阶梯出票卡生成+settle推演结算（v5.6三档制：保底HAD 4串11+翻身多池引擎seq轮换+彩票档HAD/HHAD N串1×1倍=2元合格腿全上4~8串·p_fused≥0.55/超低赔≤1.25门槛·无预算管理独立轮红线·逐场三池玩法卡pools_card·--structure=legacy对照一个月·**09-04批次1-3：主数据流读实时清单+--matchday/--exclude过滤+彩票档DC分歧熔断+approved未拍板默认false（settle跳过）**）**/ticket_report 实票报告/freq_band 比分选法模块/**goal_engine 进球引擎轨P0（双轨分化设计：滚动无泄漏特征层+λ逐项加权乘子+修正比分矩阵+TTG/CRS两出口对照统计+walk-forward分段重拟合；--compare/--walk-forward，产出 data/04-summaries/goal-engine-report.json=09-27评审数据底子；⚠️重跑--compare会抹walkForward/bypassPool节）**/**attribute 规则归因引擎（错题→F1/F3/F4/F5/F9/F10 因子→attribution.json，docs/2026-08-29-attribution-design.html）+ pin_close fd 收盘三键匹配（日±1+比分+联赛→pinClose 落盘，F3/F4 市场锚地基）**）+ `research/` 一次性研究脚本归档（ρ分诊/Elo与xG验证/市场筛选等，2026-08-25 审计归档）
- `engine/scripts/scratch/`: 临时分析脚本目录（已 gitignore 不进 git；会话结束清理；PowerShell 下**禁止 python -c 内联多行脚本**——转义坑 09-04 一日三次实证，一律写 .py 脚本文件执行）
- `engine/cache/`: DC 参数缓存（{league}_dc.json）+ `models/` 版本化存档（{league}_dc_v{n}.json+.meta+latest.json，holdout 门槛发布）+ fusion.json 融合系数 + `score_odds/` 体彩全玩法赔率日存档 + live_odds_feasibility.json 临场价源结论（2026-08-24 探测：pinnacle 直连被墙/the-odds-api 需 key → 层1 降级上轮收盘先验）
- `skill/SKILL.md`: 预测 skill v5.8（junction 到 ~/.claude/skills/football-betting-prediction；实票×推荐对齐系统同推制+平局轨入轨五条含 poolSingle.HAD=1 资格前置）+ `skill/references/` 外置参考（系数详表/官方玩法规则/教训档案，skill 正文按需加载）
- `skill/football-live-assessment/SKILL.md`: 单场临场评估 skill（junction 到 ~/.claude/skills/football-live-assessment），用于官方首发/伤停核验、胜率重估及胜平负/让球/比分玩法比较；`README.md` 仅作详细使用说明，不替代 SKILL.md。
- `docs/`: 设计文档

## 检索铁律

1. 预测时先查本地 `data/01-teams/_index.json`，只搜本地缺的数据（联赛分组批量）
2. Dixon-Coles 缓存过期（新增比赛 ≥5 场）→ 调 `engine/scripts/dc_fit.py` 重拟合（ξ=0.005 默认）
3. 概率锚 = Pinnacle 收盘价去水；体彩赔率只用于可买性/奖金/CLV 计算
4. 融合：p_final = σ(0.4·logit(p_DC) + 1.0·logit(p_pinnacle))，系数存 engine/cache/fusion.json
5. H2H 交锋仅叙事参考，不进预测权重（学术验证预测力垫底）
6. 多源球队名经 `data/01-teams/_aliases.json` 规范 ID 解析；球队文件名用英文规范 ID（如 lech-poznan.json）
6.5 HTML 文件必须含 `<meta charset="utf-8">`（开头 `<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">`），否则浏览器 GBK 乱码
7. 读取本地文件先校验 lastUpdated；过期数据刷新或在报告标注"数据截至 X 日"
8. 索引文件只做路由表（一行一条目，不放内容本体）；索引可由 build_index.py 重建，glob 永远可兜底
9. 内部文件 JSON/纯文本，禁止 HTML/SVG；仅用户报告用 HTML+内嵌 SVG
10. 报告附数据来源清单（引用了哪些本地文件），供忠实度抽查

## 数据源（2026-08-21 实测可用性）

| 源 | 状态 | 用途 |
|:---|:---|:---|
| 体彩官方 API | ✅ WebFetch 可用 | 赛程 + 赔率（**dump-odds 子命令：全玩法赔率日存档 engine/cache/score_odds/，含比分31项/总进球/半全场/胜平负+调价时间戳**）+ **赛果**（sporttery_fetch.py：`league-results` 联赛历史=zqlszl 口径 90天分段；`results` 开奖口径=zqsgkj 按场次编号"周六028"对票，ESPN 互备；含韩职 korea=86 等 fd/ESPN 缺失联赛）+ **单场情报 `insight <matchId>`**（zqdz 口径：伤停/近10场/即时排名/H2H/射手，伤停首选源免搜索配额） |
| football-data.co.uk | ✅ requests 直连（engine/scripts/odds_fetch.py） | **Pinnacle 收盘价（PPCH/PPCD/PPCA）+ B365 收盘 + xG（HxG/AxG）+ 比分**，主流联赛当季 CSV |
| ESPN API | ⚠️ 赛果接口 2026-08-22 起停摆（backfill 已切体彩编号对票主链路，恢复后 ESPN 自动回为兜底）；requests 直连（engine/scripts/espn_fetch.py，**勿加浏览器 UA 会 403**） | 赛果（按日期）、实时积分榜；覆盖日职 jpn.1/瑞超 swe.1/挪超/丹超/沙特 ksa.1/荷甲/葡超等 fd 不含联赛 |
| titan007（球探体育） | ✅ requests 直连（engine/scripts/cn_fetch.py，须带浏览器 UA+Referer 否则 442；国内速度快，ESPN 不可达时兜底） | 联赛积分榜（JS 数组直取）+ 中英文队名对照（teams 子命令补别名用）。ID：36英超 31西甲 8德甲 11法甲 16荷甲 23葡超 25日职 26瑞超 22挪超 7丹超 13芬超 292沙特 |
| clubelo.com | ⚠️ api 子域被墙；主域可达（elo_fetch.py 双链路自动切换：api CSV → 主域 HTML 正则） | Elo；主域仅"近期有比赛"的活跃队有页面，休赛期队失败属正常（21/25 实测成功） |
| understat.com | ⚠️ 2026 赛季数据未开（xg_fetch.py 备选） | xG（主链路已走 fd CSV） |
| 500.com | ⚠️ 可达但积分榜无结构化接口（页面数据灌不进 HTML 表） | 备选参考 |
| 搜索引擎 | ❌ 配额耗尽至 8-31 | 补充检索 |

> fd CSV 覆盖：英西德意法荷比葡土希俄主流联赛；沙特/日职/北欧/南美不覆盖（此类场次预测时 Elo/xG/收盘价标锚缺失，但积分榜可用 espn_fetch/cn_fetch 直连补齐）。

## 评估铁律

- 主指标：CLV（逐单算：出票赔率/Pinnacle 收盘赔率 - 1）+ RPS + log loss
- 命中率仅作展示；修正系数须消融回测，无正增益即删（首回合平局保护=首批验证对象）
- 回测一律 walk-forward，成交按 Pinnacle 收盘价计
- 检索覆盖率：每轮记录"本地命中 vs 联网补采"字段清单，发现知识库采集洞

## 升级触发线（现在不做）

- `data/02-results/` 超 500 个日期文件（约两赛季）→ 迁移 SQLite FTS5（队名+日期索引），其余目录保持文件
- corpus 就绪度 n≥100（已回填赛果）→ 实现 P2 calibrate.py 融合重校（a≤0.6 护栏）；n≥50 → ablate.py 系数消融（人审 diff）——门槛读 data/04-summaries/corpus.json readiness
- preference.json sampleSize ≥ 20（实票积累约两月）→ 结构化维度（玩法占比/联赛权重/形状分布）从 tickets.json 自动重算替代手写——6 票样本统计噪声大，先静态维护
- ~~韩职历史赛果无 ESPN 数据源~~ ✅ 2026-08-23 已解决：体彩票源接入（sporttery_fetch.py league-results korea，597 场三季回填 + dc_fit 发布 v1）
