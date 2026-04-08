# IT Ticket Agent 下一阶段多 Agent 演进计划（V2）

## 文档定位

本文档不是用来替代当前这轮"单领域落地 / 少工具 / 可靠 runtime 收敛"的总计划与实施进度文档，
而是作为**下一阶段路线图**，回答下面这个问题：

**当当前单领域生产化主线基本完成后，系统接下来应该按什么顺序进入快速路由、架构修正、多 Agent 并行支撑和生产基建阶段。**

配套文档关系如下：

- 当前收敛总计划：`projects/it-ticket-agent/docs/生产级Agent演进总计划.md`
- 当前实施进度：`projects/it-ticket-agent/docs/生产级Agent实施进度.md`
- 下一阶段路线图：本文档（新版）

**本版本重点改动：**
- 新增 Phase 0 前置快速分类（QueryClassifier），支撑简单问题快速响应
- Phase 2 新增 Executor Layer，补齐多 Agent 执行协调的关键缺失
- 明确执行安全检查的责任边界转移：M4 → Executor Layer
- 调整推荐实施顺序，确保依赖关系正确

---

## 当前基线

在进入下一阶段之前，当前项目已经具备以下基础能力：

- 会话、interrupt、checkpoint 持久化
- 上下文装配与 session/process/incident memory
- 单领域审批工作流与恢复执行
- `ExecutionPlan` / `ExecutionStep` / execution checkpoint
- system event 事件账本
- 执行安全基础约束（动作注册、参数校验、审批快照绑定）

因此，下一阶段的重点不再是"先把基础 runtime 做出来"，而是：

1. **快速响应简单问题** - 前置分流，让 FAQ / 文档查询快速返回
2. **修正会影响后续扩展的架构缺陷** - clarification、Agent 注册、执行控制
3. **让系统真正能支撑多 Agent 并行** - 包括执行协调这个核心缺失
4. **补齐生产环境所需的基建** - 认证、存储、可观测、测试

---

## 规划原则

### 1. 快速路由优先，简单问题秒级返回

- 60% 的用户问题是 FAQ / 文档查询，不需要走完整 Agent 流程
- QueryClassifier 成本低（$0.001 + <100ms），立即收益
- 可与其他 Phase 并行实施，不阻断任何其他工作

### 2. 先修架构，再加复杂度

- 会导致后续多 Agent 扩展持续返工的问题，优先级高于新增功能
- 尤其是 clarification、Agent 注册、执行协调，必须先收口为正式框架能力

### 3. 多 Agent 能力必须建立在正式 contract 之上

- 不允许继续靠临时字段、隐式 prompt 约定、零散判断逻辑来扩展多 Agent 协作
- 多 Agent 并行、冲突检测、合并审批、统一执行、验证循环都必须进入明确子系统

### 4. 执行不在 SubAgent，而在独立的 Executor 层

- SubAgent 职责：分析 + 建议（read-only 工具）
- Aggregator 职责：综合结果（可读验证）
- ApprovalGate 职责：风险评估和审批拦截
- **Executor 职责：统一执行（write 工具）** ← 本版新增，关键！
- 这样才能支持多 Agent 的顺序控制、冲突检测、故障恢复

### 5. 生产基建不是最后才考虑的"附属项"

- `Auth/RBAC`、存储切换、可观测性、测试体系不是锦上添花，而是下一阶段上线能力的底线

---

## Phase 0：前置快速分类（可并行实施）

目标：前置快速路由，让 FAQ 和文档查询 500ms 内返回，复杂问题进完整流程。

### 0. QueryClassifier

来源问题：`简单问题怎么快速响应`

当前分析：
- 当前项目假设所有问题都走 Supervisor 强模型判断（3-5s 延迟，$0.17 成本）
- 但实际用户问题中 60% 是 FAQ、文档查询、已知故障等"简单问题"
- 这些问题可以通过规则 + 小模型快速分类，然后直接 RAG 返回（500ms，$0 成本）

改造目标：

- 前置 QueryClassifier：规则库 + 小模型混合分类
- SIMPLE 路径：直接 RAG 查询 → 500ms 返回
- COMPLEX/UNKNOWN 路径：进入 Supervisor 和完整 Agent 流程（3-5s）

建议交付物：

- `query_classifier.py` - 快速分类器，包含：
  - 规则引擎（关键词、pattern 匹配）
  - 小模型分类提示（GPT-3.5 或本地模型）
  - 分类结果：SIMPLE / COMPLEX / UNKNOWN

- `query_classifier_rules.yaml` - 规则库：
  - FAQ pattern（"怎么回滚"、"在哪看日志"等）
  - 文档查询关键词
  - 已知故障识别

