# Tool-First ReAct 多 Subagent 并行分析设计方案

## 文档定位

本文档定义 `it-ticket-agent` 在当前 `tool-first ReAct` 主线之上，如何补充 **多 subagent 并行分析** 能力。

本文档解决的问题不是“如何重新设计一套多 Agent 平台”，而是：

- 保持当前 `light_router + supervisor_loop + approval_gate + finalize` 主线不变
- 让根 Supervisor 在复杂问题下可以按需派生多个分析型 subagent
- 保证 subagent 拥有独立上下文、独立工作记忆、独立执行预算
- 保证审批、执行、安全边界仍然收敛在 root session

配套文档：

- 当前迁移基线：`projects/it-ticket-agent/docs/Tool-First-ReAct迁移方案.md`
- 当前实施进度：`projects/it-ticket-agent/docs/生产级Agent实施进度.md`
- 下一阶段路线图：`projects/it-ticket-agent/docs/下一阶段多Agent演进计划.md`
- 当前运行时主入口：`projects/it-ticket-agent/src/it_ticket_agent/runtime/react_supervisor.py`

外部参考：

- `codex-rs/core/src/tools/handlers/multi_agents_v2/spawn.rs`
- `codex-rs/core/src/agent/control.rs`
- `codex-rs/core/src/agent/role.rs`
- `codex-rs/core/src/codex_delegate.rs`
- `codex-rs/core/src/memories/README.md`

---

## 一句话设计结论

当前项目如果要实现 subagent，最合适的方式是：

**把 subagent 作为 root ReAct Supervisor 可调用的一组协作工具，而不是再新建一条固定的多 Agent 主图。**

原因：

- 当前项目已经明确向 `tool-first ReAct` 收敛
- 固定多 Agent graph 会重新引入重 orchestrator、固定 fan-out、固定 aggregator 的复杂度
- 协作工具模式更贴近 `codex` 的 `spawn / wait / close` 设计，也更容易复用当前 `ToolExecutionMiddleware`、session、checkpoint、event ledger

---

## 当前问题

当前 `it-ticket-agent` 的 `ReactSupervisor` 已能处理：

- FAQ / SOP fast path
- 单 agent ReAct 推理
- tool 调用与中间件治理
- clarification / approval / feedback 恢复
- execution ledger / system event / checkpoint

但它仍有一个明显边界：

- 复杂问题下仍由一个 supervisor 持续串行分析
- 多条假设链、多个工具域、多个证据面会压到同一个上下文窗口里
- 一旦问题横跨多个领域，根 Supervisor 既要分解任务，又要亲自做所有分析

这会带来三个直接问题：

1. 上下文容易膨胀
2. 多域分析相互污染，难以形成清晰的局部结论
3. Root Supervisor 同时承担协调和执行，复杂度过高

---

## 设计目标

### 目标

- 在复杂问题下允许 root Supervisor 并行派生多个分析型 subagent
- 每个 subagent 具备独立上下文、独立 observation ledger、独立 budget
- root Supervisor 只做任务分解、结果汇总、风险决策、审批与最终输出
- subagent 结果统一收敛为结构化证据板，不直接写最终用户回复
- 保持当前 graph 主结构稳定，不额外引入大型固定流程

### 非目标

- 不恢复旧的 `skill-driven hypothesis graph` 作为默认主线
- 不在第一版支持 subagent 自己再派生 subagent
- 不允许 subagent 直接执行高风险动作
- 不允许 subagent 自己发起独立审批流
- 不把所有历史上下文完整复制给每一个 subagent

---

## 总体架构

### 保持不变的主图

当前主图继续保持：

```text
light_router
  -> direct_answer
  -> supervisor_loop
       -> approval_gate
       -> await_user
       -> execute_approved_action
       -> finalize
```

### 新增的协作能力

在 `supervisor_loop` 内部增加协作工具面：

