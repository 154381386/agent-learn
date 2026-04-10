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

---

## 当前总代办

---

### T6. Feedback Gate + IncidentCase 增强

推荐阶段：**Phase 3**

目标：

- 在执行完成后引入人工确认 / 纠正入口
- 将确认结果写回 `IncidentCaseStore`
- 为后续权重自适应提供结构化样本

建议交付物：

- `graph/nodes.py` 中的 `feedback_gate`
- `knowledge` 或等价存储层中的 `IncidentCase` 扩展字段
- `interrupts` 中的反馈中断模型
- `events` 中的 feedback 事件

完成标准：

- 支持“确认根因正确”与“纠正实际根因”
- `IncidentCase` 至少增加 `human_verified`、`hypothesis_accuracy`、`actual_root_cause_hypothesis`
- 反馈不阻塞主链路结束，但可被完整审计与回放

---

### T7. Ranker 权重自适应

推荐阶段：**Phase 3**

目标：

- 基于历史反馈样本动态调整 Ranker 权重
- 让 `evidence_strength / confidence / history_match` 的权重随历史准确率演化

建议交付物：

- `orchestration/ranker_weights.py`
- 权重持久化与加载机制
- 离线回放 / 统计脚本

完成标准：

- 可基于历史样本重新计算权重
- 权重更新过程可审计、可回滚
- 默认权重与自适应权重切换策略明确

---

### T8. 多轮对话中的 Topic Shift + Skill 动态追加

推荐阶段：**Phase 3**

目标：

- 识别用户中途改口或问题视角变化
- 在下一轮重新装配上下文时提升最新输入优先级
- 当问题扩展到新方向时，支持追加新的 Skill 分类，而不是重载全部 Skill

建议交付物：

- `runtime/topic_shift_detector.py`
- `session_memory` 或等价结构中的 `current_intent_history`
- Skill 增量加载策略
- 对挂起中的 approval / feedback / execute 的改口处理策略

完成标准：

- 历史上下文不会丢失，但不会压过用户最新问题
- 新一轮 `ContextSnapshot` 可增量纳入新 Skill 分类
- 对已挂起中断的取消、复用、重算规则明确且可观测

---

### T9. Legacy Graph 清理与迁移

推荐阶段：**Phase 4**

目标：

- 清理旧的 `RuleBasedSupervisor`、`BaseDomainAgent`、`Aggregator`、`ticket_graph`
- 将 API / runtime 默认入口迁移到 `hypothesis_graph`
- 清理旧 registry 与多领域 Agent 配置残留

建议交付物：

- `runtime/supervisor.py` 迁移或下线
- `agents/`、`agent_registry/`、`agents/registry/` 的清理计划
- `graph/builder.py` 默认入口切换
- 兼容期迁移说明

完成标准：

- 新主路径默认生效
- 旧主路径不再承担默认流量
- 旧组件删除或明确标记废弃，并有最小迁移说明

---

### T10. 生产化补齐

推荐阶段：**Phase 4**

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

10. `Legacy Graph 清理与迁移`
11. `生产化补齐`

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