- `runtime/orchestrator.py` 改造：
  - 前置调用 classifier，判断问题类型
  - SIMPLE → 直接调用 RAG client
  - COMPLEX/UNKNOWN → 进 Supervisor 路由

- 可观测性埋点：
  - SIMPLE 分类成功率
  - RAG 返回置信度
  - 归档到 RAG 的降级率

完成标准：

- 60%+ 简单问题通过 SIMPLE 路径返回
- 分类准确率 >95%（通过人工标注验证）
- SIMPLE 路径平均延迟 <500ms（分类 <100ms + RAG <400ms）
- 无 Context 污染：RAG 返回的答案不会被 Agent 误解
- 可快速迭代规则库，新规则无需改代码

---

## Phase 1：架构修正（先修当前设计缺陷）

目标：把当前仍带有单领域、单链路假设的设计点收口为可扩展框架能力，避免后面多 Agent 阶段返工。

### 1. Clarification 下沉到 Agent 内部

来源问题：`clarification 放哪`

当前判断：当前外层通用 `clarification_gate` 适合单链路，但会在多 Agent 并行下放大路由层复杂度。

改造目标：

- `BaseDomainAgent` 增加 `required_fields` 能力
- 每个 Agent 内部先做缺字段校验，再返回结构化 clarification request
- 外层编排层不再直接承担"缺哪些业务字段"的领域判断
- 为 Phase 2 的 ClarificationCollector 预留接口

建议交付物：

- `agents/base.py`：补齐以下方法
  - `required_fields: List[FieldRequirement]` - 声明最小运行字段
  - `validate_context(ctx) -> ValidationResult` - 校验字段完整性
  - `build_clarification_request(missing_fields) -> ClarificationRequest` - 构建追问

- `agents/*`：各 Agent 显式声明最小运行字段，示例：
  ```python
  class CICDAgent(BaseDomainAgent):
      required_fields = [
          FieldRequirement(name="service", type="string", description="服务名"),
          FieldRequirement(name="action", type="enum", values=["rollback", "restart"]),
      ]
  ```

- `graph/` 改造：
  - 移除外层通用 clarification 业务判断
  - 仅保留统一 interrupt 接入点
  - clarification 输出改为结构化 contract，而不是 prompt 文本

完成标准：

- 缺字段判断由 Agent 内部完成，graph 层无业务知识
- 新 Agent 不需要改 graph 层 clarification 逻辑
- clarification 输出是统一 contract，而不是 prompt 文本拼接
- 代码审视：graph/orchestrator 里不出现 domain 业务字段的判断

---

### 2. Agent Registry

来源问题：`新 Agent 要什么`

当前判断：当前 Agent 注册主要还是代码内装配，扩展新 Agent 的成本偏高，也不利于多领域管理。

改造目标：

- 引入 YAML 声明式 Agent Registry
- 每个 Agent 在注册表中声明：路由关键词、工具、审批策略、知识库、运行约束
- 启动时自动发现并注册 Agent，而不是靠手工改 orchestrator

建议交付物：

- `agents/registry/` 目录，包含：
  - `cicd_agent.yaml` - CICD Agent 声明
  - `db_agent.yaml` - DB Agent 声明
  - `network_agent.yaml` - Network Agent 声明
  - 以此类推，每个 Agent 一个 YAML

- `agent_registry/loader.py` - 注册表加载器
  - 启动时扫描 registry 目录，自动加载 Agent
  - 配置验证：必填字段、工具 schema、审批策略格式
  - 启动失败时给出清晰错误

- `runtime/orchestrator.py` 改造：
  - 从代码内装配 → 从 registry 装载
  - Supervisor 路由改为从 registry 查询 Agent 元信息

Agent Registry 最小字段：

```yaml
# agents/registry/cicd_agent.yaml
agent_name: cicd_agent
display_name: CICD 故障处理
domain: cicd
description: 分析部署失败、版本问题、Canary 失败
routing_keywords:
  - deployment
  - canary
  - rollback
  - service-restart

tools:
  - GetRecentDeployments  # 只读
  - CheckCanaryStatus     # 只读
  - CheckServiceHealth    # 只读
  # 不声明写操作，那是 Executor 的职责

approval_policy:
  risk_level: high  # 所有操作都是高风险
  requires_approval: true

knowledge_sources:
  - type: vector_store
    name: cicd_runbooks
    embedding_model: text-embedding-3-small

required_fields:
  - name: service
    type: string
    description: 需要处理的服务名
  - name: incident_time
    type: timestamp
    description: 故障发生时间

max_parallel_instances: 3
timeout_seconds: 30
```

