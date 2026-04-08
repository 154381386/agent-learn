# IT Ticket Agent 生产化演进计划（当前单领域落地 / 多领域接口预留 / 少工具版）

## 文档定位

本文档是当前项目**当前收敛阶段的总计划文档**。

配套文档仅保留：

- 总计划：`projects/it-ticket-agent/docs/生产级Agent演进总计划.md`
- 进度与验收：`projects/it-ticket-agent/docs/生产级Agent实施进度.md`
- 下一阶段路线图：`projects/it-ticket-agent/docs/下一阶段多Agent演进计划.md`

历史专项计划与阶段性实施笔记已并入上述文档；其中当前收敛期以本文件和实施进度为准，下一阶段规划以路线图文档为准。

## 文档目标

本文档回答的问题不是“如何把当前项目扩成一个大而全的多 Agent 平台”，而是：

**在当前工具条件很有限的前提下（当前主要只有 CICD MCP tools），如何把系统演进成一个真正可用于生产环境的、流程可靠的 Agent 系统。当前实现聚焦单领域落地，但在模块边界和接口层面为后续多领域扩展预留能力。**

这里的“生产级”重点不放在：

- 一开始就做大量 case 集 / 大规模评测体系
- 一开始就扩很多 tools
- 一开始就做多领域多 Agent 平台
- 一开始就追求复杂的 Debate / Hierarchical / Map-Reduce

而是放在以下能力必须做到生产级：

- 会话管理
- 上下文管理
- 记忆设计
- 状态持久化
- 中断与恢复
- 审批工作流
- 执行安全与审计
- 可观测、可回放、可排障

换句话说：

**先把“单领域 + 少工具”的 Agent runtime 做扎实，再考虑扩工具、扩领域、扩协作模式。**

---

## 1. 当前约束与现实前提

### 当前已知前提

基于当前仓库，系统已经有这些能力：

- 工单入口 API
- `Supervisor` 路由
- `CICD Agent`
- `General SRE Agent`（目前更偏 fallback 占位）
- 独立 RAG 服务
- MCP 接入能力
- 高风险动作审批
- 审批后的恢复执行

但在现实约束上，当前更应该承认以下事实：

1. **真正能依赖的工具很少**，当前主要是 `cicd mcp` 这一条工具链。
2. **没有成熟的大规模 case 集**，短期内不应把“先做大量评测集”作为主阻塞项。
3. **系统当前更像单次 request-response 编排器**，还不是长流程 Agent runtime。
4. **当前最缺的不是更多智能组件，而是可靠的过程控制。**

因此，后续演进目标应该调整为：

> 不追求“先变聪明”，而追求“先变可靠”。

### 当前阶段的边界说明

- **支持多领域扩展，但当前实现不追求一次性做多领域落地**。
- **权限系统暂不接入**，当前执行控制边界以 **审批工作流** 为主。
- 也就是说，当前阶段先不建设细粒度 RBAC / ABAC / tool permission system。
- 只要动作被识别为高风险或执行型动作，就必须进入审批链，由审批来承担主要风险控制职责。

---

## 2. 重新定义当前阶段的“生产级”

在当前条件下，生产级不意味着：

- 可以解决所有类型 IT 事故
- 可以覆盖很多 Agent 和很多 Tool
- 有完整的历史训练闭环
- 有非常复杂的自主规划能力

在当前阶段，“生产级”更适合定义为：

### 2.1 可靠会话

- 每个工单 / 会话都有稳定的 `session_id`
- 任意处理中断后都能恢复
- 用户补充信息、审批结果、外部事件都能继续推进而不是重跑全链路

### 2.2 可控上下文

- 系统知道“当前上下文里有什么、为什么带上、还能保留多久”
- 不依赖无限拼 prompt
- 能区分短期上下文、会话状态、长期记忆

### 2.3 可审计审批

- 高风险动作必须经过正式审批状态机
- 审批记录、审批人、审批意见、超时、恢复过程都可追踪
- 审批不是一个同步 HTTP 开关，而是持久化工作流

### 2.4 可恢复执行

- 每一步执行都能记录 checkpoint
- 失败后可以知道卡在哪一步
- 恢复时从断点继续，而不是从用户第一句话重新跑

### 2.5 有限但可靠的记忆

- 不追求“自动自学习”
- 先做好三种记忆：会话记忆、过程记忆、事件记忆
- 记忆必须结构化、可审计、可清理

### 2.6 单领域可上线

