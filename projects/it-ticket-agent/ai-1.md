# AI-1 工作页（状态与契约组）

## 角色定位

- 角色：AI-1（状态与契约组）
- 目标：冻结新架构下的核心状态模型与公共契约，给其他工作流提供稳定依赖。
- 主要负责目录：`src/it_ticket_agent/state/`、`src/it_ticket_agent/adapters/`
- 可少量修改：`src/it_ticket_agent/rag_client.py`、`src/it_ticket_agent/runtime/contracts.py`
- 尽量不碰：`src/it_ticket_agent/main.py`、`src/it_ticket_agent/runtime/orchestrator.py`、`src/it_ticket_agent/static/app.js`

## 总任务

### 第一轮任务
- 新建 `state/incident_state.py`
- 新建 `state/models.py`
- 新建 `state/transformers.py`
- 新建 `adapters/rag_adapter.py`
- 统一 `hits/context` 输出

### 契约对象
- `IncidentState`
- `SubAgentResult`
- `ApprovalProposal`
- `ApprovedAction`
- `ExecutionResult`
- `VerificationPlan`
- `VerificationResult`

## 当前进度

### 已完成
- 已阅读 `并行分工方案.md` 与 `重构计划.md`，确认 AI-1 边界与第一轮任务。
- 已检查当前代码结构，确认 `state/` 与 `adapters/` 目录尚未落地。
- 已检查 `rag_client.py`，确认当前仍为远端返回透传，尚无 `hits/context` 统一适配。
- 已检查 `runtime/contracts.py`，确认当前仍以旧 `AgentResult` 为主。
- 已初始化 `共享进度.md` 记录块。
- 已新增 `src/it_ticket_agent/state/models.py`，落地核心状态与契约对象。
- 已新增 `src/it_ticket_agent/state/incident_state.py`，落地统一 `IncidentState`。
- 已新增 `src/it_ticket_agent/state/transformers.py`，落地旧 `AgentResult` 转换层和示例 payload。
- 已新增 `src/it_ticket_agent/adapters/rag_adapter.py`，统一 `hits/context` 归一化。
- 已修改 `src/it_ticket_agent/rag_client.py`，统一 `search()` 返回标准化 payload，并兼容旧 `context` 读取。
- 已新增 `src/it_ticket_agent/state/__init__.py`、`src/it_ticket_agent/adapters/__init__.py` 作为导出入口。
- 已完成最小验证：`PYTHONPYCACHEPREFIX=/tmp/it-ticket-agent-pyc python3 -m compileall src` 与 `uv run python` 导入检查通过。
- 已新增 `src/it_ticket_agent/state/approval_transformers.py`，补齐状态契约与审批领域模型之间的跨组映射。
- 已提供 `build_approval_gate_input_from_state()`，可从 `IncidentState` 直接构造 approval gate 输入。
- 已提供 `apply_approval_gate_result_to_state()`、`apply_approved_actions_to_state()`、`apply_execution_results_to_state()`，可直接把审批结果与执行结果写回 `IncidentState`。
- 已补审批对接样例 `approval_gate_input` / `approval_gate_result` / `approval_resume_writeback`。
- 已与 AI-3 最新 `approval/adapters.py` / `approval.models.py` 对齐，校准 `metadata`、`created_at`、`approved_by`、`approved_at`、`comment` 的跨组透传。
- 已完成退回修复：解决 `state/approval_transformers.py` 与 `approval/__init__.py` 的循环导入，保证 `import it_ticket_agent.state` 可直接成功。
- 已新增 `apply_approval_resume_result_to_state()`，为 AI-2 提供“审批决议 -> IncidentState”整对象写回 helper。
- 已完成验收：`uv run python -c "import it_ticket_agent.state"` 成功，原有三个 helper 与新 resume helper 均可用。

### 进行中
- 当前 LD 指派任务已完成；后续仅等待 AI-4 执行器结果结构进一步稳定，再按需增强 `execution_result_to_state()` 的兼容逻辑。

### 待完成
- 如下游接入发现缺口，再增补字段或补充更细的 transformer。
- 如 Integrator 需要最小 HTTP 层映射说明，再补充响应渲染建议。

## 当前输出文件

- `src/it_ticket_agent/state/models.py`
- `src/it_ticket_agent/state/incident_state.py`
- `src/it_ticket_agent/state/transformers.py`
- `src/it_ticket_agent/state/approval_transformers.py`
- `src/it_ticket_agent/state/__init__.py`
- `src/it_ticket_agent/adapters/rag_adapter.py`
- `src/it_ticket_agent/adapters/__init__.py`
- `src/it_ticket_agent/rag_client.py`

## 字段说明（首版冻结）

### `IncidentState`
- 工单基础字段：`ticket_id`、`user_id`、`message`、`thread_id`、`service`、`cluster`、`namespace`、`channel`
- 流程字段：`status`、`routing`、`shared_context`
- 分析字段：`rag_context`、`subagent_results`、`approval_proposals`
- 执行闭环字段：`approved_actions`、`execution_results`、`verification_plan`、`verification_results`
- 汇总字段：`final_summary`、`final_message`、`open_questions`、`metadata`

### `SubAgentResult`
- 保留旧 `AgentResult` 的核心分析信息：`agent_name`、`domain`、`status`、`summary`、`findings`、`evidence`、`tool_results`、`risk_level`、`confidence`
- 不再直接把 `recommended_actions` 暴露给新流程；统一映射为 `approval_proposals`