完成标准：

- 新增 Agent 时无需改 orchestrator 主干代码
- 路由、工具、审批策略可通过声明式配置扩展
- 启动失败时能给出清晰注册错误，而不是运行时才暴露
- registry 加载包含 schema 校验，坏配置启动时即拒绝

---

### 3. M4 执行控制深化（与 Executor 层协同）

来源问题：`rollback 半路失败`

当前判断：当前已经具备 `ExecutionPlan` / `ExecutionStep` / checkpoint / 基础安全控制，
但仍主要偏"单主步骤执行"；下一阶段要深化为真正的多步执行控制器。

**重要注意：本阶段的执行安全检查（动作注册、参数校验、审批快照绑定）为 Phase 2 的 Executor Layer 做准备。**

改造目标：

- 从"有执行记录"升级到"有执行策略"
- 支持分步检查点、重试策略、补偿机制、失败恢复指引
- 为后续 Phase 2 的 Executor Layer 预留接口和代码转移准备

建议交付物：

- `execution/execution_plan.py` 深化
  - `ExecutionStep` 增加：重试次数、重试原因、补偿状态、前置依赖
  - `ExecutionPlan` 增加：plan-level 失败恢复策略、全局补偿链

- `execution/retry_policy.py`（新增）
  - `RetryPolicy` 模型：max_retries, backoff_strategy, retryable_errors
  - 示例：DB 操作可重试，权限错误不可重试

- `execution/compensation_policy.py`（新增）
  - `CompensationPolicy` 模型：补偿动作、补偿顺序、是否可部分补偿
  - 示例：添加索引失败可补偿为"删除该索引"

- `execution/executor_interface.py`（新增，为 Phase 2 预留）
  - 定义 `Executor` 接口，当前不实现，仅定义 contract
  - 确定签名：`async execute(approved_actions: List[ApprovedAction]) -> ExecutionResult`

- 恢复接口升级
  - 支持从具体 step 继续，而不是只有会话级恢复提示
  - 失败后返回：失败 step、建议原因、是否可补偿、下一步建议

完成标准：

- 至少支持 3+ 步的 plan
- 每步有明确 retry policy（可覆盖 DB、API、网络操作）
- 可对高风险动作定义 compensation 行为
- 执行失败后能定位"失败 step / 原因 / 下一步建议"
- `ExecutionStep` 模型为后续 Phase 2 Executor Layer 所用，已预留接口

**代码转移注意：**
- 目前在 `execute_approved_action_transition` 节点里的以下逻辑：
  - 动作注册检查（是否在允许列表）
  - 参数 schema 校验
  - 审批快照绑定一致性检查
- 这些代码现在保留在当前节点（M4 阶段），但在 Phase 2 Executor Layer 时会被迁移过去
- 迁移时修改点：从图节点的"单个 action 处理"改为 Executor 的"批量 actions 处理"

---

## Phase 2：多 Agent 并行支撑（新能力）

目标：让系统从"单 Agent 串行编排"升级为"多 Agent 并行分析、统一仲裁、统一审批、统一执行、统一验证"的正式框架。

### 4. ClarificationCollector

来源问题：`并行缺字段`

当前判断：多 Agent 并行时，各 Agent 可能各自提出缺字段诉求。
如果不合并，用户会被问多次同一个字段，体验很差。

改造目标：

- 多 Agent 并行时收集各自缺失字段
- 合并同名字段与重复追问
- 去重和优先级排序
- 对用户只发起一次 interrupt

建议交付物：

- `orchestration/clarification_collector.py`（新增）
  - `ClarificationCollector` 类
  - 聚合多个 `ClarificationRequest`
  - 字段去重、优先级排序（critical > high > low）
  - 生成单一合并的 clarification interrupt

- 数据模型：
  ```python
  class ClarificationField:
      name: str
      type: str  # string, enum, timestamp, etc
      description: str
      requested_by: List[str]  # 哪些 Agent 要求
      priority: str  # critical / high / low

  class MergedClarificationRequest:
      fields: List[ClarificationField]  # 合并去重后的字段
      field_sources: Dict[str, List[str]]  # 哪些 Agent 提的
      single_interrupt: bool  # 仅一次追问
  ```

完成标准：

- 同一轮并行分析最多只触发一次 clarification interrupt
- 能保留"字段由哪些 Agent 提出"的来源信息
- 用户补充字段后，所有 Agent 都能看到补充值（context 更新）

---

### 5. ConflictDetector

来源问题：`操作冲突`

