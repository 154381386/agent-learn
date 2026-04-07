# IT Ticket Agent 实施进度与验收清单

## 文档定位

本文档是当前项目**唯一进度文档**。

配套文档仅保留：

- 总计划：`projects/it-ticket-agent/docs/生产级Agent演进总计划.md`
- 进度与验收：`projects/it-ticket-agent/docs/生产级Agent实施进度.md`

历史专项计划与阶段性进度笔记已并入上述两份文档，不再单独维护。

## 文档目的

本文档用于作为当前代码实施阶段的**唯一进度确认文档**。

它不再只是“现状同步”，而是直接用于回答下面这些问题：

- 现在准备先做什么
- 每一项做到什么程度算完成
- 当前做到哪一步了
- 下一步应该改哪些模块
- 哪些能力已经可以进入代码实现

本文档与下列文档配套使用：

- 演进方向：`projects/it-ticket-agent/docs/生产级Agent演进总计划.md`
- 实施清单：本文档

---

## 当前实施范围（本轮必须收敛）

### 目标

本轮不是做“大而全”的多 Agent 平台，而是先做成一个：

**当前单领域落地、接口支持未来多领域扩展、工具数量有限但流程可靠的生产级 Agent runtime。**

### 当前约束

- 当前主要可依赖工具：`CICD MCP tools`
- 可以预留多领域接口，但当前实现先聚焦 `CICD`
- 权限系统暂不接入
- 当前执行控制边界以 **审批工作流** 为主
- 不把大量 case 集作为开工前置条件
- 只保留最小 smoke 验收集

### 本轮必须做到生产级的能力

- 会话保存
- 上下文管理
- 记忆分层
- 中断与恢复
- 审批工作流
- 执行 checkpoint
- 事件日志 / 回放能力

---

## 状态标记说明

- `[ ]` 未开始
- `[~]` 进行中
- `[x]` 已完成
- `BLOCKED` 有阻塞，不能继续推进

---

## 当前总体判断

当前项目状态：

```text
代码骨架已可运行
生产化方向文档已完成
M1（session / interrupt）已进入代码落地
M2（context / memory）已进入主体实现，B1~B4 已落地
M3（approval workflow）已部分重构到正式子系统
M4（execution / event ledger）尚未开始主体实现
```

因此，从实施角度看：

- 设计输入：**已具备**
- 当前代码已进入分阶段实现
- 当前推荐继续推进点：**先收口 A/B 偏差并补齐 S1 ~ S4 smoke 验收**

---

# 里程碑总览

## M1：可靠会话系统

目标：把系统从单次 request-response 升级为可保存、可中断、可恢复的会话型 runtime。

完成标准：

- 有 `session` 持久化模型
- 有 `interrupt` 持久化模型
- 有 `checkpoint` 持久化模型
- 用户补充信息后能从断点恢复
- 审批通过后能从断点恢复
- 服务重启后仍可恢复未完成会话

状态：`[~]`

---

## M2：可靠上下文与记忆系统

目标：把散乱输入、会话状态、证据、历史摘要统一收敛成正式上下文装配层。

完成标准：

- 有统一 `ExecutionContext`
- 有 `context/assembler.py`
- 明确区分 session memory / process memory / incident memory
- prompt 不再直接拼接全部历史文本
- 恢复依赖结构化上下文，而不是全文重跑

状态：`[~]`

---

## M3：可靠审批工作流

目标：把当前审批从功能点升级为正式工作流子系统。

完成标准：

- 审批请求持久化
- 审批状态机明确
- 审批事件可审计
- 审批通过/拒绝/超时均有确定性状态
- 审批结果能驱动会话恢复

状态：`[~]`

---

## M4：可靠执行与事件账本

目标：把当前 tool 执行升级为可追踪、可恢复、可排障的执行控制层。

完成标准：

- 有 `ExecutionPlan` / `ExecutionStep` / `ExecutionCheckpoint`
- 每一步执行都有输入/输出/证据/状态
- 失败点可定位
- 有统一系统事件日志
- 可以按会话查看关键执行过程

状态：`[ ]`

---

