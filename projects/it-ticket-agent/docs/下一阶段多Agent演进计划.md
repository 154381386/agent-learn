# IT Ticket Agent 下一阶段多 Agent 演进计划

## 文档定位

本文档不是用来替代当前这轮“单领域落地 / 少工具 / 可靠 runtime 收敛”的总计划与实施进度文档，
而是作为**下一阶段路线图**，回答下面这个问题：

**当当前单领域生产化主线基本完成后，系统接下来应该按什么顺序进入架构修正、多 Agent 并行支撑和生产基建阶段。**

配套文档关系如下：

- 当前收敛总计划：`projects/it-ticket-agent/docs/生产级Agent演进总计划.md`
- 当前实施进度：`projects/it-ticket-agent/docs/生产级Agent实施进度.md`
- 下一阶段路线图：本文档

---

## 当前基线

在进入下一阶段之前，当前项目已经具备以下基础能力：

- 会话、interrupt、checkpoint 持久化
- 上下文装配与 session/process/incident memory
- 单领域审批工作流与恢复执行
- `ExecutionPlan` / `ExecutionStep` / execution checkpoint
- system event 事件账本
- 执行安全基础约束（动作注册、参数校验、审批快照绑定）

因此，下一阶段的重点不再是“先把基础 runtime 做出来”，而是：

1. 修正会影响后续扩展的架构缺陷
2. 让系统真正能支撑多 Agent 并行
3. 补齐生产环境所需的认证、存储、可观测、测试底座

---

## 规划原则

### 1. 先修架构，再加复杂度

- 会导致后续多 Agent 扩展持续返工的问题，优先级高于新增功能
- 尤其是 clarification、Agent 注册、执行控制，必须先收口为正式框架能力

### 2. 多 Agent 能力必须建立在正式 contract 之上

- 不允许继续靠临时字段、隐式 prompt 约定、零散判断逻辑来扩展多 Agent 协作
- 多 Agent 并行、冲突检测、合并审批、验证循环都必须进入明确子系统

### 3. 生产基建不是最后才考虑的“附属项”

- `Auth/RBAC`、存储切换、可观测性、测试体系不是锦上添花，而是下一阶段上线能力的底线

---

## Phase 1：架构修正（先修当前设计缺陷）

目标：把当前仍带有单领域、单链路假设的设计点收口为可扩展框架能力，避免后面多 Agent 阶段返工。

### 1. Clarification 下沉到 Agent 内部

来源问题：`clarification 放哪`

当前判断：当前外层通用 `clarification_gate` 适合单链路，但会在多 Agent 并行下放大路由层复杂度。

改造目标：

- `BaseDomainAgent` 增加 `required_fields` 能力
- 每个 Agent 内部先做缺字段校验，再返回结构化 clarification request
- 外层编排层不再直接承担“缺哪些业务字段”的领域判断

建议交付物：

- `agents/base.py`：补齐 `required_fields` / `validate_context` / `build_clarification_request`
- `agents/*`：各 Agent 显式声明最小运行字段
- `graph/`：移除外层通用 clarification 业务判断，仅保留统一 interrupt 接入

完成标准：

- 缺字段判断由 Agent 内部完成
- 新 Agent 不需要改 graph 层 clarification 逻辑
- clarification 输出是统一 contract，而不是 prompt 文本拼接

---

### 2. Agent Registry

来源问题：`新 Agent 要什么`

当前判断：当前 Agent 注册主要还是代码内装配，扩展新 Agent 的成本偏高，也不利于多领域管理。

改造目标：

- 引入 YAML 声明式 Agent Registry
- 每个 Agent 在注册表中声明：路由关键词、工具、审批策略、知识库、运行约束
- 启动时自动发现并注册 Agent，而不是靠手工改 orchestrator

建议交付物：

- `agent_registry.yaml` 或 `agents/registry/*.yaml`
- `agent_registry/loader.py`
- `runtime/orchestrator.py` 改为从 registry 装载 Agent

最小字段建议：

- `agent_name`
- `display_name`
- `domain`
- `routing_keywords`
- `tools`
- `approval_policy`
- `knowledge_sources`
- `required_fields`

完成标准：

- 新增 Agent 时无需改 orchestrator 主干
- 路由、工具、审批策略可通过声明式配置扩展
- 启动失败时能给出清晰注册错误，而不是运行时才暴露

---

### 3. M4 执行控制深化

来源问题：`rollback 半路失败`

当前判断：当前已经具备 `ExecutionPlan` / `ExecutionStep` / checkpoint / 基础安全控制，
但仍主要偏“单主步骤执行”；下一阶段要深化为真正的多步执行控制器。

改造目标：

- 从“有执行记录”升级到“有执行策略”
- 支持分步检查点、重试策略、补偿机制、失败恢复指引
- 为后续多 Agent proposal 合并执行打基础

建议交付物：

- `execution/` 下补齐 plan builder / retry policy / compensation policy
- `ExecutionStep` 增加重试次数、重试原因、补偿状态等字段
- 恢复接口支持从具体 step 继续，而不是只有会话级恢复提示

完成标准：

- 至少支持多步 plan
- 每步有明确 retry policy
- 可对部分高风险动作定义 compensation 行为
- 执行失败后能定位“失败 step / 下一步建议 / 是否可补偿” 

---

## Phase 2：多 Agent 并行支撑（新能力）

目标：让系统从“单 Agent 串行编排”升级为“多 Agent 并行分析、统一仲裁、统一审批、统一验证”的正式框架。

### 4. ClarificationCollector

来源问题：`并行缺字段`

目标：

- 多 Agent 并行时收集各自缺失字段
- 合并同名字段与重复追问
- 对用户只发起一次 interrupt

完成标准：