当前判断：多 Agent 可能提出互相冲突的 proposed_actions。
例如：CICD Agent 提议"回滚"，DB Agent 提议"添加索引"，两个同时执行可能产生问题。

改造目标：

- 建立资源冲突矩阵
- 检测共享资源、互斥动作、上下游隐式依赖
- 在审批前先识别执行级冲突

建议交付物：

- `orchestration/conflict_detector.py`（新增）
  - `ConflictDetector` 类
  - 冲突矩阵定义（哪些操作对互斥、哪些资源共享）
  - 冲突检测算法

- `orchestration/conflict_matrix.yaml`（新增）
  - 配置化的冲突规则
  - 示例：
    ```yaml
    conflicts:
      - action1: rollback
        action2: canary_deploy
        reason: 不能同时回滚和灰度部署
        severity: high

    shared_resources:
      - resource: service-db
        actions: [add_index, drop_index, alter_column]
        max_concurrent: 1

    dependencies:
      - predecessor: add_index
        successor: query_optimization
        reason: 查询优化需要索引先存在
    ```

- 数据模型：
  ```python
  class ConflictResult:
      has_conflict: bool
      conflicts: List[Conflict]  # 具体冲突列表
      shared_resources: List[str]  # 共享资源
      dependencies: List[Dependency]  # 隐式依赖

  class Conflict:
      action1: str
      action2: str
      reason: str
      severity: str  # critical / high / medium / low
  ```

完成标准：

- 能识别显式冲突（互斥操作）和常见隐式依赖冲突
- 冲突结果进入正式结构化输出，而不是埋在自然语言里
- 冲突矩阵可声明式扩展，新冲突规则无需改代码

---

### 6. ConflictArbiter

来源问题：`谁来决定冲突怎么解`

当前判断：冲突检测后，不是所有冲突都应该拒绝执行；有些冲突可以通过调整执行顺序来规避。

改造目标：

- 对语义级冲突进行 LLM 仲裁
- 输出建议优先级、执行顺序、互斥说明和保守方案

建议交付物：

- `orchestration/conflict_arbiter.py`（新增）
  - `ConflictArbiter` 类
  - 调用 LLM 进行冲突仲裁
  - 生成结构化仲裁结果

- LLM 提示模板
  - 输入：conflict list、各 agent 的 proposed_actions、incident context
  - 输出：建议执行顺序、是否能并行、如果冲突的处理建议

- 数据模型：
  ```python
  class ArbitrationResult:
      original_conflicts: List[Conflict]
      recommended_order: List[int]  # action index 的推荐执行顺序
      parallel_groups: List[List[int]]  # 可并行执行的 action 组
      mutual_exclusive: List[Tuple[int, int]]  # 必须互斥的 action 对
      rationale: str  # LLM 仲裁的理由
      confidence: float  # 0.0-1.0
  ```

完成标准：

- 仲裁输出为结构化 contract，而不是自然语言
- 能被执行层直接消费，无需二次解析
- 包含理由和置信度，便于人工审批时理解和推翻

---

### 7. ApprovalCoordinator 扩展

来源问题：`多审批合并`

当前判断：当前已有单审批链路与基础 `ApprovalCoordinator`，下一步是把多 Agent proposals 收敛成统一审批模型。

改造目标：

- 将多 Agent proposals 合并为一个审批请求
- 支持全批、选批、全拒
- 审批结果能够精确映射回各 proposal 的后续执行状态

建议交付物：

- `approval/approval_coordinator.py` 扩展
  - 已存在的单 proposal 审批逻辑保留
  - 新增 `coordinate_multiple_proposals()` 方法
  - 支持多 proposal 的聚合、过滤、选批

- 数据模型扩展：
  ```python
  class AggregatedApprovalRequest:
      proposals: List[ProposedAction]  # 所有待审批的 proposals
      agent_sources: Dict[int, str]  # 每个 proposal 来自哪个 Agent
      conflict_hints: List[Conflict]  # ConflictDetector 的结果
      arbitration_hints: ArbitrationResult  # ConflictArbiter 的结果
      risk_assessment: Dict[int, str]  # 每个 proposal 的风险等级

  class ApprovalDecision:
      approved_actions: List[ProposedAction]  # 批准的操作
      rejected_actions: List[ProposedAction]  # 拒绝的操作
      decision_per_proposal: Dict[int, str]  # 每个 proposal 的决策
      approver_comment: str  # 审批人备注
  ```

- 前端展示（API 合同）：
  - 聚合审批单：summary + 各 proposal 详情
  - 支持"全批、全拒、逐个选择"
  - 展示冲突提示和仲裁建议（仅供参考）