- `spawn_analysis_agents`
- `wait_analysis_agents`
- `list_analysis_agents`
- `close_analysis_agent`
- `send_analysis_followup`

这意味着：

- 是否派生 subagent 由 root Supervisor 决定
- subagent 生命周期由 root Supervisor 管理
- subagent 是 ReAct 运行时的一类“高级工具”
- graph 不需要增加新的 `fanout / join` 固定节点

### Root 与 Subagent 的职责边界

#### Root Supervisor 负责

- 判断问题是否足够复杂，需要并行分析
- 选择要派生哪些 subagent
- 给每个 subagent 分配任务和工具边界
- 等待与汇总 subagent 结果
- 决定是否继续派生第二波 subagent
- 统一处理 clarification / approval / execute / finalize

#### Subagent 负责

- 在自己被授权的上下文与工具范围内完成分析
- 产出结构化结论、证据、置信度、下一步建议
- 在需要时返回动作提案
- 不直接给用户输出最终答复
- 不直接进入审批或执行高风险动作

---

## 何时触发 subagent

不是所有工单都应触发并行分析。

建议引入 `DelegationPolicy`，只有在满足以下信号时才允许派生：

- 当前问题跨多个工具域
- Root Supervisor 连续两轮后仍低置信度
- 当前存在多个互斥假设，且都需要验证
- 用户明确要求“全面排查”“并行看看多个方向”
- 当前 observation 已明显膨胀，继续单 agent 分析成本过高
- 当前问题可自然拆成多个相互独立的问题面

首版建议并发上限：

- `max_subagents_per_turn = 3`
- `max_subagent_depth = 1`
- `max_second_wave_subagents = 2`

---

## Subagent 角色模型

第一版不建议一开始就做大量角色。

建议先收敛成 3 到 4 类：

- `deploy_investigator`
- `runtime_investigator`
- `dependency_investigator`
- `knowledge_investigator`

角色不是为了做“拟人化分工”，而是为了绑定：

- 允许访问的工具域
- 默认推理提示词
- 适合的上下文切片策略
- 输出风格和关注重点

每个 subagent 必须有明确 ownership：

- 它负责哪个问题面
- 它负责哪些工具
- 它不负责哪些结论

这与 `codex` 中 worker role 的 ownership 设计一致。

---

## 上下文隔离设计

### 原则

Subagent 的“独立上下文”不是完全没有共享，而是：

- 共享根问题的只读事实快照
- 不共享 root 的全部原始推理痕迹
- 不共享其它 subagent 的完整工作记忆
- 只把当前子任务真正需要的信息打包给它

### Context Bundle 分层

每个 subagent 接收一个 `SubagentContextBundle`：

```json
{
  "incident_snapshot": {},
  "task_brief": {},
  "relevant_evidence": [],
  "similar_cases": [],
  "recent_turns": [],
  "allowed_tools": [],
  "execution_budget": {}
}
```

#### 1. 共享只读层

- `incident_snapshot`
- 用户原始请求
- 当前已解析出的 `service / cluster / namespace / environment`
- Root 已确认的结构化事实
- 可选的 RAG 摘要与历史 case 摘要

#### 2. 子任务专属层

- 当前任务目标
- 该子任务关注的问题面
- 允许使用的工具列表
- budget 与 timeout
- 当前子任务已知假设

#### 3. 子 agent 私有层

- `observation_ledger`
- `working_memory_summary`
- `pinned_findings`
- 局部 scratchpad

这部分不自动回流到父级全文上下文。

### Fork 策略

建议借鉴 `codex` 的 fork 模式，支持：

- `FullHistory`
- `LastNTurns(n)`

但在当前项目中默认应使用：

- `LastNTurns(1)` 或 `LastNTurns(2)`
- 再叠加结构化 `ContextBundle`

默认不应给 subagent 完整会话历史。

### 必须过滤掉的内容

向 subagent 传递 forked history 时，应过滤：

