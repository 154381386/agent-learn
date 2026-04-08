# IT Ticket Agent 下一阶段多 Agent 演进计划（V3）

## 文档定位

本文档不是用来替代当前这轮“单领域落地 / 少工具 / 可靠 runtime 收敛”的总计划与实施进度文档，
而是作为**当前收敛阶段基本完成后的下一阶段路线图**，回答下面这个问题：

**当当前单领域生产化主线收口后，系统下一步应该按什么顺序进入快速响应、架构修正、多 Agent 并行支撑和生产基建阶段。**

配套文档关系如下：

- 当前收敛总计划：`projects/it-ticket-agent/docs/生产级Agent演进总计划.md`
- 当前实施进度：`projects/it-ticket-agent/docs/生产级Agent实施进度.md`
- 下一阶段路线图：本文档（V3）

本版本相对 V2 的主要修正：

- 明确承认当前代码仍是**单路由 / 单 Agent / 单主动作执行** runtime，而不是已具备多 Agent 闭环
- 将 `QueryClassifier` 从“完全独立路径”修正为“runtime 内 fast path”
- 在多 Agent Phase 之前补入 **Agent Contract / Factory** 与 **Parallel Dispatcher + Aggregator**
- 将测试与可观测性从“最后补”调整为“伴随架构升级同步建设”
- 保留 `Executor Layer` 为关键阶段，但把它放回更完整的依赖链上

---

## 当前基线

在进入下一阶段之前，当前项目已经具备以下基础能力：

- 会话、interrupt、checkpoint 持久化
- 上下文装配与 session / process / incident memory
- 单领域审批工作流与恢复执行
- `ExecutionPlan` / `ExecutionStep` / execution checkpoint
- system event 事件账本
- 执行安全基础约束（动作注册、参数校验、审批快照绑定）
- 最小 smoke runtime 已可运行

但也必须明确承认当前代码的真实边界：

### 1. 当前仍是单 Agent 主链路

- `Supervisor` 当前只会在少量候选中选择一个 Agent
- ticket graph 仍是 `supervisor_route -> domain_agent -> clarification_gate -> approval_gate -> finalize`
- 还没有正式的并行分发、并行回收、聚合仲裁节点

### 2. 多 Agent contract 只是“预留”，还不是“生效”

- `TaskEnvelope.mode` 已预留 `fan_out / pipeline`
- `IncidentState` 已预留 `subagent_results / approved_actions / verification_results`
- 但这些字段目前主要是为后续演进预留，主链路仍未真正消费多 Agent 并行语义

### 3. Clarification 仍在 graph 外层承担业务判断

- 当前 `clarification_gate` 仍直接判断 `service` 是否缺失
- 这对单链路可用，但会在多 Agent 并行下放大 graph 层的领域耦合

### 4. 审批可以聚合 proposal，但执行仍是“过渡态”

- 当前 `ApprovalCoordinator` 已具备 proposal 收集、去重、审批建模能力
- 但审批恢复后，执行节点仍是 `execute_approved_action_transition`
- 即使审批单里有多个 proposal，当前也只执行 primary proposal，其余只是过渡性处理

### 5. 当前测试以单链路 smoke 为主

- 当前已有一套覆盖 clarification / approval / checkpoint / execution safety 的 smoke tests
- 这说明收敛阶段基础 runtime 已经站住
- 但多 Agent contract、并行编排、聚合审批、统一执行等能力还没有对应测试护栏

因此，下一阶段的重点不再是“先把基础 runtime 做出来”，而是：

1. **在不破坏当前 runtime 一致性的前提下，增加 fast path**
2. **先把会影响扩展的架构缺口补齐**
3. **再进入真正的多 Agent 并行分析与协调执行**
4. **同时把测试和可观测性前移，避免后续重构失控**

---

## 规划原则

### 1. 先承认当前是“单 Agent runtime”，再往多 Agent 演进

- 不把预留字段误判成已具备能力
- 不直接在单链路 graph 上叠加越来越多的多 Agent 特判

### 2. 快速路由可以先做，但必须保留 runtime 一致性

