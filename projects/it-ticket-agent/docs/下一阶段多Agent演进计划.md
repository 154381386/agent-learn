# IT Ticket Agent 下一阶段 Tool-First ReAct 演进计划（代办版）

## 文档说明

本文档以**下一阶段未完成事项 + 历史迁移记录**为主，不是当前架构说明。

当前实现请先看：

- `docs/最新架构.md`
- `README.md`

当前默认主链已经不再是旧的 `hypothesis_graph + skill` 固定流水线，而是：

- `direct_answer`
- `react_tool_first`

也就是：

- `smart_router -> direct_answer`
- `smart_router -> supervisor_loop -> approval_gate -> await_user / execute_approved_action -> finalize`

当前还需要额外注意两点：

- `context_collector` 是 `supervisor_loop` 内部步骤，不是当前 React Graph 独立节点
- `feedback` 通过 `finalize + interrupt + orchestrator resume` 完成，不存在当前主图内的 `feedback_gate`

本文档保留了大量 `hypothesis_graph / skill` 表述，主要用于记录历史迁移背景；**除本文开头的当前基线外，下文不应作为当前实现依据**。

本文档以 `projects/it-ticket-agent/docs/最新架构.md` 与 `projects/it-ticket-agent/docs/Tool-First-ReAct迁移方案.md` 为准，所有待办均围绕当前两条主路径展开：

- `direct_answer`：FAQ / 知识咨询直答
- `react_tool_first`：`smart_router -> supervisor_loop -> approval_gate -> await_user / execute_approved_action -> finalize`

旧的 `subagent_results -> aggregated_result -> root cause selector` 与 `skill-first fixed pipeline` 叙事都不再作为默认推进方向。

配套文档：

- 最新架构：`projects/it-ticket-agent/docs/最新架构.md`
- 当前进度：`projects/it-ticket-agent/docs/生产级Agent实施进度.md`
- 下一阶段待办：本文档

---

## 已完成

说明：

- 下列 T0 ~ T9 主要用于记录从旧主线迁移到当前主线过程中已经完成的能力
- 其中涉及 `hypothesis_graph / skill` 的表述应视为**历史迁移背景**
- 当前继续演进时，应优先复用已有 `router / supervisor / approval / execute / feedback` 能力，而不是恢复旧 graph

### [x] T0. Smart Router + FAQ Fast Path

已完成内容：

- `runtime/query_classifier.py`
- `runtime/query_classifier_rules.yaml`
- `runtime/smart_router.py`
- `graph/builder.py`、`graph/nodes.py` 中的 `smart_router` / `rag_direct_answer`
- `runtime/orchestrator.py` 中对 `direct_answer` / `hypothesis_graph` 的入口衔接

完成结果：

- FAQ / 知识咨询可直接返回答案
- 需要排查或操作的问题统一进入 `hypothesis_graph`
- Router 已输出结构化 `route_decision`
- fast path 保留 session / turn / event / checkpoint 账本

### [x] T1. Context Collector + Skill Level 0/1 过滤

已完成内容：

- `skills/registry.py`
- `skills/catalog.py`
- `state/models.py` 中的 `ContextSnapshot` / `SkillSignature` / `SimilarIncidentCase`
- `graph/state.py` 中的 `context_snapshot`
- `graph/nodes.py` 中的 `context_collector`

完成结果：

- `ContextSnapshot` 已包含 `request`、`rag_context`、`similar_cases`、`available_skills`
- Skill 过滤已基于上下文关键词与请求字段运行
- `hypothesis_graph` 入口现在先经过 `context_collector`

### [x] T2. Hypothesis Generator

已完成内容：

- `orchestration/hypothesis_generator.py`
- `state/models.py` 中的 `Hypothesis`、`VerificationStep`
- `graph/builder.py`、`graph/nodes.py` 中的 `hypothesis_generator`
- `incident_state` / `graph_state` 中对 `hypotheses` 的承载

完成结果：

- 可基于 `ContextSnapshot` 生成 1-3 个结构化根因假设
- 每个假设都带有 Skill 粒度的 `verification_plan`
- 当前无 LLM 时走规则生成，有 LLM 时可切换为模型生成
- `hypothesis_graph` 当前输出已包含 `hypotheses`

### [x] T3. VerificationAgent + Parallel Verification

已完成内容：

- `orchestration/verification_agent.py`
- `orchestration/parallel_verifier.py`
- `state/models.py` 中的 `SkillResult`、`EvidenceItem`、增强后的 `VerificationResult`
- `graph/builder.py`、`graph/nodes.py` 中的 `parallel_verification`