- root 的 reasoning 文本
- 原始 tool 调用输出
- 冗长日志片段
- 已失效的中断上下文
- 其它 subagent 的完整内部过程

可保留：

- system / developer 指令
- 用户问题与补充信息
- root 最终确认过的事实摘要
- 必要的 assistant 最终结论片段

---

## 协作工具设计

### 1. `spawn_analysis_agents`

用途：

- 一次性创建 1 到 N 个分析型 subagent

输入：

- 任务列表
- 角色列表
- 每个任务的 `ContextBundle`
- 每个任务的 `allowed_tools`
- `timeout_ms`
- `max_iterations`
- `max_tool_calls`

输出：

- 创建成功的 `agent_thread_id` 列表
- 每个 subagent 的初始状态

### 2. `wait_analysis_agents`

用途：

- 等待某一批 subagent 完成，或等待 mailbox 出现新结果

行为：

- 支持超时
- 允许部分完成返回
- 不因单个 subagent 失败而让整个 root turn 失败

### 3. `list_analysis_agents`

用途：

- 枚举当前 root session 下面的活跃 subagent

### 4. `close_analysis_agent`

用途：

- 主动关闭已无价值的 subagent
- 超时后释放资源
- 回收 registry slot

### 5. `send_analysis_followup`

用途：

- 对已存在的 subagent 发送补充说明
- 用于 second wave 的微调而不是重新全量 spawn

---

## Subagent 输入输出契约

### 输入契约

```json
{
  "task_id": "a-dependency-1",
  "role": "dependency_investigator",
  "goal": "判断 502 和超时是否来自 DB 或外部依赖",
  "owned_domains": ["db", "network"],
  "allowed_tools": ["db.*", "network.*"],
  "context_bundle": {
    "incident_snapshot": {},
    "relevant_evidence": [],
    "similar_cases": [],
    "recent_turns": []
  },
  "max_iterations": 3,
  "max_tool_calls": 6,
  "timeout_ms": 20000,
  "allow_delegation": false,
  "write_capability": "none"
}
```

### 输出契约

```json
{
  "task_id": "a-dependency-1",
  "status": "completed",
  "summary": "更像连接池耗尽，不像发布回归",
  "findings": [
    {
      "claim": "数据库连接池接近打满",
      "confidence": 0.81
    }
  ],
  "evidence": [
    "db.active_connections=98/100",
    "slow query p95 2.3s"
  ],
  "recommended_next_steps": [
    "补查应用线程池和最近 10 分钟错误日志"
  ],
  "proposed_actions": [
    {
      "action": "db.restart_pool",
      "risk": "high",
      "reason": "连接池已阻塞"
    }
  ],
  "uncertainty": [
    "缺少最近 10 分钟应用日志"
  ],
  "tool_calls_used": 4,
  "stop_reason": "evidence_sufficient"
}
```

约束：

- `summary` 必须短
- `findings` 必须结构化
- `evidence` 必须可审计
- `proposed_actions` 只是 proposal，不是执行结果

---

## Root 结果汇总设计

Root Supervisor 不直接拼接多个子结论，而是维护一个 `EvidenceBoard`。

`EvidenceBoard` 聚合：

- root 自己收集到的 observations
- 每个 subagent 的 `summary`
- 每个 subagent 的 `findings`
- 每个 subagent 的关键 `evidence`
- 每个 subagent 的 `proposed_actions`

Root 在汇总后有四种下一步：

1. 证据已充分，直接输出结论
2. 证据冲突，再派生第二波更窄的 subagent
3. 关键信息缺失，触发 clarification
4. 已形成动作方案，进入 approval / execute

### 为什么不允许子 agent 互聊

第一版建议禁止 mesh-style agent 通信。

原因：

- 状态空间会迅速变复杂
- 子 agent 之间相互污染上下文
- 审计链条难以复原
- approval 与恢复路径会变得混乱