- 当前版本先聚焦 **CICD 处置 runtime** 落地
- 但 `agent registry`、`task contract`、`context contract`、`approval contract`、`resume contract` 要按多领域可扩展方式设计
- 也就是说：**实现先单领域，接口先多领域**

---

## 3. 当前系统的真正短板

当前项目离生产级，主要不是差在“工具数量不够”，而是差在以下几类 runtime 能力：

1. `Supervisor` 仍承担了过多理解与路由职责。
2. graph 仍然主要是单次执行到底，缺少统一的中断 / 恢复协议。
3. 会话状态还没有成为一等公民。
4. 上下文装配还没有形成清晰分层。
5. 记忆设计还没有从“临时字段”升级为“持久化结构”。
6. 审批虽然已有雏形，但还偏同步、偏专用，不是通用工作流。
7. 执行恢复还不是通用 checkpoint 模型。
8. 缺少以事件日志为核心的回放与排障能力。

这些问题决定：

**就算今天再加很多 tools，系统也不会自动变成生产级。**

---

## 4. 本轮重构目标：只做一件事

本轮演进只聚焦一个核心目标：

> **把当前项目重构为“单领域、少工具、但流程可靠”的生产级 Agent runtime。**

也就是说，本轮不以“扩能力边界”为优先，而以“提升流程可靠性与治理能力”为优先。

目标架构如下：

```text
User / API / Webhook
        ↓
Session Layer
        ↓
Context Assembly Layer
        ↓
Interrupt-aware Orchestrator
        ↓
CICD Agent Runtime
        ↓
Approval Workflow / Execution Checkpoint
        ↓
MCP Tool Execution
        ↓
Event Log / Memory / Audit Trail
```

这里最关键的不是多智能，而是这五层：

1. `Session`
2. `Context`
3. `Interrupt / Resume`
4. `Approval`
5. `Execution Checkpoint`

---

## 5. 目标模块拆分（按当前约束收敛）

建议在 `projects/it-ticket-agent/src/it_ticket_agent/` 下优先新增或重构为以下模块：

```text
session/         # 会话生命周期、会话状态持久化
context/         # 上下文装配、裁剪、预算控制
memory/          # 记忆模型：session memory / process memory / case memory
interrupts/      # 通用中断与恢复协议
approval/        # 审批状态机、审批持久化、审批恢复
execution/       # 执行计划、checkpoint、恢复、补偿
events/          # 事件日志、进度流、系统事件回放
orchestration/   # 面向中断与恢复的流程内核
```

现有模块职责收敛：

- `runtime/supervisor.py`：不再承担全部自然语言理解，只做模式判断和任务分发。
- `graph/`：从“固定串行 graph”收敛为“可恢复流程执行层”。
- `agents/cicd.py`：聚焦单领域诊断与执行建议。
- `approval/`：从当前审批 gate 演进为正式工作流。
- `rag_client.py`：继续存在，但作为上下文来源之一，而不是系统中心。

### 5.1 实施红线：禁止 legacy contract 继续扩散

为避免“兼容层反客为主”，当前阶段必须额外遵守以下硬约束：

1. legacy DTO / payload 只能存在于 **API 层、facade 层、compatibility adapter 层**。
2. `graph/`、`runtime/orchestrator.py`、`approval/` 内部主链路必须以正式 domain contract 为准，不能把 legacy payload 当作 canonical model。
3. 不允许继续通过向 legacy `params` 追加字段的方式承载新语义；新增字段必须先进入正式 domain model，再按需投影到兼容层。
4. domain -> legacy 转换只用于对外兼容，不得在核心流程里来回往返，避免把过渡结构重新变成系统中心。
5. 在 A/B 收口和 smoke 护栏补齐前，不继续推进后续阶段实现，尤其不继续扩写 `C` 阶段主链路。

---

## 6. 生产级记忆设计

当前阶段不建议直接上“长期自学习 Agent memory”。

应优先把记忆拆成三层：

## 6.1 会话记忆（Session Memory）

作用：保存当前会话中，后续继续执行必须依赖的信息。

当前代码状态补充：

- session memory 已作为 `ConversationSession` 的结构化字段落地
- 当前 `ExecutionContext.memory_summary` 已优先读取 session memory
- 第一版主要覆盖 key entities、clarification answers、pending approval、pending interrupt、current stage

包括：

- 当前用户原始问题
- 当前结构化意图
- 关键实体（service / cluster / namespace / env）
- 澄清问答结果
- 当前待审批动作
- 当前执行阶段
- 当前未完成中断