# 实施清单（按顺序推进）

## A. 会话与恢复（M1）

### A1. 会话模型落地

状态：`[x]`

实际实现说明：

- 已落地 `session/models.py`、`session/store.py`、`session_store.py`
- 已新增 `session/service.py` 收口 session 生命周期基础操作
- 已接入 `main.py` 生命周期注入与 `runtime/orchestrator.py` 会话创建/更新
- 当前实现已支持 session create/query/update 和按 `thread_id` 查询
- 当前对外已提供 `GET /api/v1/sessions/{session_id}` 与 `GET /api/v1/sessions/by-thread/{thread_id}` 查询接口
- 当前已补齐 `current_agent`、`last_checkpoint_id`、`closed_at`
- 当前 A1 交付物已补齐，`session/service.py` 已单独抽出

目标：新增 `ConversationSession` 持久化对象。

建议落点：

- `src/it_ticket_agent/session/`
- `src/it_ticket_agent/state/`
- `src/it_ticket_agent/main.py`

交付物：

- `session/models.py`
- `session/store.py`
- `session/service.py`

最小字段：

- `session_id`
- `ticket_id`
- `status`
- `current_stage`
- `current_agent`
- `pending_interrupt_id`
- `last_checkpoint_id`
- `created_at`
- `last_active_at`
- `closed_at`

验收标准：

- 能创建会话
- 能查询会话
- 能更新当前阶段状态
- 服务重启后会话信息仍存在

确认方式：

- 能通过 API 创建并查询 session
- 能在 store 中看到持久化记录

---

### A2. 会话轮次记录落地

状态：`[x]`

实际实现说明：

- 已在 `session/models.py` 增加 `ConversationTurn`
- 已在 `session/store.py` 增加 `conversation_turn` 表以及 append/list 能力
- 已在 `session_store.py` 暴露 `append_turn` / `list_turns`
- 已在 `runtime/orchestrator.py` 的 ticket 与 approval 决策边界写入 `user` / `assistant` turn
- 当前仍未开放 public turn API，读取能力先保留在内部，供后续 A5/A6 复用

目标：保存用户、系统、工具交互的轮次记录。

建议落点：

- `src/it_ticket_agent/session/`
- `src/it_ticket_agent/events/`

交付物：

- `ConversationTurn` 模型
- turn 持久化接口

最小字段：

- `turn_id`
- `session_id`
- `role`
- `content`
- `structured_payload`
- `created_at`

验收标准：

- 每次用户消息都能落 turn
- 系统关键回复可落 turn
- 恢复时能按 session 读取最近轮次

确认方式：

- 创建会话、发送消息后可查到 turn 记录

---

### A3. Interrupt 模型落地

状态：`[x]`

实际实现说明：

- 已落地 `interrupts/models.py`、`interrupts/store.py`、`interrupts/service.py`、`interrupt_store.py`
- 已支持 pending / answered / cancelled / expired
- 当前对外已提供 `GET /api/v1/interrupts` 与 `GET /api/v1/interrupts/{interrupt_id}` 查询接口
- approval interrupt 已接入主链路
- clarification / external_event 目前仅完成模型与 service，尚未接入通用恢复流程

目标：把澄清、审批、外部事件等待统一建模为 interrupt。

建议落点：

- `src/it_ticket_agent/interrupts/`

交付物：

- `interrupts/models.py`
- `interrupts/store.py`
- `interrupts/service.py`

最小字段：

- `interrupt_id`
- `session_id`
- `ticket_id`
- `type`
- `reason`
- `question`
- `expected_input_schema`
- `status`
- `resume_token`
- `timeout_at`
- `created_at`
- `resolved_at`

验收标准：

- 可以创建 interrupt
- 可以查询 pending interrupt
- 可以标记 answered / cancelled / expired

确认方式：

- 构造一个澄清 interrupt 后能持久化并查询

---

### A4. Checkpoint 模型落地

状态：`[x]`

实际实现说明：