如果某个子 agent 的结果确实需要被另一个子 agent 使用，应由 root 提炼后作为 follow-up 再投递。

---

## 审批与执行边界

这是整个设计里最重要的收口点。

### 规则

- subagent 默认只允许只读工具
- subagent 可以提出 `proposed_actions`
- subagent 不能直接执行 mutating action
- subagent 不能直接发起独立审批
- 所有审批都只能由 root session 统一进入 `approval_gate`

### 原因

- 避免出现“子 agent 审批通过，但 root 仍不知情”的状态分裂
- 避免双层审批流
- 保持当前 `approval_store / interrupt_store / execution_store` 的一致边界

### 后续扩展

如果后续确实需要让某些 subagent 具备有限执行权，也必须满足：

- 只允许低风险动作
- 仍然经统一 `ToolExecutionMiddleware`
- 仍然把执行结果回流到 root 再决定对外输出

---

## 生命周期与状态机

### Agent 状态

- `queued`
- `running`
- `completed`
- `failed`
- `timed_out`
- `cancelled`
- `closed`

### 典型流程

```text
root decides delegation
  -> spawn subagents
  -> subagents running
  -> wait for mailbox / timeout
  -> partial or full results returned
  -> root aggregate / rank
  -> close remaining idle agents
  -> continue root loop
```

### 失败处理原则

- 单个 subagent 失败不应让 root 直接失败
- `timed_out / failed` 本身也算一条结构化结果
- Root 应能识别“哪些分支失败、哪些分支已给出证据”

---

## 存储设计

建议新增 root-scoped subagent 记录，而不是把 subagent 混入用户对话 session。

### 新增模型建议

#### `analysis_agent_thread`

- `agent_thread_id`
- `root_session_id`
- `parent_turn_id`
- `task_name`
- `role`
- `status`
- `fork_mode`
- `allowed_tools`
- `started_at`
- `finished_at`
- `timeout_ms`
- `metadata`

#### `analysis_agent_result`

- `agent_thread_id`
- `summary`
- `findings`
- `evidence`
- `recommended_next_steps`
- `proposed_actions`
- `tool_calls_used`
- `stop_reason`
- `created_at`

#### `analysis_agent_edge`

- `root_session_id`
- `parent_agent_thread_id`
- `child_agent_thread_id`
- `relationship`

### 与现有存储的关系

- `session_store` 继续管理用户会话
- `interrupt_store` 继续管理 clarification / approval / feedback
- `execution_store` 继续记录 root execution plan
- `system_event_store` 增加 subagent 生命周期事件

首版不要求为 subagent 单独做完整 session 对外 API，但必须具备内部可追踪能力。

---

## 可观测性设计

Subagent 一旦引入，当前项目现有的最小可观测性是不够的。

必须新增：

- `subagent.spawned`
- `subagent.started`
- `subagent.completed`
- `subagent.failed`
- `subagent.timed_out`
- `subagent.closed`
- `subagent.result_promoted`

Tracing 至少要覆盖：

- root delegation 决策
- 每个 subagent 的执行 span
- join / aggregate 阶段

Metrics 至少要覆盖：

- 每轮平均 subagent 数
- subagent completion rate
- subagent timeout rate
- average fan-out width
- second-wave delegation rate

---

## 图与运行时集成方式

### 推荐做法

继续保持当前 graph 稳定，只改 `supervisor_loop` 内部能力。

也就是说：

- `ReactSupervisor` 增加协作工具可见性
- `ToolExecutionMiddleware` 旁边新增 `DelegationPolicy` 或 `AgentControl`
- subagent 自己也运行简化版 `ReactSupervisor`
- 但 subagent 的配置更严格，工具范围更窄，不能递归 delegation

### 不推荐做法

不建议新增固定节点：

- `fanout`
- `parallel_subagents`
- `join`
- `rank_subagents`

原因：