特点：

- 生命周期跟随 `session`
- 可覆盖、可更新
- 恢复时优先读取
- 不直接无限追加到 prompt，而是结构化装配

## 6.2 过程记忆（Process Memory）

作用：保存当前这次处置过程中的关键轨迹，方便恢复、审计、解释。

包括：

- 路由决策
- 工具调用摘要
- 关键证据
- 审批请求与审批结果
- 执行步骤结果
- 验证结果
- 人工干预点

特点：

- 是“过程账本”，不是“对话历史全文”
- 恢复时用于决定下一步，而不是全量喂模型
- 必须可审计、可回放

当前代码状态补充：

- 已新增独立 `memory/` 模块与 `ProcessMemoryStore`
- 已落地 `process_memory_entry` 账本表，支持 append / list / summarize
- 第一版已覆盖 routing、clarification、approval、run summary 四类关键轨迹
- `ExecutionContext.memory_summary` 现已同时承载 session memory 与 process memory summary
- 当前过程记忆仍聚焦恢复与解释，尚未扩展为 execution plan 或 system event stream

## 6.3 事件记忆（Incident / Case Memory）

作用：在工单结束后沉淀为结构化历史事件，供后续参考。

包括：

- 症状
- 根因
- 关键证据
- 最终动作
- 是否审批
- 验证是否通过
- 最终结论

特点：

- 不做在线自学习
- 只做可查询、可引用的历史案例资产
- 可先从低复杂度实现：结构化事件记录 + 相似检索

### 当前阶段不建议做的记忆

- 让模型自动写大量自由文本 memory
- 无审计的长期偏好自更新
- 不加清理策略的无限会话历史累积

---

## 7. 生产级上下文管理设计

当前项目后续最容易失控的地方，就是“什么都往 prompt 里塞”。

必须明确区分上下文层次：

## 7.1 输入上下文层

来自当前用户输入与 API 请求：

- message
- ticket_id
- user_id
- service
- cluster
- namespace
- channel

这是原始输入层。

## 7.2 会话状态层

来自 `session` 当前持久化状态：

- 上一轮澄清结果
- 当前中断状态
- 上一轮 agent 结论摘要
- 当前待执行步骤
- 当前审批状态

这是恢复和延续的核心层。

## 7.3 事实与工具上下文层

来自结构化事实或外部依赖：

- RAG 结果
- MCP 工具结果
- 部署状态
- 流水线状态
- 最近变更

这是支撑推理的证据层。

## 7.4 记忆摘要层

来自历史记忆的压缩摘要：

- 当前会话摘要
- 过程账本摘要
- 相似历史事件摘要

这是帮助模型保持连续性，但不能无限增长。

## 7.5 Prompt 预算原则

建议上下文装配遵守以下原则：

1. **优先结构化字段，不优先原文堆叠**
2. **优先当前状态，不优先历史全文**
3. **优先证据摘要，不优先工具原始输出**
4. **优先最近关键节点，不优先完整对话 transcript**

建议新增统一上下文对象：

```text
ExecutionContext
  - request_context
  - session_snapshot
  - pending_interrupt
  - evidence_bundle
  - memory_summary
  - execution_budget
```

由 `context/assembler.py` 统一构造，而不是让各 Agent 自己拼。

当前代码状态补充：

- `ExecutionContext` 与 `ContextAssembler` 已落地
- 当前先作为 runtime 内部 canonical context 使用
- 现有 agent/tool contract 仍保持兼容，先通过 projection 回填到 `TaskEnvelope.shared_context`

---

## 8. 会话保存与恢复：必须作为第一优先级

这部分是当前最值得先做成生产级的能力。

## 8.1 会话模型

建议引入：

```text
ConversationSession
  - session_id
  - ticket_id
  - status
  - current_stage
  - current_agent
  - pending_interrupt_id
  - last_checkpoint_id
  - created_at
  - last_active_at
  - closed_at
```

## 8.2 会话轮次模型

```text
ConversationTurn
  - turn_id
  - session_id
  - role            # user / assistant / system / tool
  - content
  - structured_payload
  - created_at
```

## 8.3 中断模型

```text
InterruptRequest
  - interrupt_id
  - session_id
  - ticket_id
  - type            # clarification / approval / external_event
  - reason
  - question
  - expected_input_schema
  - status          # pending / answered / expired / cancelled
  - resume_token
  - timeout_at
  - created_at
  - resolved_at
```