- `QueryClassifier` 可以前置
- 但不能演变成绕开 session / turn / event / interrupt 的旁路系统
- fast path 也要进入统一 runtime 账本

### 3. 先补 contract，再补 registry，再补并发

- 如果 `BaseDomainAgent` 还没有正式 descriptor / clarification / capability contract
- 太早上 YAML registry，配置会和代码形成双轨漂移

### 4. 多 Agent Phase 之前，必须先有 Dispatcher 和 Aggregator

- 没有并行分发与聚合器，就不存在真正的多 Agent 分析阶段
- `ClarificationCollector`、`ConflictDetector`、`ConflictArbiter` 都依赖这一步

### 5. 执行必须继续收敛到独立 Executor 层

- SubAgent 职责：分析 + 建议
- Aggregator 职责：综合多个 Agent 结果
- ApprovalGate 职责：风险评估与审批拦截
- Executor 职责：统一执行、重试、补偿、证据链

### 6. 测试和可观测性要伴随架构改造同步推进

- 否则 registry、dispatcher、executor 这类结构性改造很容易把当前已收敛的 runtime 打坏

---

## Phase 0：前置 Fast Path（可并行实施）

目标：让简单 FAQ / 文档查询 / 已知 runbook 问题更快返回，但仍保留统一 runtime 入口。

### 0. QueryClassifier

来源问题：`简单问题怎么快速响应`

当前判断：

- 当前复杂路径默认都会进入 Supervisor + Agent 主链路
- 这对复杂工单是合理的，但对 FAQ / 文档查询偏重
- 当前项目已有 RAG 服务可调用，因此适合增加前置 fast path

需要修正的前提：

- `QueryClassifier` 不应被设计为“完全独立于 runtime 的直连路径”
- 更合适的设计是：**在 orchestrator 内部增加一个 fast path 分支**
- fast path 也要写入 session / turns / system events，避免形成两套行为模型

改造目标：

- 前置 QueryClassifier：规则库 + 小模型混合分类
- `SIMPLE`：进入 fast path，直接调用 RAG / runbook answer 组装回复
- `COMPLEX / UNKNOWN`：进入当前 Supervisor + Agent 主链路

建议交付物：

- `runtime/query_classifier.py`
  - 规则引擎
  - 小模型分类
  - 分类结果：`SIMPLE / COMPLEX / UNKNOWN`

- `runtime/query_classifier_rules.yaml`
  - FAQ pattern
  - 文档查询关键词
  - 已知故障入口

- `runtime/orchestrator.py` 改造
  - 分类结果写入 session memory / process memory / system event
  - `SIMPLE` 仍通过统一 response contract 返回
  - `COMPLEX / UNKNOWN` 进入当前 graph

- 可观测性埋点
  - classifier 命中率
  - fast path 命中率
  - fast path 降级率
  - fast path 平均延迟

完成标准：

- fast path 不绕开 session / turn / event 账本
- SIMPLE 问题可明显快于复杂路径
- 规则库可快速迭代，新规则无需改代码
- 先基于真实流量统计再决定命中率目标，不预设固定比例

---

## Phase 1：单 Agent 框架修正

目标：把当前仍带有单领域、单链路、硬编码装配假设的设计点收口为正式框架能力，为后续并发和多 Agent 做准备。

当前状态（2026-04-08）：

- **Phase 1 已完成第一版收口，可视为完成并进入 Phase 2**
- `Agent Contract + Clarification 下沉`、`Agent Factory / Registry`、`M4 执行控制深化第一版`、`测试与最小可观测性前移` 均已具备对应代码与测试落点
- 保守尾项仅剩 graph integration tests 仍偏 runtime smoke 化，但这不再阻塞进入 Phase 2 主干开发

### 1. Agent Contract + Clarification 下沉

来源问题：`clarification 放哪`

当前判断：

- 当前 `BaseDomainAgent` 只有 `run()` 接口，contract 过薄
- 当前 graph 外层直接判断 `service` 缺失，属于领域逻辑泄漏
- 如果不先修 contract，后续 registry 和多 Agent 会建立在脆弱边界上