完成标准：

- 一个会话可生成一份聚合审批单
- 审批结果能够驱动后续执行 plan 精确过滤
- 审批决策和执行结果有清晰映射（可追溯）

---

### 8. Executor Layer（新增，关键！）

来源问题：`多 Agent 执行怎么协调`

当前判断：Phase 2.1-7 完成后，会有多个 approved_actions 等待执行，
但如果由 SubAgent 自己在各自内部执行，无法处理执行顺序、冲突协调、故障恢复。

**核心原则：执行不在 SubAgent，而在独立的 Executor 层。**

改造目标：

- 将执行从 Agent 内部独立出来，成为独立的 Executor Layer
- 统一负责：顺序控制、冲突处理、执行前二次校验、故障恢复
- 支持多 Agent proposals 的协调执行
- 从当前图节点 `execute_approved_action_transition` 升级为独立子系统

架构对比

```
当前设计（M4）：
  domain_agent.run()
    ├─ 分析
    ├─ 决定 proposed_actions
    ├─ 在 execute_approved_action_transition 节点里执行 ← 单点执行
    └─ 返回结果

新设计（Phase 2 + Executor）：
  多个 SubAgent 并行
    ├─ 分析 + 建议（proposed_actions）
    └─ 不执行！

  Aggregator.synthesize()
    └─ final_proposed_actions

  ApprovalGate.evaluate()
    ├─ 风险评估
    └─ approved_actions（或 requires_approval interrupt）

  ConflictDetector / ConflictArbiter
    └─ resolved_actions_with_order

  Executor.execute()  ← 新增这一层！
    ├─ 执行前安全检查（迁移自 M4）
    ├─ 按顺序执行
    ├─ 错误恢复
    └─ 执行结果 + 证据链
```

建议交付物：

- `execution/executor.py`（新增）
  ```python
  class Executor:
      """统一执行引擎，负责执行 approved_actions"""

      async def execute(self, approved_actions: List[ApprovedAction]) -> ExecutionResult:
          """
          执行批准的操作。

          步骤：
          1. 构建执行计划（拓扑排序 + 并发分组）
          2. 执行前安全检查
          3. 按顺序执行
          4. 错误恢复 + 补偿
          5. 返回完整结果
          """
          pass
  ```

- `execution/execution_orchestrator.py`（新增）
  - 拓扑排序：根据 dependencies 确定执行顺序
  - 并发分组：根据 ConflictArbiter 的 parallel_groups 组织并发执行
  - 例：[action1, action2] 先串行，然后 [action3, action4] 并行

- `execution/pre_execution_validator.py`（新增，从 M4 迁移部分逻辑）
  - 动作注册检查（是否在允许列表）
  - 参数 schema 校验
  - 审批快照绑定一致性检查
  - 权限检查（新增）

- `execution/step_executor.py`（新增）
  - 单个 step 的执行器
  - 调用 MCP 工具
  - 重试逻辑
  - 错误捕获与记录

- `execution/failure_recovery.py`（新增）
  - 故障恢复策略
  - 补偿执行
  - 部分恢复支持（某些 step 失败继续）

- 数据模型升级
  ```python
  class ExecutionPlan:
      """执行计划，由 Executor 生成和管理"""
      actions: List[ApprovedAction]
      execution_order: List[int]  # action index 的执行顺序
      parallel_groups: List[List[int]]  # 并发执行分组
      steps: List[ExecutionStep]  # 展开后的执行步骤

  class ExecutionStep:
      """单个执行步骤"""
      step_id: str
      action: ApprovedAction
      status: str  # pending / running / success / failed / compensated
      retry_count: int
      retry_policy: RetryPolicy
      compensation_policy: CompensationPolicy
      pre_check_results: Dict[str, Any]  # 执行前检查结果
      execution_result: Optional[MCP_ToolResult]
      error: Optional[Exception]
      compensation_result: Optional[MCP_ToolResult]

  class ExecutionResult:
      """最终执行结果"""
      plan: ExecutionPlan
      step_results: Dict[str, ExecutionStep]
      overall_status: str  # success / partial_success / failed
      failed_steps: List[str]
      compensated_steps: List[str]
      execution_timeline: List[ExecutionEvent]
      evidence: List[str]  # 关键执行证据（日志 ID、截图等）
  ```

- Executor 与图节点的关系
  - 新建图节点：`execute_approved_actions_orchestrated`
  - 调用 Executor.execute()
  - 接收 ExecutionResult
  - 错误处理：失败导向 approval escalation 或 incident_loop