- 已新增 `checkpoints/` 与 `checkpoint_store.py`
- 已落地 `ExecutionCheckpoint` 模型与 sqlite store
- session 已增加 `last_checkpoint_id`
- 已在 `_run_ticket_message()` 和 `handle_approval_decision()` 两个稳定边界写 checkpoint
- approval 恢复当前已改为 checkpoint-first，缺失 checkpoint 时仍会回退到 session snapshot / approval payload snapshot
- 当前仍未实现 per-step execution checkpoint，也未开放 checkpoint public API

目标：让执行过程可以从断点恢复。

建议落点：

- `src/it_ticket_agent/execution/`
- 或先放 `src/it_ticket_agent/session/`

交付物：

- `ExecutionCheckpoint` 模型
- checkpoint store

最小字段：

- `checkpoint_id`
- `session_id`
- `stage`
- `state_snapshot`
- `next_action`
- `created_at`

验收标准：

- 流程关键阶段能写 checkpoint
- 恢复时能读取最近 checkpoint

确认方式：

- 人为中断后，系统可以从最近 checkpoint 恢复

---

### A5. Conversation API 落地

状态：`[x]`

实际实现说明：

- 已新增 `POST /api/v1/conversations`
- 已新增 `POST /api/v1/conversations/{session_id}/messages`
- 已新增 `POST /api/v1/conversations/{session_id}/resume`
- 已新增 `GET /api/v1/conversations/{session_id}`
- 当前 `/resume` 已收口为 interrupt-first resume contract：恢复对象优先由当前 `session.pending_interrupt_id` 决定，`interrupt_id` 仅做一致性校验
- `approval_id` 当前仅保留兼容字段语义，不能再覆盖当前 session 的 pending interrupt
- 现有 `/api/v1/tickets` 与 `/api/v1/approvals/{approval_id}/decision` 继续保留兼容

目标：把单次 ticket API 扩展为 conversation API。

建议落点：

- `src/it_ticket_agent/main.py`

目标接口：

- `POST /api/v1/conversations`
- `POST /api/v1/conversations/{session_id}/messages`
- `POST /api/v1/conversations/{session_id}/resume`
- `GET /api/v1/conversations/{session_id}`

验收标准：

- 可以新建 conversation
- 可以向 existing session 继续发消息
- 可以显式触发 resume
- 会话状态能查询

确认方式：

- curl/前端调用能完整跑通 create -> message -> resume -> query

---

### A6. 通用恢复流程接入 graph

状态：`[x]`

实际实现说明：

- 已新增 clarification gate，并在缺少关键 service 信息时创建 clarification interrupt
- `/api/v1/conversations/{session_id}/resume` 已升级为 generic interrupt answer intake
- approval interrupt 继续走原专用恢复链
- clarification interrupt 走 checkpoint-first + ticket flow re-entry
- clarification gate 当前已改为 conditional stop，不再在创建 interrupt 后继续错误流入后续节点
- 当前恢复路径已统一优先读取当前 session 的 pending interrupt，并补齐 selector mismatch 拒绝校验
- 当前 A6 第一版仅覆盖 clarification + approval；external_event 与 arbitrary graph node replay 仍未实现

目标：让恢复不再是审批专用逻辑，而是 graph 通用能力。

建议落点：

- `src/it_ticket_agent/graph/`
- `src/it_ticket_agent/orchestration/`
- `src/it_ticket_agent/interrupts/`

验收标准：

- 审批恢复不再是唯一 resume 场景
- 澄清类 interrupt 也能恢复
- 恢复时从指定阶段进入，而不是从头重跑

确认方式：

- 人工制造 clarification interrupt，提交补充信息后能继续推进后续节点

---

## B. 上下文与记忆（M2）

### B1. ExecutionContext 统一模型

状态：`[x]`

实际实现说明：

- 已新增 `context/models.py`，定义最小版 `ExecutionContext`
- 当前 `ExecutionContext` 已包含 `request_context`、`session_snapshot`、`pending_interrupt`、`evidence_bundle`、`memory_summary`、`execution_budget`
- 目前先作为 runtime 内部 canonical context，不额外新增 public API schema

目标：统一请求、会话、证据、记忆摘要的输入对象。

建议落点：