改造目标：

- `BaseDomainAgent` 增加正式 descriptor / validation / clarification contract
- 缺字段判断由 Agent 内部完成
- graph 层只保留统一 interrupt 接入点与状态转换，不再判断领域字段

建议交付物：

- `agents/base.py` 扩展
  - `descriptor`
  - `required_fields`
  - `validate_context(ctx) -> ValidationResult`
  - `build_clarification_request(...) -> ClarificationRequest`

- 新增领域模型
  - `FieldRequirement`
  - `ValidationResult`
  - `ClarificationRequest`

- `agents/*` 显式声明最小运行字段

- `graph/` 改造
  - 移除外层通用 clarification 业务判断
  - 只保留统一 interrupt materialization
  - clarification 输出改为结构化 contract

完成标准：

- graph 层不再直接判断 `service` 之类领域字段
- 新 Agent 不需要改 graph clarification 分支
- clarification request 为正式结构，不是 prompt 拼接文本

---

### 2. Agent Factory / Registry

来源问题：`新 Agent 要什么`

当前判断：

- 当前 orchestrator 仍在代码里硬编码实例化 Agent
- 如果直接跳到 YAML registry，而没有 Agent descriptor / factory，会形成“配置和代码两套真相”

改造目标：

- 先收口 `AgentDescriptor + AgentFactory`
- 再引入 YAML 声明式 registry
- Supervisor 路由逐步改为查询 registry 元信息，而不是写死候选

建议交付物：

- `agents/descriptors.py`
  - Agent 元信息模型
  - capabilities / tools / routing metadata / required_fields

- `agents/factory.py`
  - 根据 descriptor + settings + dependency wiring 构造 Agent

- `agents/registry/`
  - 每个 Agent 一个 YAML

- `agent_registry/loader.py`
  - 扫描 registry
  - schema 校验
  - 构造 descriptor
  - 与 factory 对接

- `runtime/orchestrator.py` 改造
  - 从硬编码装配切到 registry + factory

完成标准：

- 新增 Agent 时无需修改 orchestrator 主干装配逻辑
- registry schema 坏配置可在启动期失败
- descriptor 是 canonical model，YAML 只是来源之一

---

### 3. M4 执行控制深化   暂时跳过

来源问题：`rollback 半路失败`

当前判断：

- 当前已有 `ExecutionPlan / ExecutionStep / checkpoint / execution safety`
- 但仍主要偏“执行记录”，还不是“执行策略控制器”
- 这一步是后续 Executor Layer 的直接前置

改造目标：

- 从“记录执行过什么”升级到“定义如何执行与如何恢复”
- 支持 step dependency、retry policy、补偿策略、恢复提示

建议交付物：

- `execution/models.py` 深化
  - `ExecutionStep` 增加 retry / dependency / compensation 字段
  - `ExecutionPlan` 增加 plan-level recovery metadata

- `execution/retry_policy.py`
- `execution/compensation_policy.py`
- `execution/executor_interface.py`
  - 先定义 contract，不急于完整实现

- checkpoint / recovery 升级
  - 支持从 step 继续
  - 失败后返回“失败 step / 原因 / 下一步建议”

完成标准：

- 至少支撑 3+ step 执行计划
- 执行失败后能定位失败 step
- 后续 Executor 所需 contract 已稳定

代码迁移注意：

- 当前图节点里的执行安全检查仍可保留
- Phase 2 Executor 完成后再迁移
- 过渡期允许双层校验

---

### 4. 测试与可观测性前移

来源问题：`怎么确保后续重构不把当前 runtime 打坏`

当前判断：

- 当前已有最小 smoke tests，但覆盖范围仍以单链路为主
- registry、dispatcher、executor 这类改造属于结构性变化，不能等到最后再补测试

改造目标：

- 在 Phase 1 就补齐 contract-level tests 和 graph integration tests
- 同步增加关键路径指标，方便回归期间定位问题

建议交付物：

- 测试分层
  - Agent contract tests
  - registry loader tests
  - approval / execution safety tests
  - graph integration tests