完成结果：

- 统一 `VerificationAgent` 已按 `verification_plan` 执行 Skill 级验证
- 多个假设已通过 `parallel_verification` 并行验证
- `diagnosis` 当前输出已包含 `verification_results`
- 当前验证执行默认走规则型 Skill 执行器，后续可替换为真实 Skill / MCP 实现

### [x] T4. Ranker + Single Primary Action

已完成内容：

- `orchestration/ranker.py`
- `state/models.py` 中的 `RankedResult`
- `graph/builder.py`、`graph/nodes.py` 中的 `ranker`
- `incident_state` 中对 `ranked_result` 与单一 `approval_proposals` 的挂载

完成结果：

- `verification_results` 已被收敛成 `primary / secondary / rejected`
- `primary` 结果唯一，且带有结构化打分元数据
- 当前只保留 `primary` 的建议动作进入 `approval_proposals`
- `diagnosis` 当前输出已包含 `ranked_result`

### [x] T5. Approval Gate + Execute 对接

已完成内容：

- `graph/builder.py` 中的 `ranker -> approval_gate -> execute`
- `graph/nodes.py` 中面向新主链路的 `approval_gate` / `execute`
- `execution/security.py` 中对 operation Skill 动作的执行注册
- auto-approved 主动作的执行复用现有审批恢复执行逻辑

完成结果：

- `RankedResult.primary` 的动作提案已接入审批流
- 高风险动作会生成审批中断并返回 `awaiting_approval`
- 低风险动作可在主图中 auto-approve 并直接执行
- 执行结果、checkpoint、execution plan 与事件账本继续复用现有实现

### [x] T9. Legacy Graph 清理与迁移

已完成内容：

- 旧的 `runtime/supervisor.py`
- 旧的 `orchestration/aggregator.py`、`orchestration/parallel_dispatcher.py`
- 旧的 `agents/`、`agent_registry/`、`agents/registry/` 及对应测试
- `graph` / `runtime` / `state` 中只服务旧主路径的兼容残留

完成结果：

- 新主路径已经成为唯一默认入口
- 旧的 supervisor / subagent / aggregator 编排已从代码主干移除
- 仓库当前只保留 `smart_router + hypothesis_graph + skill/tool` 主线
- 相关回归测试已切换到新路径验证

### [x] T6. Feedback Gate + IncidentCase 增强

已完成内容：

- `graph/nodes.py` 中已补齐反馈中断创建与反馈请求事件
- `runtime/orchestrator.py` 中已补齐 feedback resume 与案例回写逻辑
- `memory/models.py`、`memory/store.py`、`memory_store.py` 中已扩展 `IncidentCase` 反馈字段
- `interrupts` 中已增加 `feedback` 类型中断
- `tests/test_runtime_smoke.py` 中已覆盖 feedback interrupt 与反馈回写场景

完成结果：

- 执行/诊断完成后可创建人工确认反馈中断
- 支持“确认根因正确”与“纠正实际根因”
- `IncidentCase` 已增加 `human_verified`、`hypothesis_accuracy`、`actual_root_cause_hypothesis`
- 反馈结果可审计、可恢复、可回放

### [x] T7. Ranker 权重自适应

已完成内容：

- `orchestration/ranker_weights.py`
- `orchestration/ranker.py` 中对历史反馈样本的权重解析
- 基于 sqlite 的权重快照持久化、激活与切换逻辑
- `tests/test_skill_scenarios.py` 中对权重估计和快照切换的验证

完成结果：

- 可基于历史反馈样本重新计算权重
- 支持权重快照持久化与激活切换
- 默认权重与自适应权重已具备清晰切换路径
- 基础回滚能力可通过切换历史快照实现

### [x] T8. 多轮对话中的 Topic Shift + Skill 动态追加

已完成内容：

- `runtime/topic_shift_detector.py`
- `runtime/orchestrator.py` 中的 `current_intent_history` 记录
- `graph/nodes.py` 中的 skill 增量加载整合
- 挂起中的 `approval / feedback` 遇到改口时的 supersede / cancel / recompute 逻辑
- `tests/test_runtime_smoke.py` 中对 topic shift 与 pending approval supersede 的验证

完成结果：

- 可检测用户中途改口和话题迁移
- 新一轮 `ContextSnapshot` 可增量纳入新的 Skill 分类
- 历史上下文保留在 `current_intent_history` 中
- 对挂起中的中断已经具备取消、重算与审计记录

---

## 当前总代办

当前约束：

