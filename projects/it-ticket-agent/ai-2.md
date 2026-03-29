# AI-2 工作页（LangGraph 主流程组）

## 角色定位

- 角色：AI-2（LangGraph 主流程组）
- 目标：把当前 `SupervisorOrchestrator` 重构为 LangGraph 最小闭环，同时保持现有 HTTP API 兼容。
- 主要负责目录：`src/it_ticket_agent/graph/`
- 可修改：`src/it_ticket_agent/runtime/orchestrator.py`、graph 相关适配层
- 尽量不碰：`src/it_ticket_agent/main.py`、`src/it_ticket_agent/static/app.js`
- 不负责：审批存储领域设计、领域 agent 业务实现、AI-4 的正式执行/验证闭环

## 当前结论

- 当前 LD 给 AI-2 的这一轮任务已经完成。
- graph 主链路、approval gate、resume 接线、`IncidentState` 内部统一状态接线均已落地。
- 目前 AI-2 不再阻塞于 AI-3；后续主要依赖 AI-4 替换过渡执行节点。
- 当前 `execute_approved_action_transition` 仍为过渡层，后续不应继续扩写，而应由 AI-4 / Integrator 收口。

## 当前产物

- `src/it_ticket_agent/graph/__init__.py`
- `src/it_ticket_agent/graph/builder.py`
- `src/it_ticket_agent/graph/nodes.py`
- `src/it_ticket_agent/graph/state.py`
- `src/it_ticket_agent/runtime/orchestrator.py`

## 当前状态

### 已完成
- 已阅读 `并行分工方案.md` 并确认 AI-2 职责边界。
- 已建立共享进度记录机制与 `ai-2.md` 工作页。
- 已新增 `graph/` 骨架，落地 ticket graph 与 approval resume graph。
- 已将 `runtime/orchestrator.py` 收口为 facade，并改为委托 graph。
- 已接入 AI-1 的 `IncidentState` / transformer，graph 内部不再依赖散落的旧字段搬运。
- 已补 `diagnosis.incident_state` 视图，便于观察 graph 内部统一状态。
- 已将 `approval_gate` 切到 AI-3 的 `ApprovalCoordinator.build_gate_result()`。
- 已将 resume 路径切到 AI-3 的 `build_resume_result()` 与 decision adapter。
- 已输出 `diagnosis.incident_state_update` 补丁，当前可承接 `approved_actions`、`execution_results`。
- 已完成静态校验与多轮最小烟测，确认返回结构兼容、approval gate 可处理多 proposal 场景中的“待审 + 自动通过”分流。

### 进行中
- 当前无进行中的 LD 指派任务。

### 待完成
- 等 AI-4 提供正式执行/验证接口后，替换 `execute_approved_action_transition`。
- 如 Integrator 后续引入状态持久层，将 `diagnosis.incident_state_update` 落为正式写回补丁。
- 如 LD 后续要求，可补 graph 自动化测试，覆盖多 proposal gate / resume / transition 场景。

## 节点边界

- `ingest`：只做 graph 入口标准化，不做业务决策。
- `supervisor_route`：只负责路由与构造 `TaskEnvelope`。
- `domain_agent`：只负责调用目标 agent 并回填结构化结果。
- `approval_gate`：只负责调用 `ApprovalCoordinator` 做 collect / dedupe / policy / gate，并产出兼容审批请求。
- `finalize`：只负责把 graph state 收口为兼容的 HTTP 返回。
- `approval_decision`：只负责把旧审批决议适配到 AI-3 domain decision，并产出 resume 结果与状态补丁。
- `execute_approved_action_transition`：仅为兼容当前 API 的过渡执行节点，后续交 AI-4 替换。
- `finalize_approval_decision`：只负责把审批链路结果收口为兼容返回，并附带 `incident_state_update` / graph notes。

## 对外依赖

- 已接入 AI-1 提供的统一状态模型与 transformer。
- 已接入 AI-3 的审批 adapter / coordinator / facade。
- 当前主要后续依赖：AI-4 的正式执行/验证节点接口。

## 验证记录