- 最小可观测性
  - routing decision
  - clarification created / answered
  - approval requested / decided
  - execution started / failed / completed

完成标准：

- 关键重构点有回归护栏
- 能快速判断是 registry、graph、approval 还是 execution 出问题

当前进度（2026-04-08）：

- 已补齐 `tests/test_agent_registry.py`、`tests/test_execution_contracts.py`、`tests/test_runtime_smoke.py`，其中 registry loader、execution contract、approval / execution safety、主链路 smoke 已具备第一版护栏
- 已落地最小可观测性第一版：`routing_decision`、`clarification_created / answered`、`approval_requested / decided`、`execution.plan_created`、`execution.started / execution.step_started / execution.step_finished`、`run_summary`、`conversation.closed` 已进入 system event 账本
- 已在 `runtime/orchestrator.py`、`runtime/supervisor.py`、`graph/nodes.py`、`agents/base.py`、`llm_client.py` 接入第一版 tracing spans，已能覆盖 orchestrator / supervisor / graph node / agent / tool / LLM generation 的关键路径
- 当前仍未完全收口：graph integration tests 仍主要体现在 runtime smoke 中，尚未形成更明确的分层集成测试边界；此外 metrics / alerts 仍未进入本阶段交付物

阶段判断：

- 本节“测试与最小可观测性前移”可视为 **已完成第一版落地，但仍建议保留回归扩展空间**

---

## Phase 2：多 Agent 分析框架

目标：让系统从“单 Agent 主链路”升级为“可并行分发、可聚合、可协调”的正式多 Agent 分析框架。

### 5. Parallel Dispatcher + Aggregator

来源问题：`当前还没有真正的多 Agent 主干`

当前判断：

- 没有 dispatcher，就没有 fan-out
- 没有 aggregator，就没有 fan-in
- 后面的 ClarificationCollector / ConflictDetector / Approval 聚合都无法落地

改造目标：

- 支持从 Supervisor 输出多个 candidate agents
- 并行执行多个 SubAgent
- 聚合多个 `AgentResult` 为统一 incident-level synthesis

建议交付物：

- `orchestration/parallel_dispatcher.py`
  - 根据 routing decision 并发调度 Agent
  - 控制最大并发数、超时、失败隔离

- `orchestration/aggregator.py`
  - 聚合 findings / evidence / recommended_actions / open_questions
  - 形成统一中间结果

- `graph/` 改造
  - 新增 fan-out / fan-in 节点
  - 从单 `agent_result` 演进为 `subagent_results + aggregated_result`

完成标准：

- 至少支持 2-3 个 Agent 并行分析
- 某个子 Agent 失败不导致整轮分析直接崩溃
- 后续审批、冲突、验证都消费聚合后的结构化结果

当前进度（2026-04-08）：

- 已落地第一版 `orchestration/parallel_dispatcher.py` 与 `orchestration/aggregator.py`
- ticket graph 已新增 `dispatch_subagents -> aggregate_subagent_results` fan-out / fan-in 路径，并保持单 Agent 主链路兼容
- Supervisor 已能在明确多领域线索下自动输出 `mode=fan_out + candidate_agents`，不再只支持手工注入 fan-out 决策
- 当前已启用 `network_agent` 进入默认 registry，可与 `cicd_agent`、`general_sre_agent` 形成真实 2~3 agent 并行分析场景
- approval gate 当前已统一消费聚合后的 `incident_state / aggregated_result / source_agents` 上下文；审批恢复后的 session / case / run_summary 也已对齐到聚合结果视角
- 已补充针对 dispatcher 隔离失败、aggregator 聚合去重、Supervisor 自动 fan-out、聚合审批上下文收口的测试
- 后续未完成部分已下沉到下一步：`ClarificationCollector / ConflictDetector / ApprovalCoordinator 扩展`，不再属于本节自身阻塞项

阶段判断：

- 本节当前状态可标记为 **已完成第一版收口**，已经形成可运行的多 Agent fan-out / fan-in 主干

---

### 6. ClarificationCollector

来源问题：`并行缺字段`

当前判断：

