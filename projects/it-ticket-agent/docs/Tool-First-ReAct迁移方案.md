# Tool-First ReAct 迁移方案

## 文档定位

本文档定义 `it-ticket-agent` 从“Skill 驱动的 Hypothesis Graph”向“Tool-First ReAct Supervisor”演进的目标架构、阶段拆分与设计边界。

这不是当前线上架构说明，而是**下一阶段重构方案**。

## 当前落地进度

截至当前代码状态：

- Phase 1 已完成主体落地，新旧 graph 可切换，`react_tool_first` 已可运行
- Phase 2 已完成最小版 `ToolExecutionMiddleware`、风险拦截与基础执行治理
- Phase 3 已完成 Supervisor 护栏、observation ledger、摘要与上下文裁剪
- Phase 4 已完成第一批 execution envelope、timeout / retry / error normalization 与 runtime 调试入口
- Phase 5 尚未开始，旧 `skills/` 体系仍在仓库中

因此，这份文档仍然是迁移蓝图，但项目已经进入 **Phase 4 收尾、Phase 5 未启动** 的阶段。

## 目标

目标是把当前诊断链路收敛成：

- 保留轻路由（FAQ / SOP / 直接问答 fast path）
- 删除 `skills/` 抽象层
- 改为 `Supervisor Agent + ToolExecutionMiddleware + Tool-first ReAct`
- 高风险 Tool 通过中间件强制走审批
- 用 ReAct 主循环替代固定诊断管线

一句话概括：

> 保留轻路由，删除 skill，改成 tool-first ReAct；高风险 tool 通过 middleware 强制审批拦截；再补齐 Supervisor 护栏与 tool 执行治理。

---

## 当前架构的主要问题

当前架构的核心问题不是“功能不够”，而是：

- `skill` 层过重，承担了不必要的规划与编排职责
- 固定 graph 让简单问题也必须走完整假设链路
- 高层诊断策略和底层工具执行耦合较深
- ReAct 主循环没有成为系统中心
- 工具风险控制和工具执行治理还没有统一收口到 tool 层

对当前项目而言，更合适的方向是：

- 让 Supervisor 直接看到 tool schema
- 让 LLM 在 ReAct 中直接决定调用哪些 tool
- 让 runtime 中间件负责审批、超时、重试、标准化结果

---

## 目标架构

```text
User Request
    ↓
Light Router
    ├─ direct_answer → Finalize
    └─ Supervisor Loop (ReAct)
         ├─ call_tool(...)
         ├─ parallel_call_tools([...])
         ├─ ask_user(...)
         ├─ output(...)
         └─ re-think / replan (lightweight)
                ↓
         ToolExecutionMiddleware
           ├─ risk check
           ├─ approval intercept
           ├─ timeout / retry
           └─ result normalization
                ↓
             Tool Runtime
                ↓
      approval_gate / await_user / execute_approved_action
                ↓
             Finalize
```

### 核心原则

1. **轻路由保留**
   - FAQ / SOP / 明确知识问答继续走 fast path
   - 诊断类请求才进入 ReAct Supervisor

2. **删除 Skill 层**
   - 不再保留 `SkillCategory` / `SkillSignature` / `SkillResult`
   - 不再由 skill 组合 tool 决定诊断路径
   - 直接暴露 tool schema 给 LLM

3. **风险控制下沉到 Tool 层**
   - 高风险 tool 不允许直接执行
   - 必须经 `ToolExecutionMiddleware` 拦截后进入审批链

4. **降级决策交给 Supervisor**
   - 不硬编码 `fallback_tools`
   - tool 失败后返回结构化错误
   - 下一步怎么换 tool、补问用户、结束，交给 Supervisor 自己 think

5. **Supervisor 护栏优先于 Tool 治理**
   - 先保证 agent 不跑飞
   - 再补 timeout / retry / standardization

---

## 新 Graph 设计

新 graph 采用 7 节点结构：

- `light_router`
- `direct_answer`
- `supervisor_loop`
- `approval_gate`
- `await_user`
- `execute_approved_action`
- `finalize`

### 节点职责

#### 1. `light_router`
负责把请求分成两类：

- FAQ / SOP / 直接问答 → fast path
- 诊断问题 → `supervisor_loop`

#### 2. `direct_answer`
承载 FAQ / SOP / 明确知识问答的 fast path，并把结果交给 `finalize`。

#### 3. `supervisor_loop`
ReAct 主循环节点，负责：

- think
- decide next action
- call tool / parallel call tools
- ask user
- output
- 判断是否继续下一轮

普通只读 Tool 在这个节点内部直接调用，不离开 graph；但所有调用都必须经过统一的 `ToolExecutionMiddleware`。

它是系统的主脑。

#### 4. `approval_gate`
处理高风险 tool / action 的审批拦截，并把审批等待统一交给 `await_user`。

#### 5. `await_user`
统一承载 clarification / approval / feedback 等 interrupt。

恢复后的去向必须显式路由：

- clarification → `supervisor_loop`
- approval approved → `execute_approved_action`
- feedback → `finalize`

#### 6. `execute_approved_action`
只负责审批通过后的高风险 tool / action 执行。

注意：普通 Tool 不经过这个节点；这个节点只服务于审批后的执行动作。

#### 7. `finalize`
负责最终输出、事件落库、状态收敛。

---

## Phase 规划

## Phase 1：ReAct Supervisor + 7 节点 Graph + 新旧并存