## 8.4 Checkpoint 模型

```text
ExecutionCheckpoint
  - checkpoint_id
  - session_id
  - stage
  - state_snapshot
  - next_action
  - created_at
```

## 8.5 恢复原则

恢复不能靠“再发一次原问题”。

恢复必须遵守：

1. 读取 session 当前状态
2. 读取最近 checkpoint
3. 读取未完成 interrupt
4. 读取必要 evidence 摘要
5. 从指定 stage 恢复，而不是重新跑完整 graph

### 目标 API

建议把接口从“单次提交 ticket”升级为：

- `POST /api/v1/conversations`
- `POST /api/v1/conversations/{session_id}/messages`
- `POST /api/v1/conversations/{session_id}/resume`
- `GET /api/v1/conversations/{session_id}`
- `GET /api/v1/conversations/{session_id}/events`
- `POST /api/v1/approvals/{approval_id}/decision`

当前代码状态：

- `conversations` 的 create / message / resume / query 已落地
- `/resume` 当前仅支持 approval interrupt
- `/events` 尚未实现，留待后续事件账本阶段

### 验收标准

- 用户补充信息后，流程能从澄清断点继续。
- 审批通过后，流程能从审批断点继续。
- 服务重启后，未完成会话仍可恢复。
- 恢复结果基于 checkpoint，而不是重新从头推理。

当前代码状态补充：

- approval 恢复已优先读取 checkpoint，再回退到 session snapshot / approval payload snapshot
- clarification resume 已落地，采用 checkpoint-first + ticket flow re-entry
- `/resume` 当前已支持 approval + clarification 两类 interrupt
- external_event 的通用恢复仍未实现，留待后续扩展
- `/events` 与执行层细粒度 checkpoint 仍未实现

---

## 9. 审批体系：必须升级为正式工作流

当前审批不应该只是“高风险动作前弹一个 gate”。

在当前少工具场景下，审批反而更应该先做扎实，因为：

- 可执行动作少，但风险高
- 用户对执行类动作的信任建立，首先依赖审批与审计
- 一旦要上线，审批链比“多 Agent”更重要

## 9.1 审批状态机

建议最低支持：

```text
pending
  -> approved
  -> rejected
  -> expired
  -> cancelled
```

如果后续需要再扩：

```text
pending
  -> escalated
  -> auto_approved
```

但第一阶段可以先不做复杂多级审批。

## 9.2 审批数据模型

```text
ApprovalWorkflow
  - approval_id
  - session_id
  - ticket_id
  - action
  - risk_level
  - reason
  - params
  - status
  - requested_by
  - approver_id
  - comment
  - deadline_at
  - created_at
  - decided_at
```

## 9.3 审批事件模型

```text
ApprovalEvent
  - event_id
  - approval_id
  - event_type      # created / approved / rejected / expired / resumed
  - actor_id
  - detail
  - created_at
```

## 9.4 审批的生产级要求

生产级审批至少要满足：

1. 审批请求必须持久化
2. 审批决定必须有审计事件
3. 审批超时必须能显式处理
4. 审批通过后必须能恢复到断点
5. 审批拒绝后必须能生成确定性结束状态

## 9.5 当前阶段建议

考虑到当前工具少、系统还不复杂，审批阶段建议：

- 先不做复杂会签 / 或签
- 先做单审批人工作流
- 但把状态、持久化、事件、恢复机制一次性做对

### 验收标准

- 审批不依赖同步在线等待。
- 审批请求可以跨进程、跨重启存在。
- 审批后系统可以继续执行，而不是人工重新提交 ticket。
- 所有审批动作都有事件日志。

## 9.6 项目收尾前必须补齐的执行安全硬约束

即使审批工作流与鉴权都已接入，也**不能**把它等同于执行安全已经完成。

项目最后收尾前，必须再补齐以下硬约束：

1. **动作注册表 / 白名单**
   - 所有可执行动作必须在代码中显式注册
   - agent / LLM 只能提出已注册动作
   - 未注册动作即使进入审批，也必须在执行前被拒绝

2. **参数 Schema 校验**
   - 每个动作必须定义允许参数、必填参数、参数类型
   - 执行前必须做结构化校验，禁止透传任意 params 到 MCP tool

3. **审批快照绑定**
   - 审批时展示并持久化 `action + target + params + risk` 的确定性快照
   - 恢复执行时必须校验执行请求与审批快照完全一致
   - 不允许审批后再隐式改写动作名、目标对象或关键参数