- `src/it_ticket_agent/context/`

交付物：

- `context/models.py`

建议字段：

- `request_context`
- `session_snapshot`
- `pending_interrupt`
- `evidence_bundle`
- `memory_summary`
- `execution_budget`

验收标准：

- Agent / orchestrator 不再直接消费零散字段
- 恢复时能从统一 context 重建输入

确认方式：

- `CICDAgent` 接口改为消费统一上下文对象或由其派生的结构化任务输入

---

### B2. Context Assembler 落地

状态：`[x]`

实际实现说明：

- 已新增 `context/assembler.py`
- 当前 assembler 会把 request/session/interrupt/checkpoint/incident_state/recent_turns 装配成统一上下文
- 当前先通过 projection 回填到 `TaskEnvelope.shared_context`，避免一次性改动所有 agent/tool 签名
- 第一批 consumer 已覆盖 CICD 与 General agent，approval 路径暂保持兼容

目标：由单一模块负责上下文装配，而不是分散拼 prompt。

建议落点：

- `src/it_ticket_agent/context/assembler.py`

验收标准：

- request 输入、session 状态、RAG、tool 证据统一进入 assembler
- assembler 能输出结构化上下文
- 明确上下文裁剪规则

确认方式：

- 关键入口统一走 assembler
- 可打印或调试查看最终上下文结构

---

### B3. Session Memory 落地

状态：`[x]`

实际实现说明：

- `ConversationSession` 已增加 `session_memory`
- `conversation_session` 表已增加 `session_memory_json`
- create / clarification / approval 等稳定边界已开始回写 session memory
- `ContextAssembler.memory_summary` 现已优先读取 `session_memory`，旧数据仍可 fallback 到原有 `incident_state.metadata` 逻辑

目标：保存当前会话必须依赖的信息。

内容至少包括：

- 原始问题
- 当前结构化意图
- 当前关键实体
- 澄清结果
- 当前待审批动作
- 当前执行阶段
- 当前 pending interrupt

验收标准：

- 会话继续时可直接读取
- 不需要重新从 turn 全量推断

确认方式：

- 中断恢复时可直接使用 session memory 重建核心上下文

---

### B4. Process Memory 落地

状态：`[x]`

实际实现说明：

- 已新增 `memory/models.py`、`memory/store.py`、`memory/__init__.py`、`memory_store.py`
- 已落地独立 `process_memory_entry` sqlite ledger，按 session append/list/summarize
- 已在 `graph/nodes.py` 写入 `routing_decision`、`clarification_created`、`approval_requested`
- 已在 `runtime/orchestrator.py` 写入 `clarification_answered`、`approval_decided`、`run_summary`
- 已在 `ContextAssembler` 中接入 `process_memory_summary`，进入 `ExecutionContext.memory_summary`
- 当前仍未扩展为 `ExecutionPlan / ExecutionStep`，也还不是 `system event log`

目标：把处置过程中的关键轨迹沉淀为过程账本。

内容至少包括：

- 路由决策
- 工具调用摘要
- 关键证据
- 审批请求与结果
- 执行步骤结果

验收标准：

- 恢复时可以读取过程账本摘要
- 不需要全量重读所有历史原文

确认方式：

- 能按 session 查询过程账本摘要

---

### B5. Incident Case Memory 最小版本

状态：`[x]`

实际实现说明：

- 已在 `memory/models.py`、`memory/store.py`、`memory_store.py` 增加最小版 `IncidentCase` 与 sqlite upsert/query 能力
- 已在 `runtime/orchestrator.py` 的 terminal session 边界自动沉淀结构化 case
- 已新增 `GET /api/v1/cases` 与 `GET /api/v1/cases/{case_id}` 查询接口
- `ContextAssembler.memory_summary` 已支持携带同 service 的近期 incident cases 摘要
- 已在 `tests/test_runtime_smoke.py` 验证 completed ticket 后可按 service 查询 case 记录

目标：在工单结束后沉淀结构化案例资产。

最小字段：

- 症状
- 根因
- 关键证据
- 最终动作
- 是否审批
- 验证是否通过
- 最终结论