- 会让当前 graph 再次变重
- 会重新走回固定流水线
- 与当前 `tool-first ReAct` 方向冲突

---

## 模块拆分建议

建议新增：

- `src/it_ticket_agent/multi_agent/models.py`
- `src/it_ticket_agent/multi_agent/registry.py`
- `src/it_ticket_agent/multi_agent/control.py`
- `src/it_ticket_agent/multi_agent/context_builder.py`
- `src/it_ticket_agent/multi_agent/result_merger.py`
- `src/it_ticket_agent/multi_agent/policy.py`
- `src/it_ticket_agent/tools/collab.py`

建议改造：

- `src/it_ticket_agent/runtime/react_supervisor.py`
- `src/it_ticket_agent/runtime/orchestrator.py`
- `src/it_ticket_agent/execution/tool_middleware.py`
- `src/it_ticket_agent/system_event_store.py`
- `src/it_ticket_agent/state/models.py`

---

## 分阶段实施建议

### Phase 0：控制面与模型

- 新增 `analysis_agent_thread` / `result` / `edge`
- 新增 root-scoped `AgentRegistry`
- 新增 `AgentControl`

### Phase 1：单 subagent 受控派生

- 暴露 `spawn_analysis_agents`
- 先支持 1 个 read-only subagent
- 打通 `spawn -> wait -> summary return`

### Phase 2：并行 fan-out

- 支持 2 到 3 个并行 subagent
- 增加 `wait_analysis_agents`
- 增加 `close_analysis_agent`

### Phase 3：上下文裁剪与角色化

- 引入 `SubagentContextBuilder`
- 引入 `LastNTurns + ContextBundle`
- 加入角色与工具绑定

### Phase 4：汇总与第二波派生

- 增加 `EvidenceBoard`
- 支持 second-wave delegation
- 支持 follow-up message

### Phase 5：完整 observability 与回归测试

- metrics / tracing / health signal
- failure / timeout / cancel / recovery 测试

---

## 测试清单

### 单元测试

- `DelegationPolicy` 是否正确拦截不必要的派生
- `ContextBuilder` 是否正确裁剪上下文
- `ResultMerger` 是否正确聚合证据
- `AgentRegistry` 是否正确管理并发上限

### 集成测试

- Root 派生两个只读 subagent 并成功汇总
- 一个 subagent 失败时，另一个结果仍可被 root 使用
- subagent timeout 后 root 能正常继续
- subagent 提出高风险 proposal，但真正审批只发生在 root

### 回归测试

- 简单 FAQ 不应触发 subagent
- 简单单域问题不应触发 subagent
- approval / feedback / resume 在启用 subagent 后不回归
- root session 重启后仍能恢复未结束的 subagent 元数据

---

## 风险与取舍

### 风险

- 如果把 subagent 做成固定 graph 节点，系统会再次变重
- 如果给 subagent 完整上下文，token 成本和污染都会快速上升
- 如果允许 subagent 自己审批或执行，恢复链路会非常复杂
- 如果允许 subagent 互聊，调试和回放难度会成倍增加

### 当前取舍

- 先做 root-controlled delegation
- 先做 read-only analysis subagent
- 先禁用递归派生
- 先做 parent-mediated communication
- 先用结构化结果回收，不做自由对话式聚合

---

## 最终推荐落地口径

如果当前项目要实现 subagent，建议对外统一表述为：

> 在保持 `tool-first ReAct` 主线不变的前提下，为 root Supervisor 增加受控的多 subagent 并行分析能力。subagent 只负责局部问题面的独立分析，具备隔离上下文、独立 budget 和结构化结果输出；所有审批、执行和最终回复仍统一收敛在 root session。

这个表述能够同时满足：

- 与当前迁移方向一致
- 与现有 runtime 兼容
- 与 `codex` 的协作模型一致
- 为后续更强的多 Agent 能力预留接口，但不提前引入过度复杂度