完成标准：

- 多个 approved_actions 可按正确顺序执行（拓扑排序）
- 支持部分并发执行（parallel_groups）
- 能检测并规避冲突（ConflictArbiter 的输出被正确消费）
- 执行失败可部分重试或升级人工（不是全部失败）
- 与 ConflictArbiter 的输出正式对接，无 prompt 猜测
- 执行前安全检查从 M4 迁移过来，成为 Executor 的一部分
- 完整的执行证据链（日志、快照、事件）

---

### 9. IncidentVerifier

来源问题：`谁来验证原始故障是否已解决`

当前判断：执行完操作后，不能假设"操作成功 = 问题解决"。
需要独立验证角色验证原始问题的恢复情况。

改造目标：

- 引入独立验证角色
- 对原始问题是否恢复做结果验证，而不是默认"执行完就算成功"
- 最小先覆盖：P99、错误率、上游健康、关键接口状态

建议交付物：

- `verification/incident_verifier.py`（新增）
  - `IncidentVerifier` 类
  - 验证逻辑独立于执行 Agent
  - 对接监控数据（Prometheus、Grafana、应用指标）

- 验证规则引擎
  - 规则配置：不同事故类型的验收标准
  - 示例：部署失败恢复 = P99 恢复 + 错误率 < threshold + 无新报错

- 数据模型：
  ```python
  class VerificationRule:
      incident_type: str  # deployment_failure, database_down, etc
      checks: List[VerificationCheck]  # 要验证的指标
      pass_criteria: Dict[str, Any]  # 通过标准

  class VerificationCheck:
      name: str
      metric: str  # prometheus metric name
      query: str  # PromQL 或 SQL
      operator: str  # > < == >= <= in
      threshold: float

  class VerificationResult:
      incident_id: str
      incident_type: str
      verified: bool  # 是否通过验证
      check_results: Dict[str, CheckResult]  # 各检查的结果
      failure_reasons: List[str]  # 验证失败的原因
      next_suggestions: List[str]  # 下一步建议
      timestamp: datetime
  ```

完成标准：

- 验证逻辑独立于执行 Agent（不混在一起）
- 验证失败能够回传结构化失败原因与下一轮建议
- 支持多种指标来源（Prometheus、应用 API、日志）
- 规则可声明式扩展

---

### 10. IncidentLoop

来源问题：`怎么循环重试`

当前判断：验证失败后，系统应该能回到分析阶段重新诊断，形成闭环。
而不是简单的"失败告诉人工"。

改造目标：

- 建立验证-重试循环控制器
- 支持 `max_rounds`
- 每轮保留上下文传递、失败理由、已尝试动作
- 超限后明确升级人工

建议交付物：

- `orchestration/incident_loop_controller.py`（新增）
  - `IncidentLoopController` 类
  - 循环控制逻辑
  - 上下文传递（已尝试的 Agent、已执行的 actions、失败原因）
  - 决策：继续分析 / 升级人工 / 结束

- 数据模型：
  ```python
  class IncidentLoopState:
      round: int
      max_rounds: int
      original_incident: IncidentReport
      tried_agents: List[str]  # 已尝试过的 Agent
      executed_actions: List[str]  # 已执行过的操作
      verification_results: List[VerificationResult]  # 每轮验证结果
      failure_reasons: List[str]  # 各轮失败原因

  class LoopDecision:
      action: str  # continue / escalate_to_human / success
      reason: str
      next_agents: Optional[List[str]]  # 下一轮尝试的 Agent
      escalation_level: Optional[str]  # warning / critical
  ```

- 循环控制决策策略
  - Round 1-2：自动重试（可能尝试其他 Agent）
  - Round 3+：降级置信度，增加人工审批权重
  - Round >= max_rounds：明确升级人工

- 图节点：`incident_verification_loop`
  - 调用 IncidentVerifier
  - 如果验证失败，调用 LoopController 决策
  - 如果继续，回到多 Agent 分析阶段
  - 如果升级，创建人工任务

完成标准：

- 循环控制不依赖 prompt 暗示，而是正式 runtime 逻辑
- 能区分"继续自动重试"和"必须人工接管"
- 每轮有清晰的 abort 条件和 escalation 流程
- 上下文在循环中保留，不丢失历史信息

---

## Phase 3：生产基建

目标：让系统从"能工作"升级到"能长期稳定运行、可管可审可扩展"。

### 11. Auth / RBAC

来源问题：`权限控制`

改造目标：

- API 认证
- 审批权限绑定
- 数据隔离
- 操作审计

建议交付物：

