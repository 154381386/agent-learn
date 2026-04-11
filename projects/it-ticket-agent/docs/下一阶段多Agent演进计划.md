# IT Ticket Agent 下一阶段 Hypothesis Graph 演进计划（代办版）

## 文档说明

本文档以未完成事项为主；已经落地的关键项会在前面简记为“已完成”，避免和当前代码状态脱节。

本文档以 `projects/it-ticket-agent/docs/最新架构.md` 为准，所有待办均围绕最新的两条主路径展开：

- `direct_answer`：FAQ / 知识咨询直答
- `hypothesis_graph`：`smart_router -> context_collector -> hypothesis_generator -> parallel_verification -> ranker -> approval_gate -> execute -> feedback_gate`

旧的 `subagent_results -> aggregated_result -> root cause selector` 叙事不再作为默认推进方向。

配套文档：

- 最新架构：`projects/it-ticket-agent/docs/最新架构.md`
- 当前进度：`projects/it-ticket-agent/docs/生产级Agent实施进度.md`
- 下一阶段待办：本文档

---

## 已完成

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