- `py_compile` 已通过：`graph/`、`runtime/orchestrator.py`、审批接线相关文件可正常导入。
- 最小运行验证已通过：`handle_ticket()` 返回兼容结构。
- 拒绝审批路径已验证：`handle_approval_decision()` 可通过 resume graph 正常返回。
- 已验证 `handle_ticket()` 的 `diagnosis` 中包含 `incident_state` 且状态正确。
- 已验证 `approval_gate` 可同时处理“高风险待审 + 低风险自动通过”。
- 已验证 approval resume 可生成 `incident_state_update.approved_actions`。
- 当前验证中出现过外部 LLM DNS 失败，但已按现有 fallback 逻辑回退，不影响 graph 主流程闭环。

## LD 任务归档（2026-03-29，已完成）

### 任务目标
- 在 `src/it_ticket_agent/graph/` 内完成 graph builder、nodes、state adapter 占位与 resume 占位。
- 将 `runtime/orchestrator.py` 收口为 facade，不扩散审批策略、执行策略或领域推理逻辑。
- 在 AI-1 / AI-3 契约稳定后，把 approval gate 与 resume 正式接到统一状态与审批协调器接口。

### 完成情况
- [x] 新建 `src/it_ticket_agent/graph/` 骨架，完成 `builder.py`、`nodes.py`、`state.py`、`__init__.py`
- [x] 将 `runtime/orchestrator.py` 改为 facade，并委托 ticket graph / approval graph
- [x] 接入 AI-1 `IncidentState` 与 transformer，统一 graph 内部状态表达
- [x] 补 `diagnosis.incident_state` 视图
- [x] 接入 AI-3 `ApprovalCoordinator` / adapter / facade
- [x] 将 approval gate 切到 `build_gate_result()`
- [x] 将 resume 切到 `build_resume_result()`
- [x] 输出 `diagnosis.incident_state_update` 补丁，承接审批后的状态写回结构
- [x] 完成静态校验与最小烟测

### 归档说明
- 本轮 LD 指派给 AI-2 的任务已全部完成。
- 与本轮任务相关的实现已落在 `graph/`、`runtime/orchestrator.py`、`共享进度.md` 中。
- 后续继续推进时，应从新的任务区接续，不再把本轮任务视为进行中。


## LD 追加任务归档（2026-03-29，已完成）

### 任务目标
- 将审批 resume 从“返回 incident_state_update 字典”升级为“真正写回完整 IncidentState”。
- 保持 `runtime/orchestrator.py` 继续只做 facade。
- 不新增第二套审批/执行状态结构。

### 完成情况
- [x] 已修改 `src/it_ticket_agent/graph/state.py`，让 `ApprovalGraphState` 持有完整 `incident_state`
- [x] 已修改 `src/it_ticket_agent/graph/nodes.py`，在 `ingest_approval_decision` 恢复或初始化可写回 `IncidentState`
- [x] 已在 `approval_decision` / `execute_approved_action_transition` / `finalize_approval_decision` 中直接使用 AI-1 helper 写回完整状态
- [x] 已保证最终审批响应中的 `diagnosis` 带完整 `incident_state`，不再只返回局部 update dict
- [x] 已保持 `runtime/orchestrator.py` 继续只做 facade，未扩逻辑
- [x] 已在多 proposal + 过渡执行器场景下给出清晰限制：只执行首个 proposal，其余以现有 `ExecutionResult(status="skipped")` 明示

### 验收结果
- [x] 普通工单：仍返回兼容结构
- [x] 进入审批：`diagnosis.incident_state` 可见完整状态
- [x] 拒绝审批：响应里带完整 `incident_state`
- [x] 批准审批：响应里带完整 `incident_state`，且包含 `approved_actions`、`execution_results`、`metadata.approval_decision`
- [x] 未破坏现有 HTTP API 返回结构

### 归档说明
- 本轮追加任务已完成。
- 当前审批 resume 链路已经从“局部状态补丁”升级为“完整 IncidentState 写回”。
- 后续 AI-2 的主要剩余工作不在本轮，而是等待 AI-4 替换过渡执行节点。

## LD 当前任务区（待新任务）

### 状态
- 当前无新的 LD 指派任务。

### 预留位
- 任务目标：待补充
- 必交项：待补充
- 不要做：待补充
- 交付标准：待补充

## 续做入口

1. 先看 `共享进度.md` 最新进度块。
2. 再看本文件的“LD 当前任务区（待新任务）”。
3. 如果新的任务涉及执行/验证闭环，优先确认 AI-4 是否已冻结接口。
4. 每完成一个独立子块，先更新 `共享进度.md`，再回写本文件。