- `T10`、`T11`、`T12`：当前只做设计说明，暂不进入代码实现
- 设计类代办项需要把模块边界、输入输出契约、状态流转、风险点与验收口径写清楚

---

### T10. 生产化补齐

推荐阶段：**Phase 4**

当前处理方式：**仅设计，不实现**

目标：

- 补齐人工升级、权限、值班交接、存储抽象、完整可观测性、测试护栏
- 让新架构具备持续演进与线上运维能力

建议分项：

- `Manual Escalation / Human Handoff`
- `Permission Middleware / Auth / RBAC`
- `Repository / Storage Migration`
- 完整可观测性：`routing / context / hypothesis / verification / ranker / approval / execute / feedback`
- 测试扩展：graph integration、Mock LLM、Golden、恢复 / 审批 / feedback 回归

完成标准：

- 人工接管、审批、执行、反馈、交接均可追踪
- 权限拒绝具备结构化原因与审计记录
- 主逻辑不直接绑定单一存储实现
- 核心链路具备稳定回归测试

---

### T11. 自动化任务 / Cron 预诊断

推荐阶段：**Phase 4**

当前处理方式：**仅设计，不实现**

目标：

- 让系统支持定时巡检、定时预诊断与定期 case 回放
- 将 `skill` / `tool` 主链路扩展到主动运行，而不只是在用户提问时触发
- 为后续告警联动、日报周报与值班辅助打基础

建议交付物：

- `automation` 或 `cron` 模块

### T13. 上下文解析 Agent（前置实体解析 / 槽位补全 / 软硬澄清）

推荐阶段：**Phase 4**

当前处理方式：**仅设计，不实现**

目标：

- 将当前规则驱动的前置解析层，继续演进为受控的上下文解析 agent
- 对服务类、机器类、数据库类问题统一做实体识别、槽位补全与缺失分级
- 在进入 `hypothesis_graph` 前，就决定是：
  - 直接进入诊断
  - 先给通用建议再软澄清
  - 直接进入硬澄清

建议步骤：

1. 先尝试解析实体
   - service
   - host_identifier
   - db_name / db_type

2. 不确定时查询外部上下文源
   - `CMDB`
   - `Service Registry`
   - `DB Registry`

3. 基于返回结果更新判断
   - 合并推测槽位
   - 标记推测来源与置信度

4. 必要时发起下一轮查询
   - 允许有限轮数的上下文补全

5. 最终决定处理方式
   - 直接答
   - 软澄清：给通用建议 + 提示补槽位
   - 硬澄清：缺关键定位字段，必须中断

完成标准：

- 前置解析层不再只是静态规则集合
- 推测出的 `environment / cluster / namespace` 等值必须支持“要求用户确认或覆盖”
- 对不同问题类型有明确的 `required_slots_for_diagnosis`
- clarification policy 支持软澄清与硬澄清分流

### T14. 统一多环节并行 Tool 调度（Skill 抽象层 + 受控 Tool Fan-out）

推荐阶段：**Phase 4**

当前处理方式：**仅设计，不实现**

目标：

- 让 `Supervisor`、`SubAgent` 等各环节都具备“单轮并行 fan-out 多个 tool”的能力
- 保留 `Skill` 作为 Tool 的组织层和 SOP 抽象层，而不是唯一入口
- 避免让所有 agent 直接面对同一个全量 tool 池

设计原则：

1. 所有 agent 环节都应支持：
   - 一轮规划
   - 多个 tool calls
   - 并行执行
   - 汇总 observation
   - 再进入下一轮规划

2. `Skill` 的定位应收敛为：
   - 默认 SOP
   - 推荐工具集合
   - Tool 白名单组织层

3. 不同 agent 看到的 tool 子集必须隔离：
   - `Supervisor`：检索 / 上下文类工具
   - `SubAgent`：本领域验证类工具
   - `Executor`：动作类工具

4. 并行执行的失败处理需要统一：
   - 单个 tool 失败不应直接打穿整个 skill / agent round
   - 需返回结构化 partial failure

5. 工具体系要分层：
   - 公共工具：检索、CMDB、Registry、状态、告警等只读能力
   - Skill 工具：按领域或 SOP 组织的专属工具集合

6. `Skill` 要承担渐进式加载角色：
   - 默认不向 agent 暴露全量 tool schema
   - 先加载当前任务相关的公共工具和 `skill` 对应工具
   - 只有在证据不足或任务扩展时，才按需加载新的 `skill / tool`