- 多 Agent 并行后，clarification 诉求可能来自多个 Agent
- 需要在 Aggregator 之后统一合并

改造目标：

- 聚合多个 `ClarificationRequest`
- 去重同名字段
- 统一排序和一次性 interrupt

建议交付物：

- `orchestration/clarification_collector.py`
- `MergedClarificationRequest` 模型

完成标准：

- 同一轮分析最多只发起一次 clarification interrupt
- 用户补充后，所有 Agent 都能看到更新后的字段

---

### 7. ConflictDetector

来源问题：`操作冲突`

当前判断：

- 多 Agent 聚合后，才会出现真正的 proposal 冲突识别需求

改造目标：

- 建立资源冲突矩阵
- 检测显式互斥、共享资源、隐式依赖

建议交付物：

- `orchestration/conflict_detector.py`
- `orchestration/conflict_matrix.yaml`

完成标准：

- 冲突结果进入结构化输出
- 新规则可声明式扩展

---

### 8. ConflictArbiter

来源问题：`谁来决定冲突怎么解`

当前判断：

- 冲突不是都要拒绝，有些可以排序解决

改造目标：

- 对冲突结果进行结构化仲裁
- 输出建议顺序、并行组、互斥说明和保守方案

建议交付物：

- `orchestration/conflict_arbiter.py`
- `ArbitrationResult` 模型

完成标准：

- 输出是可被 Approval / Executor 直接消费的结构化 contract

---

### 9. ApprovalCoordinator 扩展

来源问题：`多审批合并`

当前判断：

- 当前 `ApprovalCoordinator` 已有 proposal 收集与去重基础
- 但还缺少真正面向多 Agent 聚合审批的 contract

改造目标：

- 生成聚合审批单
- 支持全批、选批、全拒
- 决策精确映射回 proposal 与后续执行计划

建议交付物：

- 扩展 `approval/coordinator.py`
  - `coordinate_multiple_proposals()`

- 新增数据模型
  - `AggregatedApprovalRequest`
  - `ApprovalDecision`

完成标准：

- 一个会话可以生成一份聚合审批单
- 聚合审批结果能驱动后续 execution filtering

---

## Phase 3：统一执行与验证闭环

目标：让系统从“多 Agent 分析”升级到“统一执行、统一验证、统一重试”的正式闭环。

### 10. Executor Layer

来源问题：`多 Agent 执行怎么协调`

当前判断：

- 这是当前最关键的结构性缺口
- 当前审批恢复后的执行仍是过渡节点
- 没有 Executor，前面的多 Agent proposals 最终还是会坍缩回单点执行

改造目标：

- 将执行从 graph 过渡节点中独立出来
- 统一处理顺序控制、pre-check、重试、补偿、证据链
- 正式消费 ConflictArbiter 与 ApprovalCoordinator 的结构化输出

建议交付物：

- `execution/executor.py`
- `execution/execution_orchestrator.py`
- `execution/pre_execution_validator.py`
- `execution/step_executor.py`
- `execution/failure_recovery.py`

完成标准：

- 多个 approved actions 可按顺序 / 并发组正确执行
- 执行失败可部分重试或升级人工
- 执行安全检查从 graph 节点迁移到 Executor

---

### 11. IncidentVerifier

来源问题：`谁来验证原始故障是否已解决`

改造目标：

- 引入独立验证角色
- 验证的是“事故是否恢复”，而不只是“动作是否成功”

建议交付物：

- `verification/incident_verifier.py`
- 验证规则配置

完成标准：

- 验证逻辑独立于执行逻辑
- 验证失败可结构化回传原因

---

### 12. IncidentLoop

来源问题：`怎么循环重试`

改造目标：

- 建立验证失败后的闭环控制器
- 支持多轮尝试、上下文保留、人工升级边界

建议交付物：

- `orchestration/incident_loop_controller.py`
- `IncidentLoopState`
- `LoopDecision`

完成标准：

- 能区分自动继续、人工接管、最终成功
- 每轮保留失败原因与历史动作

---

## Phase 4：生产基建

