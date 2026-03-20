# Supervisor + SubAgent + MCP 重构计划

## 背景

当前系统的主要问题：

- 单一 `SREAgent` 承载过多工具，随着领域扩展会出现工具膨胀与 prompt 稀释。
- 平台团队承担了大量 REST API 到 Tool 的胶水开发与维护工作，新增领域和 API 变更成本高。
- 现有编排更偏向中心化诊断流程，不利于未来扩展到多领域协作、并行排查和顺序处置。

本计划的目标是将当前架构演进为：

- `Supervisor`：只负责路由、协作模式选择和结果汇总，不直接持有工具。
- `Domain SubAgent`：每个领域 Agent 只挂载本领域能力和专属 prompt。
- `MCP`：领域团队通过 MCP Server 暴露工具，平台团队只维护连接与治理层。
- `Platform Governance`：统一承载审批、审计、Tracing、日志、权限、超时和结果协议。

## 目标架构

```text
Supervisor (0 tools, 0 MCP)
  ├── CICD Agent       -> Jenkins MCP, GitLab MCP
  ├── Cost Agent       -> FinOps MCP
  ├── Bastion Agent    -> Security MCP
  ├── Container Agent  -> K8s MCP, Prometheus MCP
  ├── Database Agent   -> DBA MCP, Grafana MCP
  ├── General SRE      -> 通用诊断与兜底能力
  └── Synthesize       -> 纯汇总，不直接连工具
```

## 设计原则

- `Supervisor` 纯编排，不直接执行业务工具。
- `SubAgent` 只处理本领域任务，输出结构化 `AgentResult`。
- 工具实现尽可能下沉到领域团队的 `MCP Server`，平台团队负责连接和治理。
- 子 Agent 只返回摘要，不返回完整对话历史，避免上下文膨胀。
- 敏感操作统一纳入审批策略，不因工具来源不同而绕过平台控制。

## 标准协议

### TaskEnvelope

Supervisor 下发给 SubAgent 的标准任务结构建议包含：

- `task_id`
- `ticket_id`
- `goal`
- `mode` (`router` / `fan_out` / `pipeline`)
- `shared_context`
- `upstream_findings`
- `constraints`
- `priority`
- `deadline`
- `allowed_actions`

### AgentResult

SubAgent 返回给 Supervisor 的标准结果结构建议包含：

- `agent_name`
- `domain`
- `status`
- `summary`
- `findings`
- `evidence`
- `recommended_actions`
- `risk_level`
- `confidence`
- `open_questions`
- `needs_handoff`
- `raw_refs`

### ToolPolicy

平台对工具和 MCP 能力的治理策略建议包含：

- `read_only`
- `mutating`
- `sensitive`
- `requires_approval`
- `requires_human_confirmation`
- `max_timeout_sec`
- `rate_limit`
- `audit_required`

## 协作模式路线

- `Router`：Supervisor 路由到单一领域 Agent，作为默认路径。
- `Fan-out`：Supervisor 并行派发到多个领域 Agent，用于跨域排查。
- `Pipeline`：Supervisor 顺序流转多个阶段 Agent，用于告警响应或处置链路。

推荐上线顺序：

1. 先落地 `Router`
2. 再落地 `Fan-out`
3. 最后引入 `Pipeline`

## 分阶段任务

### Phase 0：方案冻结与协议设计

目标：统一目标架构、数据协议和团队边界，避免后续返工。

任务：

- [ ] 明确平台团队与领域团队的职责边界
- [ ] 确认 `Supervisor`、`SubAgent`、`Synthesize` 的职责定义
- [ ] 设计 `TaskEnvelope`、`AgentResult`、`ToolPolicy` 的初版 schema
- [ ] 设计 `shared_context`、`task_context`、`private_context` 的上下文隔离规则
- [ ] 明确敏感操作清单与审批策略
- [ ] 确认模型分层策略：`Supervisor -> Haiku`、`Domain Agent -> Sonnet`、`Synthesize -> Opus`
- [ ] 明确首批接入领域与优先级，建议以 `CICD` 为第一领域

交付物：

- [ ] 架构决策文档（ADR）
- [ ] 协议文档与 JSON Schema
- [ ] 领域拆分清单与 Owner 列表

验收标准：

- [ ] 团队对最终目标架构和边界无重大分歧
- [ ] 协议已能支持 Router、Fan-out、Pipeline 三种模式
- [ ] 审批策略已覆盖高风险动作

### Phase 1：引入 Supervisor，跑通单领域 Router

目标：在不大规模推翻现有代码的前提下，跑通 `Supervisor + 1 个领域 Agent`。

任务：

- [ ] 将现有 `SREAgent` 重命名为 `General SRE Agent`
- [ ] 新增 `Supervisor`，仅负责路由与任务分发，不直接持有工具
- [ ] 从现有能力中拆出 `CICD Agent`
- [ ] 给 `CICD Agent` 配置专属 system prompt 与工具集合
- [ ] 实现 `TaskEnvelope -> SubAgent -> AgentResult` 基础调用链
- [ ] 实现 Router 模式的 LangGraph 节点或等价运行时流程
- [ ] 保持现有对外 API 兼容
- [ ] 补充基础 tracing / logging，让一次请求能串起 Supervisor 与 SubAgent

交付物：

- [ ] `Supervisor` 基础实现
- [ ] `CICD Agent` 基础实现
- [ ] Router 模式调用链
- [ ] 首版结构化 `AgentResult`

验收标准：

- [ ] 单领域问题可以由 Supervisor 正确路由到 `CICD Agent`
- [ ] 每个 Agent 只绑定自己的少量工具，不再全量加载
- [ ] 对外工单入口与结果格式保持兼容或可平滑兼容

### Phase 2：平台治理层与 MCP 连接层