7. `SubAgent` 不应绑定固定领域身份：
   - `SubAgent` 是任务执行单元
   - tool 可见集合应按当前任务动态裁剪
   - 不应简单静态绑定为 `k8s agent / db agent / network agent`

建议步骤：

1. 为各 agent 层统一抽象“单轮 fan-out”执行器
2. 收敛 `Skill` 为 tool 组织层，而不是唯一调用入口
3. 建立公共工具层
   - `search_knowledge`
   - `search_case_memory`
   - `lookup_cmdb_host`
   - `lookup_service_registry`
   - `lookup_db_registry`
   - `check_status / alerts`
4. 显式配置不同 agent 的 tool 可见子集
4. 对并行执行增加 partial failure / timeout / retry 策略

完成标准：

- `Supervisor` 和 `SubAgent` 都支持单轮并行 tool fan-out
- `Skill` 仍然保留，但角色从“唯一执行入口”降级为“默认 SOP / Tool 组织层”
- agent 不会共享一个无边界的全量 tool 池
- 公共工具层与 skill 工具层职责清晰
- `Skill` 支持渐进式加载，不再一次性暴露全量工具
- 并行 tool 调用具备统一容错和审计记录

### T15. Supervisor 从 Plan-and-Execute 演进到 ReAct Supervisor

推荐阶段：**Phase 4 / Phase 5**

当前处理方式：**仅设计，不实现**

当前现状：

- `Supervisor` 仍然是典型 `Plan-and-Execute`
- 先生成完整验证计划
- 并发交给 `SubAgent`
- 最后统一收敛结果

目标：

- 将 `Supervisor` 从一次性计划器，演进成多轮调度的 `ReAct Supervisor`
- 支持在执行过程中根据部分 observation 动态调整验证策略
- 让系统具备更强的任务级自主推进能力

设计演进方向：

当前模式：

1. 先出完整计划
2. `SubAgents` 执行
3. 最后统一收敛

目标模式：

1. 先出一个初始计划
2. 根据部分 observation 再改计划
3. 动态增删 `hypothesis / subagents`
4. 多轮调度直到收敛

建议步骤：

1. 为 `Supervisor` 增加中途重规划能力
2. 支持根据局部验证结果提升 / 降低 hypothesis 优先级
3. 支持取消无价值 `subagent`
4. 支持在必要时新增 hypothesis 并启动新的 `subagent`
5. 对多轮 supervisor 决策补齐 trace / event / audit

完成标准：

- `Supervisor` 不再只是一次性计划器
- 能根据中途 observation 重排验证顺序
- 能动态增删 hypothesis 和 subagent
- 多轮调度过程可观测、可回放、可测试

### T16. Skill 机制彻底落地（Skill Pack / SOP / Tool 组织层）

推荐阶段：**Phase 4 / Phase 5**

当前处理方式：**仅设计，不实现**

当前现状：

- 已有 `SkillRegistry + SkillPackLoader + Skill Pack` 的最小能力
- `SkillSignature` 已能承载 `planning_mode / tool_names / sop_summary / guide_path`
- 但当前 pack 数量很少，`Skill` 仍未完全收敛成稳定的 Tool 组织层和 SOP 层

目标：

- 将 `Skill` 从“部分代码内建能力”升级成完整的领域执行抽象
- 让 `Skill` 成为：
  - 默认 SOP
  - Tool 白名单组织层
  - 规划与执行的稳定边界

建议步骤：

1. 补齐 `Skill Pack` 机制
   - pack manifest
   - guide / SOP
   - tool 白名单
   - pack 级测试

2. 明确 `Skill` 的角色
   - 不是唯一执行入口
   - 而是 Tool 的组织层和默认执行模板

3. 收敛 skill 执行模式
   - `serial`
   - `llm_parallel`
   - `react_subagent`

4. 为常见领域补 pack
   - k8s / pod crash
   - network / timeout
   - db / slow query / pool saturation
   - cicd / deploy regression

完成标准：

- `Skill Pack` 不再只是单个示例
- 不同领域已有稳定的 skill/SOP/tool 组织方式
- `Skill` 在 supervisor / subagent / executor 三层中的角色清晰
- skill 级测试和回归样例可覆盖核心领域

### T17. Agentic RAG 完整化（动态检索 / Query Rewrite / 多轮补检索）

推荐阶段：**Phase 4 / Phase 5**

当前处理方式：**仅设计，不实现**

当前现状：

- 已有最小版本的 Agentic RAG
  - 初始检索
  - query rewrite
  - 二次补检索
  - 合并上下文
- 但仍然偏向受控扩展层，距离完整 Agentic RAG 还有差距