### `ApprovalProposal`
- 核心字段：`proposal_id`、`source_agent`、`action`、`risk`、`reason`、`params`
- 控制字段：`requires_approval`
- 辅助字段：`title`、`target`、`evidence`、`metadata`

### `RAGContextBundle`
- 标准字段：`hits`
- 兼容字段：`context`
- 其他字段：`query`、`query_type`、`should_respond_directly`、`direct_answer`、`citations`、`facts`、`index_info`、`raw_response`

## 示例 payload

- 代码入口：`src/it_ticket_agent/state/transformers.py` 的 `example_payloads()`
- 当前提供：`initial_incident_state`、`analyzed_incident_state`、`verified_incident_state`

## 对下游的接线建议

- AI-2：graph state 直接依赖 `IncidentState`
- AI-3：审批输入直接依赖 `ApprovalProposal`
- AI-4：执行与验证结果直接依赖 `ApprovedAction`、`ExecutionResult`、`VerificationResult`
- AI-5：旧 agent 若仍输出 `AgentResult`，统一通过 `subagent_result_from_agent_result()` 接入
- AI-2 接 approval gate 时，优先使用 `build_approval_gate_input_from_state()` 与 `apply_approval_gate_result_to_state()`，不要在 graph 节点里手写字段搬运
- AI-2 做审批 resume 时，优先使用 `apply_approval_resume_result_to_state()`，不要只写局部 dict 回填统一状态
- AI-4 写回执行结果时，优先使用 `apply_approved_actions_to_state()`、`apply_execution_results_to_state()`

## 审批对接补充

- 代码入口：`src/it_ticket_agent/state/approval_transformers.py`
- 跨组映射：
  - `state_approval_proposal_to_domain()`
  - `domain_approval_proposal_to_state()`
  - `domain_approved_action_to_state()`
  - `approval_resume_result_to_state_actions()`
- 写回 helper：
  - `build_approval_gate_input_from_state()`
  - `apply_approval_gate_result_to_state()`
  - `apply_approval_resume_result_to_state()`
  - `apply_approved_actions_to_state()`
  - `apply_execution_results_to_state()`
- 样例入口：`approval_example_payloads()`

## 续做指南

- 若中断后恢复，先看 `共享进度.md` 最新 AI-1 进度块，再回看本文件“当前进度”。
- 接下来优先做“新增文件”，避免提前改高冲突入口文件。
- 每完成一个独立子块后：
  - 在 `共享进度.md` 追加一个新进度块
  - 在本文件同步更新“已完成 / 进行中 / 待完成”
  - 标明涉及文件、未决问题、对 Integrator 的交接说明

## 当前判断

- 应先冻结数据契约，再让 AI-2 接 LangGraph、AI-3 接审批、AI-4 接执行验证。
- `AgentResult` 现在应视为兼容层，而不是最终核心状态。
- RAG 返回兼容应收敛到 adapter 层，减少上层分支判断。
- `hits` 已作为统一标准字段保留，`context` 仅作为兼容别名继续输出。

## LD 最新要求（2026-03-28）

### 本轮只做这些
- 先交 `state/` 与 `adapters/` 的最小可 import 骨架，再补 transformer 与示例 payload。
- 优先统一 `hits/context` 契约；如果需要兼容旧返回，必须收敛在 adapter 层，不能把分支判断散落到 agent / tool / graph。
- 保持 `runtime/contracts.py` 中旧 `AgentResult` 可继续工作，新结构先作为上层可依赖的新契约。

### 本轮不要做这些
- 不改 `src/it_ticket_agent/main.py`。
- 不改 `src/it_ticket_agent/runtime/orchestrator.py`。
- 不直接修改审批流或 graph 节点。

### 交付门禁
- 必须给出 `IncidentState`、`ApprovalProposal` 等核心对象的字段说明。
- 必须至少给出 2 个状态/DTO 示例 payload，供 AI-2、AI-3 对接。
- 如果触碰 `rag_client.py`，要显式说明“上层统一读取哪个字段、旧字段如何兼容”。

## LD 任务区（待指派）

### 当前状态
- 暂无新的 LD 指派任务
- 当前包级导入已稳定，`it_ticket_agent.state` 可直接作为公共入口被其他组依赖。

### 下一条任务预留区
- 任务日期：待填写
- 任务目标：待填写
- 必须交付：待填写
- 不要做：待填写
- 交付标准：待填写

## 已归档的 LD 任务

### [已完成][2026-03-29] 审批跨组转换层与状态写回收口
- 任务目标：收口“状态契约 ↔ 审批领域模型 ↔ graph 写回”之间的最后一层公共契约，减少 AI-2 / AI-3 接线摩擦。
- 交付内容：
  - 新增 `src/it_ticket_agent/state/approval_transformers.py`
  - 补齐 `ApprovalProposal` / `ApprovedAction` / `ExecutionResult` 跨组转换
  - 补齐 `apply_approval_gate_result_to_state()` / `apply_approved_actions_to_state()` / `apply_execution_results_to_state()`
  - 补齐 `approval_gate_input` / `approval_gate_result` / `approval_resume_writeback` 样例
- 约束要求：未改 `src/it_ticket_agent/runtime/orchestrator.py`、未改 `src/it_ticket_agent/graph/`、未直接进入 `src/it_ticket_agent/approval/` 修改 AI-3 领域实现
- 完成状态：已完成并已验证