- 同一轮并行分析最多只触发一次 clarification interrupt
- 能保留“字段由哪个 Agent 提出”的来源信息

---

### 5. ConflictDetector

来源问题：`操作冲突`

目标：

- 建立资源冲突矩阵
- 检测共享资源、互斥动作、上下游隐式依赖
- 在审批前先识别执行级冲突

完成标准：

- 能识别显式冲突和常见隐式依赖冲突
- 冲突结果进入正式结构化输出，而不是埋在自然语言里

---

### 6. ConflictArbiter

来源问题：`操作冲突`

目标：

- 对语义级冲突进行 LLM 仲裁
- 输出建议优先级、执行顺序、互斥说明和保守方案

完成标准：

- 仲裁输出为结构化 contract
- 能被执行层直接消费，而不是依赖二次解析自然语言

---

### 7. ApprovalCoordinator 扩展

来源问题：`多审批合并`

当前判断：当前已有单审批链路与基础 `ApprovalCoordinator`，下一步是把多 Agent proposals 收敛成统一审批模型。

目标：

- 将多 Agent proposals 合并为一个审批请求
- 支持全批、选批、全拒
- 审批结果能够精确映射回各 proposal 的后续执行状态

完成标准：

- 一个会话可生成一份聚合审批单
- 审批结果能够驱动后续执行 plan 精确过滤

---

### 8. IncidentVerifier

来源问题：`谁来验证`

目标：

- 引入独立验证角色
- 对原始问题是否恢复做结果验证，而不是默认“执行完就算成功”
- 最小先覆盖：P99、错误率、上游健康、关键接口状态

完成标准：

- 验证逻辑独立于执行 Agent
- 验证失败能够回传结构化失败原因与下一轮建议

---

### 9. IncidentLoop

来源问题：`怎么循环`

目标：

- 建立验证-重试循环控制器
- 支持 `max_rounds`
- 每轮保留上下文传递、失败理由、已尝试动作
- 超限后明确升级人工

完成标准：

- 循环控制不依赖 prompt 暗示，而是正式 runtime 逻辑
- 能区分“继续自动重试”和“必须人工接管”

---

## Phase 3：生产基建

目标：让系统从“能工作”升级到“能长期稳定运行、可管可审可扩展”。

### 10. Auth / RBAC

来源问题：`权限控制`

目标：

- API 认证
- 审批权限绑定
- 数据隔离
- 操作审计

完成标准：

- 非授权用户不能读取、审批或恢复不属于自己的会话
- 审批人身份进入正式审计链路

---

### 11. 存储迁移

来源问题：`存储扩展`

目标：

- 引入 Repository 抽象层
- 支持 SQLite / PostgreSQL 可切换
- 为后续横向扩容和多实例部署准备基础设施

完成标准：

- 业务层不直接依赖 SQLite 细节
- 在不改主业务逻辑的前提下切换存储后端

---

### 12. 可观测性

来源问题：`可观测性`

目标：

- OpenTelemetry tracing
- Metrics
- 告警

建议最小覆盖：

- session 创建 / resume / approval / execution / verification 主链路 span
- 执行成功率、审批等待时长、恢复次数、自动升级人工次数等关键指标

完成标准：

- 关键链路可追踪
- 异常可定位到具体阶段与子系统

---

### 13. 测试体系

来源问题：`怎么测 Agent`

目标：

- 组件级单元测试
- Mock LLM 集成测试
- Golden Test 回归

建议分层：

- 领域组件测试：Agent / registry / conflict / approval / verifier
- 运行时集成测试：单 Agent、并行 Agent、审批恢复、循环验证
- Golden 回归：典型事故与多轮交互 case

完成标准：

- 新增 Agent 或流程调整时，能快速判断是否破坏既有行为
- 核心行为不再只依赖人工手测

---

## 推荐实施顺序

严格建议按下面顺序推进：

1. `Clarification 下沉到 Agent 内部`
2. `Agent Registry`
3. `M4 执行控制深化`
4. `ClarificationCollector`
5. `ConflictDetector`
6. `ConflictArbiter`
7. `ApprovalCoordinator 扩展`
8. `IncidentVerifier`
9. `IncidentLoop`
10. `Auth / RBAC`
11. `存储迁移`
12. `可观测性`
13. `测试体系`

原因：

- `1~3` 是架构底座，如果不先做，`4~9` 会建立在错误边界上
- `4~9` 才是真正的多 Agent 并行闭环
- `10~13` 决定系统是否可以进入更长期的生产运行阶段

---

## 里程碑建议

### Milestone N1：可扩展单 Agent 框架

完成条件：

- Clarification 下沉完成
- Agent Registry 完成
- 执行控制深化完成第一版

### Milestone N2：多 Agent 并行闭环

完成条件：

- ClarificationCollector / ConflictDetector / ConflictArbiter / ApprovalCoordinator 扩展完成
- IncidentVerifier / IncidentLoop 最小闭环跑通

### Milestone N3：生产可运维化

完成条件：

- Auth / RBAC
- 存储迁移
- 可观测性
- 测试体系

---

## 最终结论

当前项目的下一阶段，不应该直接跳去“堆更多 Agent 和更多工具”，而应该严格按下面的逻辑推进：

**先修架构缺陷，再补多 Agent 并行能力，最后补齐生产基建。**

如果顺序反过来，后续很容易出现：

- clarification 逻辑继续外溢
- 新 Agent 接入成本越来越高
- 多 Agent proposal 无法正式合并
- 冲突处理只能靠 prompt 临时解决
- 执行、验证、循环和权限边界互相缠绕

因此，建议把本文档作为下一阶段的唯一路线图，并在当前这轮收敛完成后按本文档开新一轮实施清单。