目标：让系统从“能跑闭环”升级到“能长期稳定运行、可管可审可扩展”。

### 13. Auth / RBAC

改造目标：

- API 认证
- 审批权限绑定
- 数据隔离
- 审计日志

完成标准：

- 非授权用户不能读取、审批或恢复不属于自己的会话
- 审批人身份进入正式审计链路

---

### 14. 存储迁移

改造目标：

- 引入 repository 抽象
- 支持 SQLite / PostgreSQL 可切换

完成标准：

- 业务层不直接依赖 SQLite 细节
- 主逻辑不变即可切换存储后端

---

### 15. 完整可观测性

改造目标：

- 在 Phase 1 的最小可观测性基础上，补齐 tracing / metrics / alerts

建议最小覆盖：

- session create / resume
- routing / fan-out / aggregation
- approval
- execution
- verification
- incident loop

完成标准：

- 关键链路可追踪
- 异常可定位到具体阶段与子系统

当前进度（2026-04-08）：

- 已落地可选 Langfuse tracing 封装，并完成应用生命周期内的 configure / flush / shutdown 接入
- API 响应当前已可附带 `trace_id / trace_url / observation_id`，能够把会话响应与 tracing 平台进行交叉定位
- 当前 tracing 主要覆盖 `session create / resume`、routing、approval、execution 等单 Agent 主链路阶段
- 路线图里要求的 `fan-out / aggregation / verification / incident loop` 观测覆盖仍未开始，这些能力当前也尚未进入正式主链路
- `metrics / alerts` 仍未落地；`/healthz` 当前仅反映 Langfuse 配置是否存在，还不能严格代表观测后端可达与 trace flush 成功

阶段判断：

- 本节“完整可观测性”**尚未完成**，当前最多可以视为 tracing baseline 已具备，离生产级完整观测仍有明显缺口

---

### 16. 测试体系扩展

改造目标：

- 在 Phase 1 基础护栏上，补齐更完整的回归体系

建议分层：

- 组件级单元测试
- Mock LLM 集成测试
- graph / orchestrator 集成测试
- Golden 回归测试
- 多 Agent 场景回归测试

完成标准：

- 新增 Agent 或流程调整时，能快速判断是否破坏既有行为
- 核心行为不再依赖人工手测

---

## 推荐实施顺序

严格建议按下面顺序推进：

### 可并行试点

1. `QueryClassifier` - 作为 runtime fast path 试点，但不要绕开统一 runtime

### 顺序必须

2. `Agent Contract + Clarification 下沉`
3. `Agent Factory / Registry`
4. `M4 执行控制深化`
5. `测试与最小可观测性前移`
6. `Parallel Dispatcher + Aggregator`
7. `ClarificationCollector`
8. `ConflictDetector`
9. `ConflictArbiter`
10. `ApprovalCoordinator 扩展`
11. `Executor Layer`
12. `IncidentVerifier`
13. `IncidentLoop`
14. `Auth / RBAC`
15. `存储迁移`
16. `完整可观测性`
17. `测试体系扩展`

原因：

- Phase 0 可以先做，但必须服从统一 runtime 账本
- Phase 1 是多 Agent 的真实基础，不先做后面会持续返工
- `Dispatcher + Aggregator` 是多 Agent Phase 的入口，没有这一步，后续 collector / conflict / approval 扩展都没有承载点
- `Executor Layer` 仍然是执行侧关键缺口，但它必须建立在聚合审批和冲突仲裁之后
- 测试与可观测性必须前移，不然越往后返工成本越高

---

## 里程碑建议

### Milestone N0：Fast Path 可用

完成条件：

- QueryClassifier 完成第一版
- fast path 已接入统一 session / event / response contract

预期收益：

- 简单问题响应更快
- 不引入第二套 runtime

---

### Milestone N1：可扩展单 Agent 内核

完成条件：

- Clarification 下沉完成
- Agent contract 稳定
- Agent registry / factory 完成
- 执行控制深化第一版完成

预期收益：

- 新增 Agent 不再依赖改 orchestrator 主干
- graph 层领域耦合明显下降

---

### Milestone N1.5：重构护栏到位

