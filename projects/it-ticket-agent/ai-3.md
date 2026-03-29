# AI-3 工作页：审批协调组

## 角色定位

- 负责把当前简单审批升级成真正的 `ApprovalCoordinator`。
- 不负责 LangGraph 主流程，不负责 agent 推理本身，不负责前端接线。
- 主工作目录：`src/it_ticket_agent/approval/`

## 当前结论

- 当前审批逻辑仍耦合在 `src/it_ticket_agent/runtime/orchestrator.py`：从 `recommended_actions` 中挑第一个高风险动作后直接创建审批单。
- 当前旧 HTTP / 图层 DTO 仍以 `schemas.py` 中的 `ApprovalPayload` / `ApprovalDecisionRequest` 为主，但 AI-3 已补齐 adapter 和 facade，可平滑过渡到新领域模型。
- `src/it_ticket_agent/approval_store.py` 现已收口为 v2 facade，旧接口仍可用，但状态机约束、审计、重复审批保护由 `ApprovalStoreV2` 提供。
- AI-3 的新审批能力现在已经可以被 graph 直接接入；旧兼容层仅用于过渡，不应再扩写成长期主模型。

## 已完成

### Block 1：领域模型与接口骨架
- [x] 新建 `src/it_ticket_agent/approval/models.py`
- [x] 新建 `src/it_ticket_agent/approval/coordinator.py`
- [x] 新建 `src/it_ticket_agent/approval/policy.py`
- [x] 新建 `src/it_ticket_agent/approval/store.py`
- [x] 新建 `src/it_ticket_agent/approval/__init__.py`
- [x] 定义 proposal / request / decision / policy result / approved action 基础字段
- [x] 实现 `ApprovalCoordinator.build_gate_result()` 与 `build_resume_result()` 初版
- [x] 实现 `ApprovalStoreV2` 初版，包含状态流转约束、审计事件、重复审批保护
- [x] 完成轻量烟测：proposal 去重、审批单创建、决议记录、重复审批保护

### Block 2：兼容层与 DTO 收口
- [x] 新建 `src/it_ticket_agent/approval/adapters.py`
- [x] 实现 AI-1 state `ApprovalProposal` -> AI-3 domain `ApprovalProposal`
- [x] 实现旧 `ApprovalPayload` -> 新 `ApprovalRequest`
- [x] 实现旧 `ApprovalDecisionRequest` / decision dict -> `ApprovalDecisionRecord`
- [x] 实现新 `ApprovedAction` -> AI-1 state `ApprovedAction`
- [x] 保持 `schemas.py` 不动，通过 adapter 解决兼容问题

### Block 3：旧 store 兼容策略
- [x] 将 `src/it_ticket_agent/approval_store.py` 收敛为 facade
- [x] 保留旧接口 `create/get/decide`
- [x] 内部委托 `ApprovalStoreV2`
- [x] 保留旧 payload 字段 `action/risk/reason/params/status/approver_id/comment`
- [x] 保持状态机约束、重复审批保护、审计记录不回退
- [x] 完成 facade 烟测

## 职责范围

### 我负责
- approval 领域模型
- proposal collect / dedupe / merge / policy check
- 升级 SQLite approval store
- 统一审批 payload
- resume 后 decision 结构
- `ApprovalCoordinator` 的对外接口

### 我尽量不碰
- `src/it_ticket_agent/runtime/supervisor.py`
- `src/it_ticket_agent/agents/`
- `src/it_ticket_agent/static/app.js`

### 谨慎修改
- `src/it_ticket_agent/schemas.py`
- `src/it_ticket_agent/approval_store.py`

## 当前产物说明

### `src/it_ticket_agent/approval/models.py`
- 提供 `ApprovalProposal`、`ApprovalRequest`、`ApprovalDecisionRecord`、`ApprovedAction`、`ApprovalGateInput`、`ApprovalGateResult` 等领域对象。
- 已补 `metadata`、`approver_id/comment/decided_at`、`approved_by/approved_at`，方便兼容层与 resume 结构承接。
- `ApprovalProposal` 提供 `dedupe_key`，用于同动作同资源同参数的去重。

### `src/it_ticket_agent/approval/policy.py`
- 当前策略规则较轻：
  - `high/critical` 默认需要审批
  - `auto_approve=true` 可自动放行
  - `policy_blocked=true` 可直接拒绝
  - `rollback/delete/restart/scale/drain` 这类动作会至少提升到 `high`