目标：把工具运行治理和 MCP 连接平台化，为后续多领域复制打基础。

任务：

- [ ] 实现 `MCP Connection Manager`
- [ ] 设计 `mcp_connections.yaml` 的配置格式
- [ ] 实现 Agent 到 MCP Server 的绑定关系
- [ ] 对 MCP tool schema 做标准化校验与缓存
- [ ] 统一超时、重试、熔断、限流策略
- [ ] 增加统一 tracing、结构化日志、审计事件
- [ ] 实现工具级别的 `ToolPolicy`
- [ ] 将审批逻辑平台化，支持对敏感 MCP 工具执行前拦截
- [ ] 为尚未具备 MCP 开发能力的团队准备 `YAML -> MCP` 生成器方案

交付物：

- [ ] `MCP Connection Manager`
- [ ] `mcp_connections.yaml` 配置样例
- [ ] 平台治理层：审批、审计、Tracing、日志、策略控制
- [ ] `YAML -> MCP` 生成器设计文档

验收标准：

- [ ] 平台团队无需再为每个领域 API 手写 Tool 翻译层
- [ ] 新接一个领域能力时，主要是新增连接与策略配置
- [ ] 敏感工具在 MCP 场景下依然能被统一审批与审计

### Phase 3：扩展领域 Agent，落地 Fan-out

目标：把架构从“单领域验证”扩展到“多领域并行排查”。

任务：

- [ ] 新增 `Database Agent`
- [ ] 新增 `Container Agent`
- [ ] 新增 `Bastion Agent`
- [ ] 新增 `Cost Agent`
- [ ] 梳理每个领域 Agent 的 prompt、MCP 列表、权限边界和超时策略
- [ ] 实现 Fan-out 模式，支持 Supervisor 并行派发多个 SubAgent
- [ ] 实现多 Agent 结果冲突检测与去重逻辑
- [ ] 新增 `Synthesize` 节点或 Agent，负责汇总 `AgentResult`
- [ ] 仅在复杂跨域归因时启用更强模型做根因归因

交付物：

- [ ] 4 个新增领域 Agent
- [ ] Fan-out 并行编排能力
- [ ] 统一结果汇总器 / `Synthesize`

验收标准：

- [ ] 跨域问题可以并行调用多个领域 Agent
- [ ] Supervisor 汇总时只消费结构化摘要，不依赖完整历史对话
- [ ] 成本和延迟在可接受范围内，且明显优于单大 Agent 全量工具方案

### Phase 4：引入 Pipeline，支撑告警与处置链路

目标：支持顺序流转的告警响应和处置流程。

任务：

- [ ] 设计 `Triage -> Diagnosis -> Remediation` 的标准阶段协议
- [ ] 实现 Pipeline 模式的阶段交接逻辑
- [ ] 支持阶段失败重试与超时降级
- [ ] 增加阶段完成度检查（completeness check）
- [ ] 为执行型工具接入更严格的审批与通知链路
- [ ] 支持对异步任务的等待与恢复

交付物：

- [ ] Pipeline 运行时流程
- [ ] 阶段交接协议
- [ ] 异步作业等待与恢复机制

验收标准：

- [ ] 告警类问题可以走顺序处置流程
- [ ] 高风险动作必须通过审批后才能继续执行
- [ ] 中断、恢复、超时、失败回退流程清晰可观测

### Phase 5：精细化优化与规模化治理

目标：降低成本、提高质量、增强可观测性和可维护性。

任务：

- [ ] 按复杂度做 Agent 内部模型动态切换
- [ ] 仅在必要时启用高成本模型做综合推理
- [ ] 为高复杂度 Agent 预留 `Skill` 机制
- [ ] 增强评估体系，建立路由准确率、工具使用成功率、审批命中率等指标
- [ ] 增加跨 Agent trace 可视化与运行分析
- [ ] 补充故障注入与回归测试集

交付物：

- [ ] 模型动态切换策略
- [ ] 评估指标面板
- [ ] 技术债清理与运行优化清单

验收标准：

- [ ] 模型成本可控，复杂场景质量稳定
- [ ] 路由、并发、审批、MCP 调用均有清晰指标可追踪
- [ ] 新增领域可以模板化接入

## 团队分工建议

### 平台团队

- 负责 `Supervisor`、`SubAgent Runtime`、`MCP Connection Manager`
- 负责 `TaskEnvelope`、`AgentResult`、`ToolPolicy` 协议
- 负责审批、审计、Tracing、日志、权限和连接治理
- 负责评估体系、路由策略、汇总逻辑和可观测性

### 领域团队

- 负责本领域 `MCP Server` 的实现和维护
- 负责工具语义设计、输入输出稳定性和领域知识沉淀
- 负责 API 变更时同步更新 MCP Server
- 配合定义敏感动作、权限边界和安全要求

## 优先级建议

强烈建议以下顺序推进：

1. `Phase 0`
2. `Phase 1`
3. `Phase 2`
4. `Phase 3`
5. `Phase 4`
6. `Phase 5`

其中最关键的里程碑是：

- 先用 `Supervisor + CICD Agent + Router` 证明架构可行
- 再用 `MCP Connection Manager` 证明组织协作模式可持续
- 最后再扩展到 Fan-out、Pipeline 和模型精细化优化

## 最小可行里程碑（建议 2~4 周）

- [ ] 完成协议定义：`TaskEnvelope`、`AgentResult`、`ToolPolicy`
- [ ] 上线 `Supervisor`
- [ ] 从现有能力拆出 `CICD Agent`
- [ ] 跑通 Router 模式
- [ ] 接入 1 个真实 MCP Server
- [ ] 跑通基础审批、Tracing、日志链路

达到以上里程碑后，再决定是否继续扩领域或推进 Fan-out。
