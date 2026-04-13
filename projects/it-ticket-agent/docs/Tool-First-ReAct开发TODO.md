# Tool-First ReAct 开发 TODO

> 对应方案：`docs/Tool-First-ReAct迁移方案.md`

## Phase 1：ReAct Supervisor + 新 Graph

- [ ] 新建 `runtime/react_supervisor.py`
- [ ] 定义 Supervisor state：`iterations/tool_calls/confidence/stop_reason`
- [ ] 新建 `graph/react_state.py`
- [ ] 新建 `graph/react_nodes.py`
- [ ] 新建 `graph/react_builder.py`
- [ ] 实现 `light_router` 节点
- [ ] 实现 `supervisor_loop` 节点
- [ ] 实现 `approval_gate` 节点
- [ ] 实现 `execute_tool` 节点
- [ ] 实现 `await_user` 节点
- [ ] 实现 `finalize` 节点
- [ ] 增加 `orchestration_mode` 配置
- [ ] 保留旧 graph，并实现新旧 graph 切换入口
- [ ] 打通 FAQ / SOP fast path
- [ ] 打通诊断路径进入 `supervisor_loop`

## Phase 2：ToolExecutionMiddleware + 风险控制

- [ ] 给 `BaseTool` 增加 `risk_level`
- [ ] 给 `BaseTool` 增加 `retryable`
- [ ] 给 `BaseTool` 增加 `timeout_sec`
- [ ] 梳理现有 tool 的 `risk_level`
- [ ] 新建 `execution/tool_middleware.py`
- [ ] 实现 tool 注册检查
- [ ] 实现高风险 tool 审批拦截
- [ ] 复用现有 approval / execution binding 能力
- [ ] 保留 ApprovalPolicy 兜底校验
- [ ] 明确 `risk_level >= high` 的工具不直接执行

## Phase 3：Supervisor 护栏 + 上下文窗口管理

- [ ] 增加 `max_iterations`
- [ ] 增加 `max_tool_calls`
- [ ] 增加 `confidence_threshold`
- [ ] 增加 `stop_reason`
- [ ] 增加 `max_parallel_branches`
- [ ] 为每轮 observation 建立统一账本结构
- [ ] 新增 observation summary 逻辑
- [ ] 新增 `working_memory_summary`
- [ ] 新增 `pinned_findings`
- [ ] 增加 `max_context_tokens`
- [ ] 增加上下文超限裁剪策略

## Phase 4：Tool 超时 / 重试 + 结果标准化

- [ ] 定义统一 `ToolExecutionEnvelope`
- [ ] 实现 tool timeout
- [ ] 实现 retryable tool 重试策略
- [ ] 实现 retry count 记录
- [ ] 为 tool 结果补充 `latency_ms`
- [ ] 为失败结果补充 `error_type`
- [ ] 确保 tool 失败返回结构化错误，而不是直接抛异常炸穿 supervisor
- [ ] 不引入硬编码 fallback 链
- [ ] 让 Supervisor 基于失败结果自行换 tool / ask user / stop

## Phase 5：删除旧 Skill 体系

- [ ] 删除 `skills/` 目录
- [ ] 删除 `SkillCategory`
- [ ] 删除 `SkillSignature`
- [ ] 删除 `SkillResult`
- [ ] 删除旧 skill registry / skill executor 初始化
- [ ] 删除旧固定诊断 graph 节点接线
- [ ] 保留并复用 `context_collector / hypothesis_generator / ranker` 作为内部能力
- [ ] 清理 README / docs 中对 skill 架构的描述

## 验收

- [ ] FAQ 请求不进入诊断循环
- [ ] 普通诊断请求能走完整 ReAct Supervisor 循环
- [ ] 高风险 tool 能稳定进入审批链
- [ ] Supervisor 不会无限循环
- [ ] 多轮 observation 不会导致上下文无限膨胀
- [ ] tool timeout / retry / structured error 生效
- [ ] 新旧链路可配置切换