- 身份认证层
- 权限模型：查看权限、审批权限、执行权限
- 审计日志：谁批准了什么、谁执行了什么

完成标准：

- 非授权用户不能读取、审批或恢复不属于自己的会话
- 审批人身份进入正式审计链路

---

### 12. 存储迁移

来源问题：`存储扩展`

改造目标：

- 引入 Repository 抽象层
- 支持 SQLite / PostgreSQL 可切换
- 为后续横向扩容和多实例部署准备基础设施

建议交付物：

- Repository 接口层
- SQLite 实现（保留）
- PostgreSQL 实现（新增）
- 迁移脚本

完成标准：

- 业务层不直接依赖 SQLite 细节
- 在不改主业务逻辑的前提下切换存储后端

---

### 13. 可观测性

来源问题：`可观测性`

改造目标：

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

### 14. 测试体系

来源问题：`怎么测 Agent`

改造目标：

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

**并行可做的**：
1. `QueryClassifier` - Phase 0，可与其他工作完全独立

**顺序必须的**：
2. `Clarification 下沉到 Agent 内部` - Phase 1.1，为后续多 Agent 打基础
3. `Agent Registry` - Phase 1.2，规范化 Agent 扩展
4. `M4 执行控制深化` - Phase 1.3，准备 Executor Layer 的代码转移
5. `ClarificationCollector` - Phase 2.4，多 Agent 字段合并
6. `ConflictDetector` - Phase 2.5，冲突识别
7. `ConflictArbiter` - Phase 2.6，冲突仲裁
8. `ApprovalCoordinator 扩展` - Phase 2.7，多 proposal 合并审批
9. **`Executor Layer` - Phase 2.8（新增，关键！）** - 多 Agent 执行协调
10. `IncidentVerifier` - Phase 2.9，验证恢复
11. `IncidentLoop` - Phase 2.10，循环重试
12. `Auth / RBAC` - Phase 3.11，权限控制
13. `存储迁移` - Phase 3.12，扩展性
14. `可观测性` - Phase 3.13，可运维性
15. `测试体系` - Phase 3.14，长期质量

原因：

- Phase 0（QueryClassifier）可立即收益，不阻断主线
- Phase 1（1-4）是架构底座，如果不先做，Phase 2 会建立在错误边界上
- Phase 2（5-11）才是真正的多 Agent 并行闭环，其中 Executor Layer 是关键缺失
- Phase 3（12-15）决定系统是否可以进入更长期的生产运行阶段

---

## 里程碑建议

### Milestone N0：简单问题快速响应（快速收益）

完成条件：

- QueryClassifier 完成
- 60%+ 简单问题 <500ms 返回

预期收益：

- 用户满意度提升
- 系统成本降低 40%+
- 可立即上线，不影响其他工作

---

### Milestone N1：可扩展单 Agent 框架

完成条件：

- Clarification 下沉完成
- Agent Registry 完成
- 执行控制深化完成第一版

预期收益：

- 新增 Agent 不需要改 orchestrator
- 架构为多 Agent 并行做准备

---

### Milestone N2：多 Agent 并行闭环（核心）

完成条件：

- ClarificationCollector / ConflictDetector / ConflictArbiter / ApprovalCoordinator 扩展完成
- **Executor Layer 完成** ← 关键
- IncidentVerifier / IncidentLoop 最小闭环跑通

预期收益：

- 系统真正支持多 Agent 并行分析和协调执行
- 复杂问题分析时间从 3-5s 降到 1-2s（并行收益）
- 自动化程度提升，人工干预次数减少

---

### Milestone N3：生产可运维化

完成条件：

- Auth / RBAC
- 存储迁移
- 可观测性
- 测试体系

预期收益：

- 系统达到生产级别
- 可长期稳定运行
- 可管可审可扩展

---

## 关键设计决策

### 决策 1：为什么是 QueryClassifier（Phase 0），而不是后面？

- **快速收益**：60% 简单问题可 500ms 返回，立即改善用户体验
- **独立解耦**：不依赖 Phase 1-3 的任何改动，可并行实施
- **成本低**：规则库 + 小模型，成本 $0.001，RoI 很高
- **风险低**：RAG 直接返回，不调用 Agent，降级风险最小

### 决策 2：为什么 Executor Layer 必须在 Phase 2.8？

- **缺口**：ApprovalCoordinator 合并多 proposals 后，谁来执行？
- **顺序控制**：多 Agent 的操作不能乱执行，需要拓扑排序
- **冲突处理**：ConflictArbiter 的输出需要被 Executor 消费
- **故障恢复**：执行失败时需要部分重试或升级，不是全部回滚