完成条件：

- contract tests
- graph integration tests
- 最小可观测性

预期收益：

- 后续并发改造可控
- 能快速定位回归问题

当前判断（2026-04-08）：

- 该里程碑已基本达成：contract / registry / execution safety 已有测试护栏，最小可观测性与 tracing baseline 已落地
- 唯一保守项是 graph integration tests 仍偏 runtime smoke 化，若后续进入 dispatcher / aggregator 改造，建议在进入 N2 前单独补一层更清晰的 graph integration suite

---

### Milestone N2：多 Agent 分析闭环

完成条件：

- Dispatcher + Aggregator 完成
- ClarificationCollector / ConflictDetector / ConflictArbiter / ApprovalCoordinator 扩展完成

预期收益：

- 系统真正具备多 Agent 分析与聚合能力
- 不再只是“预留 fan_out 字段”

---

### Milestone N3：统一执行与验证闭环

完成条件：

- Executor Layer 完成
- IncidentVerifier / IncidentLoop 跑通最小闭环

预期收益：

- 系统真正支持分析、审批、执行、验证、重试的统一闭环

---

### Milestone N4：生产可运维化

完成条件：

- Auth / RBAC
- 存储迁移
- 完整可观测性
- 测试体系扩展

预期收益：

- 系统达到更长期运行要求
- 可管、可审、可扩展

当前判断（2026-04-08）：

- 该里程碑当前仍不应视为开始收口
- 其中“完整可观测性”虽然已有 tracing baseline，但 `metrics / alerts / verification / incident loop / 多 Agent 主干` 相关观测覆盖尚未具备

---

## 关键设计决策

### 决策 1：为什么 QueryClassifier 不是“完全独立路径”

- 当前项目核心价值是可靠 runtime，而不是单次问答转发
- 如果 SIMPLE 路径绕开 session / interrupt / events，就会形成两套系统行为
- 因此更合理的是 **runtime 内 fast path**，不是 runtime 外旁路

### 决策 2：为什么要先做 Agent Contract，再做 Registry

- 没有 descriptor / validation / clarification contract，registry 只是配置收纳盒
- 先定义 canonical contract，registry 才不会和代码双轨漂移

### 决策 3：为什么 Dispatcher + Aggregator 必须先于 Collector / Conflict / Approval 扩展

- `ClarificationCollector` 依赖多个 Agent 的 clarification 输出
- `ConflictDetector` 依赖聚合后的 proposals
- `ApprovalCoordinator` 的聚合审批也依赖统一 synthesis
- 所以必须先有 fan-out / fan-in 主干

### 决策 4：为什么 Executor Layer 仍然是关键

- 当前执行仍停留在过渡节点
- 它已经成为多 proposal、多 step、多重试、多补偿的结构瓶颈
- 如果不引入 Executor，多 Agent 最终仍会坍缩回单点执行

### 决策 5：为什么测试和可观测性要前移

- 当前单链路 runtime 已经收敛出稳定行为
- 后续改造集中在 graph、approval、execution、registry 这些高风险位置
- 没有测试与埋点护栏，后续每一步都会放大不确定性

---

## 最终结论

当前项目的下一阶段，不应该直接跳去“堆更多 Agent 和更多工具”，也不应该把预留字段误判成“多 Agent 已基本具备”。

更符合当前代码现实的推进逻辑应该是：

**Phase 0：在统一 runtime 内增加 fast path**
→ 简单问题更快返回，但不分裂系统行为

**Phase 1：补齐单 Agent 可扩展框架**
→ 先修 contract、clarification、registry、execution model、测试护栏

**Phase 2：建立真正的多 Agent 分析框架**
→ 先有 dispatcher / aggregator，再谈 clarification 合并、冲突仲裁、聚合审批

**Phase 3：收口统一执行与验证闭环**
→ 以 Executor 为核心补齐执行协调、验证与循环重试

**Phase 4：进入生产基建**
→ 权限、存储、完整可观测性、测试扩展

这个顺序更贴当前代码状态，也更能避免在后续多 Agent 阶段持续返工。