### 目标

- 新建 `ReActSupervisor`
- 新建 7 节点 graph
- 保留旧 graph，通过配置切换新旧模式
- 保留轻路由
- 增加 `direct_answer` fast path 节点
- 让 Supervisor 直接可见 tool schema

### 关键点

- 新增 `orchestration_mode = legacy | react_tool_first`
- FAQ / SOP fast path 继续保留：`light_router -> direct_answer -> finalize`
- 诊断类请求进入 `supervisor_loop`
- 普通 Tool 在 `supervisor_loop` 内直接调用，不经过独立 graph 节点
- 高风险 Tool / Action 走：`supervisor_loop -> approval_gate -> await_user -> execute_approved_action`
- 暂时不删旧代码

### 输出物

- `runtime/react_supervisor.py`
- `graph/react_builder.py`
- `graph/react_nodes.py`
- `direct_answer` 节点实现
- 配置切换入口

---

## Phase 2：ToolExecutionMiddleware + 精简元数据 + 风险拦截

### 目标

所有风险治理统一收敛到 tool 层，不再依赖 skill 层。

### `BaseTool` 最小元数据

只保留：

- `risk_level`
- `retryable`
- `timeout_sec`

其余属性通过规则推导：

- `requires_approval = (risk_level >= high)`
- `parallel_safe = (risk_level == low)`
- `mutates_resource = (risk_level >= medium)`

### Middleware 职责

`ToolExecutionMiddleware` 是**所有 Tool 调用的统一入口**，不论调用发生在：

- `supervisor_loop` 内部
- `execute_approved_action` 节点内部

执行前统一做：

- tool 是否注册
- risk 是否需要审批
- 当前会话是否已有批准快照
- 参数是否满足执行要求
- 是否允许执行

### 原则

- `risk_level >= high`：不直接执行，进入审批链
- `risk_level < high`：允许直接执行
- `ApprovalPolicy` 关键词匹配保留，作为兜底

---

## Phase 3：Supervisor 护栏 + 上下文窗口管理

### 目标

先保证 ReAct Supervisor 不跑飞，再控制上下文膨胀。

### 护栏

建议最少具备：

- `max_iterations`
- `max_tool_calls`
- `confidence_threshold`
- `stop_reason`
- `max_parallel_branches`

### 上下文窗口管理

ReAct 多轮执行时，旧 observation 会不断堆积，因此先收敛为两个机制：

- 超过 `summary_after_n_steps` 的旧 observation 摘要化
- `pinned_findings` 永远不被裁剪

建议先保留一个核心预算字段：

- `max_context_tokens`

`working_memory_summary` 是运行时产物，不作为第一版配置项独立暴露。

### 原则

- 最近 observation 保留原文
- 较早 observation 摘要化
- 关键发现长期 pin 住
- 上下文预算超限时优先裁剪非关键观察结果

---

## Phase 4：Tool 超时 / 重试 + 结果标准化

### 目标

把 tool 的执行行为做成稳定 runtime，而不是把降级逻辑硬编码在 graph 里。

### 超时

每个 tool 有：

- `timeout_sec`

### 重试

每个 tool 有：

- `retryable`
- 默认 `max_retries`
- 默认 backoff 策略

### 结果标准化

所有 tool 都统一返回：

- `status`
- `summary`
- `payload`
- `evidence`
- `retry_count`
- `latency_ms`
- `error_type`（失败时）

### 特别说明

**不硬编码 fallback 链。**

如果 tool 超时或失败：

- runtime 只返回结构化错误
- 下一步该换哪个 tool、是否 ask user、是否结束，由 Supervisor 自己判断

---

## Phase 5：删除旧 Skill 体系与固定诊断管线

### 删除目标

- `skills/` 整个目录
- `SkillCategory`
- `SkillSignature`
- `SkillResult`
- 旧 skill registry / skill executor
- 旧固定诊断 graph 节点

### 保留并复用

虽然固定 graph 节点会删除，但这些能力实现大概率仍保留为内部组件：

- `context_collector`
- `hypothesis_generator`
- `ranker`
- `approval`
- `interrupt`
- `execution`
- `session`
- `checkpoint`
- `events`

也就是说：

> 删除的是“旧组织方式”，不是把所有底层能力实现物理删除干净。

---

## 不做的事情

为了避免过度设计，当前方案明确不做：

- 不做硬编码 `fallback_tools`
- 不做复杂的 `parallel_safe / concurrency_group` 元数据体系
- 不在第一阶段引入完整 Plan-and-Execute 显式计划对象
- 不保留 skill 作为中间抽象层

---

## 迁移验收标准

满足以下条件后，才进入 Phase 5 删除旧代码：

- 新 graph 能处理 FAQ fast path 与诊断路径
- 高风险 tool 能稳定进入审批链
- Supervisor 有明确 stop 条件，不会无限循环
- observation summary 机制可控
- tool timeout / retry / result normalization 稳定
- 新旧链路至少完成一轮对比验证

---

## 最终落地形态

最终系统形态应该是：

- 一个 `ReAct Supervisor`
- 一套轻路由
- 一组直接暴露给 LLM 的 tools
- 一个统一的 ToolExecutionMiddleware
- 一条审批 / interrupt / execution / finalize runtime

而不是：

- `skill` 抽象层
- 固定的 hypothesis graph
- 每个请求都强制走假设生成 → 并行验证 → 排序