4. **执行前二次校验**
   - 执行节点不能仅根据审批通过就直接调用外部 tool
   - 必须再次校验：动作是否注册、参数是否合规、风险级别是否匹配审批结论

5. **执行审计闭环**
   - 审批通过、开始执行、执行完成 / 失败、跳过 / 拒绝，都要形成可查询事件
   - 审计记录里要能看到审批人、审批快照、实际执行动作、执行结果摘要

6. **鉴权与审批职责拆分**
   - 鉴权解决“谁可以批”
   - 动作注册表与执行校验解决“批了以后系统到底能执行什么”
   - 两者都完成，才算形成真正的执行安全闭环

### 这部分的最终验收标准

- 未注册动作无法进入最终执行链
- 任意 prompt 注入都不能让系统执行注册表之外的动作
- 审批通过后，实际执行内容必须与审批时展示的快照一致
- 未授权审批人会被拒绝；已授权审批人也不能批准未注册动作落地执行

---

## 10. 执行层：少工具不等于不需要生产级执行控制

虽然当前主要只有 CICD MCP tools，但执行层仍然需要生产级控制。

## 10.1 当前阶段执行目标

不是上来做大而全的 Saga 平台，而是先做：

- 单步或少量步骤执行
- 每一步有 checkpoint
- 每一步有明确输入 / 输出 / 证据
- 失败能终止并留下恢复点
- 高风险动作执行前必须走审批

## 10.2 最小执行模型

```text
ExecutionPlan
  - plan_id
  - session_id
  - steps
  - status
  - created_at
```

```text
ExecutionStep
  - step_id
  - plan_id
  - action
  - tool_name
  - params
  - status
  - result_summary
  - evidence
  - started_at
  - finished_at
```

## 10.3 当前阶段的执行原则

1. **先做 checkpoint，再做复杂补偿**
2. **先做显式状态，再做自动恢复**
3. **先做单领域稳定执行，再做跨系统长链路**

也就是说，当前阶段不要求完整 Saga 全量落地，但要求：

- 执行过程可记录
- 执行失败可定位
- 执行完成可验证
- 执行后可追责

---

## 11. 事件日志：生产级的底座

当前阶段即使不先上 Kafka，也必须把事件日志作为一等公民。

建议统一记录以下系统事件：

- `conversation.created`
- `message.received`
- `intent.understood`
- `agent.started`
- `tool.called`
- `tool.finished`
- `interrupt.created`
- `approval.pending`
- `approval.approved`
- `approval.rejected`
- `execution.started`
- `execution.step_finished`
- `execution.failed`
- `conversation.resumed`
- `conversation.closed`

这些事件至少用于：

- 回放
- 排障
- 审计
- 用户进度查看

### 当前阶段建议

先做：

- 持久化事件表
- 查询 API
- 会话事件流查看

后续再考虑：

- SSE 推送
- Webhook 推送
- 外部告警接入

---

## 12. 评估与 case 集：降级为“非阻塞项”

你现在没有很多 case 集，这不应该成为当前生产化重构的阻塞点。

因此这里建议调整原则：

### 12.1 不要求先做大规模 evals

当前阶段不把以下内容作为主前置：

- 大规模路由 case 集
- 大规模 RAG benchmark
- 全量多场景自动回归集

### 12.2 但要保留最小护栏

即使没有大量 case，也至少要有：

- 3~5 个典型 CICD 场景 smoke cases
- 3~5 个审批恢复场景
- 2~3 个会话中断恢复场景

目的不是做研究型评测，而是保证：

- 改完 session 不会把审批搞坏
- 改完审批不会把恢复搞坏
- 改完上下文装配不会让 CICD agent 完全失真

### 12.3 当前阶段更关键的指标

当前最应该先看的是过程可靠性指标，而不是智能效果指标：

- 会话恢复成功率
- 审批恢复成功率
- 中断后继续执行成功率
- 执行步骤落账完整率
- 事件日志完整率
- 重启后未完成会话恢复成功率

这比先做大量 case 更符合当前阶段。

---

## 13. 分阶段重构路线（按当前约束重排）

## Phase A：会话化与统一中断恢复

### 目标

先把系统从“单次 ticket 请求”升级成“有 session、有 checkpoint、能恢复”的 runtime。

### 核心任务