不加 Executor 层的后果：
- ✗ 多 Agent 的 proposed_actions 无法协调，回到单 Agent 模式
- ✗ 冲突检测和仲裁的结果无处可用
- ✗ IncidentLoop 无法做有效重试（哪一步失败？该补偿哪一步？）

### 决策 3：为什么 SubAgent 不执行，只建议？

- **职责清晰**：分析和执行分离，不混在一起
- **审批拦截**：审批能在执行前完整介入，不是事后补救
- **多 Agent 协调**：Executor 可以统一控制执行顺序和冲突处理
- **故障恢复**：IncidentLoop 知道哪个操作失败，能精确重试

代价：多一个 Executor 层，但收益远大于代价

### 决策 4：M4 执行安全检查如何过渡到 Executor？

- **现在（M4）**：保留在 `execute_approved_action_transition` 节点
  - 动作注册、参数校验、快照绑定一致性检查
  - 已在生产运行，稳定可靠

- **Phase 2 Executor 完成后**：迁移到 `Executor.pre_execution_validator()`
  - 从"单个 action 处理"改为"批量 actions 处理"
  - 增加权限检查、资源锁定等逻辑
  - 原图节点 fallback 到 Executor 调用

- **过渡期**：可以保持两层验证，逐步迁移

---

## 成本与收益分析

### 简单问题快速响应（Phase 0）

| 指标 | 当前 | 改后 | 收益 |
|------|------|------|------|
| 简单问题延迟 | 4s | 0.5s | ↓ 88% |
| 简单问题成本 | $0.17 | $0 | ↓ 100% |
| 平均延迟（60% 简单 + 40% 复杂） | 2.8s | 1.0s | ↓ 64% |
| 平均成本 | $0.068 | $0.068 | = |

### 多 Agent 并行（Phase 2）

假设复杂问题需要 3 个 Agent 并行分析：

| 指标 | 串行 | 并行 | 收益 |
|------|------|------|------|
| 分析时间 | 1.5s × 3 = 4.5s | 1.5s | ↓ 67% |
| 总体时间（分析+执行+验证） | 8-10s | 3-5s | ↓ 50% |
| API 调用数 | 3 个 Agent | 3 个 Agent | = |
| 成本 | $0.17 | $0.17 | = |
| 用户体验 | 等待 CICD Agent → DB Agent → Network Agent | 同时得到三个分析 | ↑ 显著 |

---

## 最终结论

当前项目的下一阶段，不应该直接跳去"堆更多 Agent 和更多工具"，而应该严格按下面的逻辑推进：

**Phase 0：快速路由（立即收益）**
→ 简单问题秒级返回

**Phase 1：架构修正（打好基础）**
→ 先修 clarification、Agent 注册、执行控制

**Phase 2：多 Agent 并行（核心能力）**
→ 包括被遗漏的 Executor Layer

**Phase 3：生产基建（长期稳定）**
→ 权限、存储、可观测、测试

如果顺序反过来或跳过任何一个阶段，后续很容易出现：

- clarification 逻辑继续外溢，多 Agent 时混乱
- 新 Agent 接入成本越来越高，难以扩展
- 多 Agent 的 proposed_actions 无法正式合并和协调
- 冲突处理只能靠 prompt 临时解决，无法可靠
- 执行、验证、循环和权限边界互相缠绕，无法收口
- **最关键：无法实现真正的多 Agent 并行执行**

因此，建议把本文档作为下一阶段的唯一路线图，并在当前这轮收敛完成后按本文档开新一轮实施清单。

---

## 附录：与原计划的变更映射

| 项 | 原计划 | 新版本 | 原因 |
|----|--------|--------|------|
| Phase 0 | 无 | QueryClassifier | 快速收益 + 并行可做 |
| Phase 1.1-3 | Clarification + Registry + M4 | Clarification + Registry + M4 | 保持不变 |
| Phase 2.4-7 | ClarificationCollector 等 4 项 | ClarificationCollector 等 4 项 | 保持不变 |
| Phase 2.8 | IncidentVerifier（原 2.8） | Executor Layer（新增） | 缺失的关键层 |
| Phase 2.9-10 | IncidentVerifier / IncidentLoop（原 2.8-9） | IncidentVerifier / IncidentLoop（新 2.9-10） | 因新增 Executor 后移 |
| Phase 3.10-13 | Auth 等 4 项（原 2.10-13） | Auth 等 4 项（新 3.11-14） | 编号调整 |
| M4 迁移 | 执行安全检查保留在图节点 | 为 Executor Layer 预留接口 | 平滑过渡 |