验收标准：

- 工单关闭后可写入一条结构化 case
- 后续能按关键字段查询

确认方式：

- 完成一条工单后，能查到对应 case 记录

---

## C. 审批工作流（M3）

### C1. 审批状态机收敛

状态：`[~]`

实际实现说明：

- 当前审批已具备 pending / approved / rejected 等核心流转语义
- interrupt 侧也已具备 pending / answered / cancelled / expired
- 但审批主模型与状态机约束还未完全统一到单一工作流实现

目标：把审批状态显式建模。

最低状态：

- `pending`
- `approved`
- `rejected`
- `expired`
- `cancelled`

验收标准：

- 状态流转受控
- 非法状态转换被拒绝

确认方式：

- 为每种状态转换编写最小测试 / smoke 验证

---

### C2. 审批工作流模型统一

状态：`[~]`

实际实现说明：

- 已新增 `approval/coordinator.py` 并在 graph 中走 `ApprovalCoordinator`
- 当前 `approval_gate` 持久化已优先走 domain `ApprovalRequest`，不再以 legacy payload 作为主流程 canonical contract
- `runtime/orchestrator.py` 与 `graph/` 的 approval resume 主链已优先传递 domain request，legacy payload 主要收敛在 API / facade 兼容边界
- 目前仍存在 approval facade / legacy payload / domain model 并存的过渡状态，但核心链路对 legacy 的主流程依赖已减少

目标：统一当前 approval facade / legacy / store 的主模型。

建议落点：

- `src/it_ticket_agent/approval/`

验收标准：

- graph 内部以统一审批模型流转
- API 层才做 DTO 转换
- 尽量消除 legacy payload 在核心流程中的反复转换

确认方式：

- 审批主链路内部只保留一套主数据对象

---

### C3. 审批事件账本落地

状态：`[ ]`

实际实现说明：

- 当前只有审批记录与部分链路日志，还没有正式的 approval event ledger
- 该项尚未开始主体实现

目标：审批创建、通过、拒绝、恢复都要有事件。

验收标准：

- 所有审批关键动作都有事件记录
- 能按 approval_id 查询事件序列

确认方式：

- 创建审批、通过审批后可查完整事件链

---

### C4. 审批与 session/resume 打通

状态：`[~]`

实际实现说明：

- approval interrupt 已写入 session.pending_interrupt_id
- 审批决策后会回填 interrupt answered，并更新 session 状态
- 当前恢复仍主要是 approval 专用链路，尚未升级为通用 interrupt-aware resume

目标：审批结果自动驱动会话恢复。

验收标准：

- 审批通过后能恢复到待执行阶段
- 审批拒绝后生成确定性结束状态
- 审批超时后能生成明确状态

确认方式：

- 手动创建审批 -> 决策 -> 恢复流程完整跑通

---

## D. 执行与事件账本（M4）

### D1. ExecutionPlan / ExecutionStep 模型落地

状态：`[ ]`

目标：让执行过程从“调用工具”升级为“受控执行步骤”。

验收标准：

- 至少支持单步 / 少量步骤执行
- 每步有状态、结果摘要、证据

确认方式：

- 执行一次高风险动作前后，能看到 plan 和 step 记录

---

### D2. 执行 checkpoint 写入

状态：`[ ]`

目标：关键步骤执行前后写 checkpoint。

验收标准：

- 失败后能看到最后成功 checkpoint
- 可据此决定恢复策略

确认方式：

- 执行中断后，能从最近 checkpoint 恢复

---

### D3. System Event 日志落地

状态：`[ ]`

目标：统一记录会话、审批、执行、恢复事件。

首批事件：

- `conversation.created`
- `message.received`
- `interrupt.created`
- `approval.pending`
- `approval.approved`
- `approval.rejected`
- `execution.started`
- `execution.step_finished`
- `conversation.resumed`
- `conversation.closed`

验收标准：

- 能按 session 查询关键事件流
- 关键链路可回放

确认方式：

- 完成一条工单后能按时间顺序看到核心事件序列

---

# 最小 smoke 验收集（非阻塞，但必须保留）