- 新增 `session/`
- 新增 `interrupts/`
- 为 `IncidentState` 增加 `session_id`、`pending_interrupt`、`last_checkpoint_id`
- API 升级为 conversation 模式
- 把当前审批恢复链路迁移成统一 resume 机制的一种特例

### 验收标准

- 用户补充信息后可从断点恢复
- 审批通过后可从断点恢复
- 服务重启后 session 可恢复

---

## Phase B：上下文层与记忆层成型

### 目标

把“临时拼 prompt”升级为正式的 context assembly + memory 模型。

### 核心任务

- 新增 `context/assembler.py`
- 新增 `memory/` 模块
- 明确 session memory / process memory / incident memory 三层
- 引入统一 `ExecutionContext`
- 工具结果统一沉淀为 evidence summary，而不是原始全文透传

### 验收标准

- Agent 不再直接消费散乱字段
- 恢复时能使用结构化上下文而不是全量历史文本
- 长会话不会无限膨胀 prompt

---

## Phase C：审批工作流正式化

### 目标

把当前审批从“功能点”升级成“正式子系统”。

### 核心任务

- 审批状态机收敛
- 审批持久化模型统一
- 审批事件表落地
- 审批恢复基于 session + checkpoint
- API 与事件日志打通

### 验收标准

- 审批跨进程、跨重启仍然可靠
- 审批拒绝 / 通过 / 超时都有清晰状态
- 审批历史可追踪可回放

---

## Phase D：执行控制与事件账本

### 目标

把当前执行从“调用一下 tool”升级成“可追踪的受控执行”。

### 核心任务

- 新增 `execution/` 模块
- 每个执行步骤有状态、结果、证据、checkpoint
- 统一系统事件模型
- 增加会话事件查询 API

### 验收标准

- 执行每一步都能查到
- 失败点明确
- 能根据 checkpoint 决定是否恢复

---

## Phase E：再考虑增强智能，而不是反过来

### 目标

在 runtime 已经可靠后，再逐步加强：

- 更好的意图理解
- 更好的检索策略
- 更复杂的执行规划
- 更丰富的工具接入

这里强调：

**这些都应该建立在前四个 Phase 稳定之后。**

---

## 14. 当前阶段建议的数据表 / 存储对象

在当前约束下，优先补齐这些对象即可：

- `conversation_session`
- `conversation_turn`
- `interrupt_request`
- `approval_workflow`
- `approval_event`
- `execution_plan`
- `execution_step`
- `execution_checkpoint`
- `system_event`
- `incident_case`（可晚一点）

这些对象的意义是：

- 支撑恢复
- 支撑审计
- 支撑回放
- 支撑排障
- 支撑后续增强

不是为了“平台化好看”。

---

## 15. 最小可行生产里程碑（按当前目标重定义）

## Milestone 1：可靠会话系统

完成：

- Phase A
- Phase B

此时系统具备：

- 会话保存
- 中断恢复
- 上下文装配
- 记忆分层

这时它已经不是 demo，而是 **可靠会话型 Agent runtime**。

## Milestone 2：可靠审批型 Agent

完成：

- Phase C

此时系统具备：

- 正式审批工作流
- 审批事件与恢复
- 高风险动作审计闭环

这时它已经可以被视为 **准生产级执行型 Agent**。

## Milestone 3：可靠执行型 Agent

完成：

- Phase D

此时系统具备：

- 执行 checkpoint
- 执行事件账本
- 失败恢复与排障能力

这时系统才接近当前约束下的 **生产级单领域 Agent 系统**。

---

## 16. 最终结论

基于当前现实条件：

- tools 不多
- 当前核心可依赖的是 CICD MCP tools
- 没有大量 case 集

项目仍然完全可以朝生产级方向演进。

但正确路线不是：

- 先堆更多工具
- 先造很多 agent
- 先做复杂多模式协作
- 先追求大量智能能力

而应该是：

```text
先会话
  -> 再上下文
  -> 再记忆
  -> 再审批
  -> 再执行控制
  -> 最后再增强智能能力
```

也就是说，在当前阶段，真正决定项目是否能成为生产级 Agent 系统的，不是工具数量，而是：

- 会不会丢上下文
- 能不能持久化状态
- 能不能从中断恢复
- 审批是否可靠
- 执行是否可审计
- 故障后是否可回放

如果这些能力做到位，哪怕当前只有一条 CICD 工具链，这个项目也可以先成为：

**一个生产级的、单领域、少工具、但过程可靠的 Agent 系统。**