目标：

- 让检索真正成为推理过程的一部分
- 让 `Supervisor` 和 `SubAgent` 都能基于证据缺口动态补检索
- 在不破坏边界的前提下，把 `rag-service` 变成统一检索执行端

建议步骤：

1. 强化 query rewrite
   - 基于 hypothesis / subagent 类型生成更聚焦的子查询

2. 支持多轮补检索
   - 不只一次扩展
   - 允许根据 observation 再次检索

3. 检索源统一
   - knowledge
   - case-memory
   - 后续可扩展到 registry / cmdb / db metadata

4. 完善检索回流
   - 记录哪些 query 命中有效
   - 为后续检索策略优化提供样本

完成标准：

- 检索不再只是固定前置步骤
- `Supervisor / SubAgent` 都可基于缺口动态补检索
- query rewrite 和补检索过程可观测、可回放
- `rag-service` 成为统一检索执行层
- 定时任务配置模型与持久化
- 预置任务模板：`SLO 巡检`、`错误预算检查`、`高风险服务健康扫描`
- 自动化任务执行日志、失败重试与结果审计

完成标准：

- 可按 cron 周期触发 `hypothesis_graph` 或指定 `skill`
- 自动化任务可查看最近执行记录、状态与关键证据
- 支持跳过重复任务、失败重试与基础告警

---

### T12. 多入口接入（API / Webhook / IM / Alert Ingress）

推荐阶段：**Phase 4**

当前处理方式：**仅设计，不实现**

目标：

- 把当前单一 API 入口扩展为统一 ingress 层
- 支持用户提问、告警事件、Webhook 推送、IM 消息进入同一条主链路
- 统一会话、审计、权限与事件模型，避免每种入口单独实现一套逻辑

建议交付物：

- `ingress/` 或等价模块
- 标准入站事件模型：`user_message`、`alert_event`、`webhook_event`、`approval_callback`
- Webhook 路由与签名校验
- IM 渠道适配层（先从飞书/企业微信二选一）
- 告警事件到 `hypothesis_graph` 的映射规则

完成标准：

- 普通会话 API、Webhook、至少一种 IM 渠道共用统一入口协议
- 告警事件可以直接触发预诊断，并生成可追踪 session
- 不同入口的上下文归一到同一个 `TicketRequest` / `IncidentState` 语义层

---

## 推荐实施顺序

### 第一批：先打通诊断主干

1. `Smart Router + FAQ Fast Path`
2. `Context Collector + Skill Level 0/1 过滤`
3. `Hypothesis Generator`
4. `VerificationAgent + Parallel Verification`
5. `Ranker + Single Primary Action`

### 第二批：接入执行与学习闭环

6. `Approval Gate + Execute 对接`
7. `Feedback Gate + IncidentCase 增强`
8. `Ranker 权重自适应`
9. `Topic Shift + Skill 动态追加`

### 第三批：迁移与生产化

10. `Feedback Gate + IncidentCase 增强`
11. `Ranker 权重自适应`
12. `Topic Shift + Skill 动态追加`
13. `生产化补齐`
14. `自动化任务 / Cron 预诊断`
15. `多入口接入（API / Webhook / IM / Alert Ingress）`

---

## 里程碑

### N0：入口分流可用

完成条件：

- `smart_router` 跑通
- FAQ / 知识咨询走 `direct_answer`
- 需要排查的问题进入 `hypothesis_graph`

### N1：假设驱动诊断链路可用

完成条件：

- `ContextSnapshot` 跑通
- `Hypothesis Generator` 输出结构化假设
- `VerificationAgent` 可按计划并行验证
- `Ranker` 输出 `primary / secondary / rejected`

### N2：审批与执行闭环可用

完成条件：

- `approval_gate` 与 `execute` 接通
- 操作 Skill 可审批、可执行、可恢复
- 默认只推进 `primary` 动作

### N3：反馈学习闭环可用

完成条件：

- `feedback_gate` 完成
- `IncidentCase` 扩展字段落地
- Ranker 支持基于反馈样本调整权重

### N4：完成迁移并具备生产化护栏

完成条件：

- 旧 `ticket_graph` / `Aggregator` / 领域 Agent 退出默认主路径
- 权限、人工升级、值班交接、可观测性、测试体系具备最小生产能力

### N5：具备主动运行与多入口接入能力

完成条件：

- 定时自动化任务可稳定触发并审计
- API、Webhook、至少一种 IM / Alert Ingress 已打通
- 多入口事件统一进入主链路，具备一致的 session / trace / event 语义