- 这是骨架规则，后续可继续外扩。

### `src/it_ticket_agent/approval/coordinator.py`
- `build_gate_result()`：graph/approval gate 的主要入口。
- `build_resume_result()`：resume 后将审批决议转换为 `approved_actions` 或 `rejected_proposals`。
- 当前已支持 collect / dedupe / merge / policy evaluate / approval request create。
- resume 后生成的 `ApprovedAction` 已带 `approved_by/approved_at/comment`。

### `src/it_ticket_agent/approval/adapters.py`
- 负责 AI-1 state / 旧 DTO / 新领域模型之间的转换。
- 当前是兼容层的唯一入口，优先在此做协议映射，不直接把旧字段扩散进 domain 层。

### `src/it_ticket_agent/approval/store.py`
- 提供 `ApprovalStoreV2`。
- 新表设计为 `approval_request_v2` + `approval_audit_event`。
- 已补上 `pending -> approved/rejected` 状态机约束。
- 已补上审计事件记录与重复审批保护。

### `src/it_ticket_agent/approval_store.py`
- 现为 facade，不再自己维护旧审批表逻辑。
- 对外继续暴露 `create/get/decide`，对内通过 adapter + `ApprovalStoreV2` 完成过渡。
- 多 proposal 请求在旧兼容 payload 中会压平成主 proposal + `params.proposals` 列表，仅供旧路由/UI 过渡使用。

## 给 AI-2 的直接接口说明

### Approval gate 输入
- 类型：`ApprovalGateInput`
- 文件：`src/it_ticket_agent/approval/models.py`
- 最少需要：`ticket_id`、`thread_id`、`proposals`
- `proposals` 应优先传 AI-1 state `ApprovalProposal` 经 adapter 转换后的 domain proposal

### Approval gate 主入口
- 接口：`ApprovalCoordinator.build_gate_result(gate_input)`
- 返回：`ApprovalGateResult`
- 行为：
  - 自动做 collect / dedupe / merge / policy evaluate
  - 若有需审批 proposal，则返回 `approval_request`
  - 若可自动通过，则返回 `approved_actions`
  - 若被策略拒绝，则返回 `rejected_proposals`

### Resume 入口
- 先用 adapter 把旧 `ApprovalDecisionRequest` 或 decision dict 转成 `ApprovalDecisionRecord`
- 再调用：`ApprovalCoordinator.build_resume_result(approval_request, decision_record)`
- 返回：`ApprovalGateResult`
- 若批准：读 `approved_actions`
- 若拒绝：读 `rejected_proposals`

### 旧兼容层什么时候用
- 如果当前 graph / HTTP 路由暂时还依赖旧 `ApprovalPayload` 或 `approval_store.py`，可以继续使用 facade 过渡。
- 但真正的多 proposal graph 编排，应直接调用 coordinator + domain models，不应再依赖旧 payload 压平结构。

## 下一步建议

### Block 4：对接说明与收口
- [x] 给 AI-2 输出 `ApprovalGateInput -> ApprovalGateResult` 接口说明
- [ ] 给 AI-4 输出 `ApprovedAction` / decision 结构约定（阻塞：AI-4 的 executor / verifier 最终输入契约与写回方式尚未冻结）
- [ ] 等 AI-1 最终契约落地后校准字段细节（阻塞：AI-1 已交首版，但跨组字段仍可能随 AI-4 / Integrator 接线微调）
- [ ] 如集成方需要，补一份单独的 approval gate 示例文档（阻塞：当前未收到 Integrator 的单独文档需求）

## 当前待办

- [x] 读完 `并行分工方案.md` 并确认 AI-3 边界
- [x] 核对当前审批实现与主要旧文件
- [x] 建立共享进度记录机制
- [x] 新建 `approval/` 目录骨架
- [x] 定义 approval 领域模型初版
- [x] 定义 `ApprovalCoordinator` 接口初版
- [x] 设计 approval store v2 结构
- [x] 产出统一审批请求 / 决议 DTO
- [x] 补齐旧 DTO 到新模型的兼容 adapter
- [x] 评估并设计旧 `approval_store.py` 的兼容 facade
- [x] 输出 approval gate 对接说明
- [ ] 给 AI-4 输出 `ApprovedAction` / decision 结构约定
- [ ] 校准跨组字段细节并视需要补示例文档