当前不要求大量 case 集，但至少保留以下 smoke cases：

## S1. 会话恢复

- 创建会话
- 发起问题
- 系统进入澄清中断
- 用户补充信息
- 系统从断点恢复

状态：`[x]`

实际实现说明：

- 已新增 `tests/test_runtime_smoke.py`
- 已通过 `unittest` 覆盖 clarification interrupt 创建、resume、session memory 更新、process memory 摘要更新

---

## S2. 审批恢复

- 创建高风险动作建议
- 进入审批
- 审批通过
- 系统恢复到执行阶段

状态：`[x]`

实际实现说明：

- 已在 `tests/test_runtime_smoke.py` 中通过稳定 fixture 覆盖 pending approval -> approve -> resume
- 当前 smoke 对执行器外部 MCP 调用采用仓库内 mock，重点验证会话恢复、checkpoint、approval/process memory 主链路

---

## S3. 审批拒绝

- 创建高风险动作建议
- 审批拒绝
- 系统进入确定性结束状态

状态：`[x]`

实际实现说明：

- 已在 `tests/test_runtime_smoke.py` 覆盖 pending approval -> reject -> terminal session state
- 当前验证点包括 `session.status=completed`、`current_stage=finalize` 与 process memory 的 approval decision 记录

---

## S4. 重启后恢复

- 创建未完成会话
- 写入 checkpoint
- 模拟服务重启
- 查询并恢复未完成 session

状态：`[x]`

实际实现说明：

- 已在 `tests/test_runtime_smoke.py` 覆盖“新建 orchestrator 实例后读取既有 session / pending interrupt / checkpoint”
- 当前 smoke 已验证重启后可重新查询 conversation，并从持久化状态恢复 incident snapshot

---

# 当前建议开工顺序

严格按下面顺序继续推进：

1. `S1` ~ `S4` 为 A/B 现状补齐 smoke 验收
2. 收口 A 阶段 conversation / resume / checkpoint 链路中的实现偏差
3. 收口 B 阶段 context / session memory / process memory 与正式 contract 的边界
4. 清理 A/B 主链路中继续扩散的 legacy contract
5. 完成 A/B 收口后，再重新评估是否进入后续阶段
6. `C1` ~ `C4` 审批正式化收口（暂缓，不作为当前开工项）
7. `D1` ~ `D3` 执行与事件账本

---

# 当前开工点

## 当前推荐继续推进的第一项

**`S1` ~ `S4`：先为已落地的 A/B 能力补齐 smoke 验收，并同步收口 legacy contract 偏差**

原因：

- `A1` ~ `A6`、`B1` ~ `B4` 已基本落地，当前主要问题不再是“缺功能”，而是“要和计划严格对齐”
- 当前主链路仍存在 legacy contract 扩散，继续推进 `C` 会放大偏差
- 先把 A/B 的 session、resume、context、memory 与 smoke 护栏收紧，才能保证后续阶段不建立在漂移实现之上

如果继续写代码，默认从这里开始。

---

# 更新规则

## 当前阶段实施红线

1. `legacy contract` 只能停留在 **API / facade / compatibility adapter** 边界。
2. `graph/`、`runtime/orchestrator.py`、`approval/` 核心链路不得继续新增对 legacy approval payload 的主流程依赖。
3. 新能力不得继续通过向 `ApprovalPayload.params` 塞新字段来落地；如需新增语义，必须先进入正式 domain model。
4. domain -> legacy 的转换只允许用于兼容现有外部接口，不能反向成为核心流程的 canonical contract。
5. 在 `S1` ~ `S4` smoke 和 A/B 收口完成前，不继续推进 `C` 阶段实现。

后续每完成一个工作项，都要同步更新以下内容：

1. 将对应状态从 `[ ]` 改为 `[~]` 或 `[x]`
2. 如有实现偏差，在该项下追加“实际实现说明”
3. 如新增必要子项，在对应工作项下补充
4. 如某项被阻塞，标记 `BLOCKED` 并写明原因

这份文档之后应始终反映**真实代码进度**，不能只反映计划。
