# Tool-First ReAct 开发 TODO

> 对应方案：`docs/Tool-First-ReAct迁移方案.md`

## Phase 1 当前状态

已完成的骨架：

- [x] 新增 `react_supervisor.py`
- [x] 新增 `react_state.py`
- [x] 新增 `react_nodes.py`
- [x] 新增 `react_builder.py`
- [x] 新增 `orchestration_mode`
- [x] 新 graph 与旧 graph 并存
- [x] `light_router -> direct_answer -> finalize`
- [x] `supervisor_loop` 已具备 tool-first ReAct 雏形
- [x] 普通 Tool 在 `supervisor_loop` 内部执行

当前剩余项：

- [x] `confidence_threshold` 已接入 `ReactSupervisor` 的终止判断
- [ ] `await_user` 的完整恢复闭环仍主要复用旧逻辑
- [ ] 真实 LLM 端到端联调未完成
- [x] 已接入最小版 `ToolExecutionMiddleware` 到 `supervisor_loop` 执行链路
- [x] `execute_approved_action` 已接到统一 action middleware 入口

## Phase 2 当前状态

已完成的最小版：

- [x] `BaseTool` 最小元数据：`risk_level / retryable / timeout_sec`
- [x] 新增 `ToolExecutionMiddleware`
- [x] 普通 tool 已接入 middleware
- [x] action 执行已接入统一 action middleware 入口
- [x] tool / action 执行都已具备最小版 timeout / retry / structured error envelope
- [x] action registry 已从 `execution/security.py` 独立拆分

当前剩余项：

- [ ] 还没有把更多 tool 的 `risk_level` 系统性梳理完整
- [ ] action 执行仍是最小封装，尚未完全统一到更完整的 execution envelope
- [ ] timeout / retry / structured error 仍属于最小版，还没做成最终形态

## Phase 1：ReAct Supervisor + 7 节点新 Graph

- [ ] 新建 `runtime/react_supervisor.py`
- [ ] 定义 Supervisor state：`iterations/tool_calls/confidence/stop_reason`
- [ ] 新建 `graph/react_state.py`
- [ ] 新建 `graph/react_nodes.py`
- [ ] 新建 `graph/react_builder.py`
- [ ] 实现 `light_router` 节点
- [ ] 实现 `direct_answer` 节点
- [ ] 实现 `supervisor_loop` 节点
- [ ] 实现 `approval_gate` 节点
- [ ] 实现 `await_user` 节点
- [ ] 实现 `execute_approved_action` 节点
- [ ] 实现 `finalize` 节点
- [ ] 增加 `orchestration_mode` 配置
- [ ] 保留旧 graph，并实现新旧 graph 切换入口
- [ ] 打通 FAQ / SOP fast path：`light_router -> direct_answer -> finalize`
- [ ] 打通诊断路径进入 `supervisor_loop`
- [ ] 明确普通 Tool 在 `supervisor_loop` 内部执行，不通过独立 graph 节点
- [ ] 实现 `await_user` 恢复后的条件路由：clarification → `supervisor_loop` / approval → `execute_approved_action` / feedback → `finalize`

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
- [ ] 新增 `summary_after_n_steps`
- [ ] 新增 `pinned_findings`
- [ ] 增加 `max_context_tokens`
- [ ] 增加 observation 摘要化策略
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

