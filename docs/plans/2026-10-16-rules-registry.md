# 经验规则化系统（rules-registry）Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 教训从 memory 走向强制核心逻辑——规则册三段生命周期（候选→简报影子→硬闸）＋结算回测规则战绩＋context 五层控制。

**Architecture:** 四件新物（registry.json 规则册 / rule_brief.py 编译器 / rule_settle.py 结算钩子 / leg_gate.py 硬闸）＋ llm_pred 修正卷。设计文档 `docs/plans/2026-10-16-rules-registry-design.html`（判据已冻结 §3）。

**Tech Stack:** Python 3.14 / pytest。无新依赖。

**执行方式:** 本会话直接逐任务实施（SDD 台账同步），每任务 TDD＋独立 commit。

---

## Task 1: 规则册骨架＋首批 19 条入册＋迁移审计表

**Files:** Create `engine/cache/rules/registry.json`, `engine/cache/rules/migration_audit.json`; Test `engine/tests/test_rules_registry.py`

1. 写失败测试：schema 校验（id 唯一/三段不重/stage-form 合法组合/gate 必带 trigger/record 字段齐）＋迁移审计表覆盖检查（凡 SKILL/memory/CLAUDE 列入首批判条目必须有去向行）。
2. registry.json 种子：R001-R008（T048 八教训，stage=shadow，拍板①=设计落档 f53b89e5 视同）；R009-R019（历史迁移，gate-with-enforced-by 者 stage=gate 且 trigger 标 upstream 不可检）。
3. migration_audit.json：逐条 source→ruleId 或 留原地理由（如"报命中率三件套=汇报纪律非预测流程"）。
4. memory 侧指针：本次不逐文件改 memory（量大），审计表记"指针补录待下轮"——诚实优先。
5. 全量测试＋commit。

## Task 2: rule_brief.py 编译器＋回执命令

**Files:** Create `engine/scripts/rule_brief.py`; Test `engine/tests/test_rule_brief.py`

1. `brief <date>`：active_shadow 全量（id|text|战绩|why 一行）＋候选池 id+text 简列（cap 10）→ `engine/cache/rules/briefs/{date}.md`；shadow 注入硬顶 15，溢出按 record.saved 降序，尾部列未注入 id。幂等：当日已有回执则拒覆盖（保护回执）。
2. `receipt <date> --rule R001 --action "拦下 周四013 HAFU hh(拟入3串)"`：结构化追加回执行（rule|action|code|pick 解析自 action）。
3. 测试：编译格式/硬顶/溢出排序/回执追加幂等。

## Task 3: rule_settle.py 结算钩子（拦截账＋三模式检测）

**Files:** Create `engine/scripts/rule_settle.py`; Test `engine/tests/test_rule_settle.py`

1. `settle <date>`：读 briefs/{date}.md 回执 → 被拦腿对赛果判死活（HAD 方向/TTG 总球/CRS 精确/HAFU 有半场则判无则 unknown）→ 更新 record{rounds,blocked,saved,harmed}。rounds 计该简报含的全部 shadow 规则。
2. 三模式检测扫当轮已结算票：①HAFU 过关腿死（market=hafu 且票腿≥3 且 miss）②贴平腿死（|q−1/odds|<2pt 且 miss）③深水腿死（score_odds 档案算池返还率<85% 且 miss）→ 给匹配 trigger 的候选规则 evidence 追加。检测器只加权不立新候选。
3. 生命周期自动动作：harmed≥2 的 gate 规则降 shadow 标红；候选 30 天无新证据标 stale；20 轮零触发标灰（写 flags，不动 stage——降级须主公）。
4. 测试：回执判死活三类/模式检测三例/自动标旗。

## Task 4: leg_gate.py 硬闸

**Files:** Create `engine/scripts/leg_gate.py`; Test `engine/tests/test_leg_gate.py`

1. 从 registry 读 stage=gate 规则，按 trigger 类型分派检查器：`play+parlayMin`（HAFU≥3 串）/`deviationPtMax`（腿有 q+odds 时）/`poolPayoutMin`（传入池赔率时算返还率）。trigger 类型未知或数据缺 → 记 skip 不误拦。
2. `check_ticket(ticket) -> violations[]`＋CLI `check <ticket.json路径>`。退出码 0/1。
3. 注册链接线：backfill.settle_tickets 结算前不拦（票已出不可撤），闸在**登记前**——本任务先交独立模块＋测试；首条 gate 规则升闸（约 5 轮后）时再接 paper.register/add-ticket 调用点，接线记台账。
4. 测试：三 trigger 各拦一例＋skip 不误拦＋exit code。

## Task 5: llm_pred 修正卷（-rN）＋消费方读卷序

**Files:** Modify `engine/scripts/llm_pred.py`, `engine/scripts/llm_eval.py`, `engine/shadow/paper.py`(load_llm_preds); Test `engine/tests/test_llm_pred_v2.py` 追加

1. `freeze(sale_date, preds, revision=None, reason=None)`：revision=N 写 `{date}-r{N}.json`，带 `revisionOf`+`revisionReason`；主卷存在与否皆可写修正卷（修正卷不必先有主卷）。
2. 消费方读卷序 `-rN 优先`（取最大 N）：llm_eval._load_day_rows、paper.load_llm_preds。
3. 测试：修正卷写入/读卷优先/无修正卷回退主卷。

## Task 6: 全链接线＋验收

1. llm_card.py gen 末尾 best-effort 调 rule_brief.brief（try/except 不熔断）。
2. backfill.py main 末尾 best-effort 调 rule_settle.settle（同）。
3. 手工演练一轮：brief 1016 → 模拟回执 → settle → registry record 变化 → 审计三模式。演练数据用测试夹具，不污染真册（或演练后回滚 record）。
4. 全量 pytest＋SDD 台账收口＋commit。

## Ruling

- **R1（检测器第三模式数据面）**：贴平检测用腿上 q 与 1/odds 近似（非完整去水），误差可接受——检测器只加权不立规，宁粗勿缺。深水用 score_odds 档案，缺档跳过该票。
- **R2（memory 指针延后）**：Task 1 首批不逐条改 memory 文件（19 处编辑量大且易错），审计表记指针债，下轮专项补。
- **R3（leg_gate 接线延后）**：首 5 轮无 gate 可升（§3 判据），接线提前无意义——YAGNI。