## 当前假设

- 在 AI-1 的 `ApprovalProposal` 契约进一步扩展前，当前 adapter 以 `target -> resource`、`evidence -> source_refs`、`metadata -> metadata` 的方式兼容。
- `schemas.py` 仍由 Integrator 统一收口的概率较高，因此当前继续用 adapter 维持旧 DTO 兼容，而不是直接改 HTTP 层 DTO。
- 旧 `approval_store.py` 现在只是 facade，后续不应再往里塞审批领域逻辑。
- 多 proposal 审批真正的消费方应直接使用 `ApprovalRequest.proposals`，不要依赖旧兼容 payload 中压平后的 `action/risk/reason`。

## 续做入口

下次继续时优先按这个顺序：

1. 查看 `共享进度.md` 里最新的 AI-3 进度块。
2. 查看本文件“给 AI-2 的直接接口说明”与“当前待办”。
3. 先给 AI-4 补 `ApprovedAction` / decision 结构约定，再视集成情况补单独示例文档。
4. 每完成一个独立子块，先更新 `共享进度.md`，再回写本文件。

## 本次更新时间

- 2026-03-28：完成职责对齐、现状核对、共享记录基线建立。
- 2026-03-28：完成 Block 1，落地 `approval/` 领域骨架、`ApprovalCoordinator` 初版、`ApprovalStoreV2` 初版，并完成轻量烟测。
- 2026-03-29：完成 Block 2/3，落地 adapter 兼容层、旧 store facade，并完成 adapter/facade 烟测。

## LD 任务归档（2026-03-29，已完成）

### 任务目标
- 把新审批领域层从“独立骨架”推进到“可被 graph 直接接入的兼容服务层”。

### 完成情况
- [x] 新增 `src/it_ticket_agent/approval/adapters.py`，已提供以下转换：
  - AI-1 state `ApprovalProposal` -> AI-3 `approval.models.ApprovalProposal`
  - 旧 `ApprovalPayload` -> 新 `ApprovalRequest`
  - 旧 `ApprovalDecisionRequest` / 旧 decision payload -> `ApprovalDecisionRecord`
  - 新 `ApprovedAction` -> AI-1 state `ApprovedAction` 所需字段
- [x] 已实现 `src/it_ticket_agent/approval_store.py` 的兼容 facade 方案：
  - 保留旧 `create/get/decide` 入口
  - 内部委托 `ApprovalStoreV2`
  - 已标明兼容层临时保留字段
- [x] 已在 `ai-3.md` 中补明确接口说明，供 AI-2 直接接：
  - `ApprovalGateInput -> ApprovalGateResult`
  - `ApprovalDecisionRecord -> build_resume_result(...)`

### 归档说明
- 本轮 LD 必交项已全部完成。
- 相关实现已在 `approval/`、`approval_store.py`、`ai-3.md`、`共享进度.md` 落地并记录。
- 后续如需继续推进，默认进入新的任务区，不再把本轮任务当作进行中事项。

## LD 当前任务区（待新任务）

### 状态
- 当前无新的 LD 指派任务。

### 预留位
- 任务目标：待补充
- 必交项：待补充
- 不要做：待补充
- 交付标准：待补充

## 补充说明（2026-03-29）

- 退回修正已完成：`approval/adapters.py` 不再在顶层 import `state` 包路径，避免 package 初始化阶段的反向触发。
- `approval/__init__.py` 已收窄为稳定领域入口；adapter 需从 `it_ticket_agent.approval.adapters` 显式导入。
- 已复验导入顺序：`import it_ticket_agent.approval; import it_ticket_agent.state` 与反向顺序都成功。
- 已复验 `approval_store.py` facade 路径：重复审批保护仍生效，会抛 `ApprovalStateError`。
- 当前统一映射入口为：`state_approval_proposal_to_domain()`、`domain_approval_proposal_to_state()`、`domain_approved_action_to_state()`。
- `approval/adapters.py` 已移除顶层 `state` 包依赖；兼容函数应从 `it_ticket_agent.approval.adapters` 显式导入，不再通过包根 re-export。
- `domain_approved_action_to_state()` 额外补齐了 `approved_by/approved_at/comment`，因为这三个审批决议字段对 resume 写回很关键。
