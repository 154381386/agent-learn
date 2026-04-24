# IT Ticket Orchestrator

当前项目当前默认负责 **Smart Router + Tool-First ReAct Supervisor + HITL 审批主流程**，RAG 已拆到兄弟项目 `projects/it-ticket-rag-service`。

当前默认运行模式是 `react_tool_first`：

- FAQ / SOP / 知识问答走 `direct_answer`
- 诊断类请求进入 `supervisor_loop`
- 高风险动作统一经 `approval_gate`
- 审批后的执行统一经 `execute_approved_action`

为了保证本地开发和回归测试可运行，当前在**未配置 LLM** 时会启用最小版 `rule-based react fallback`，用于驱动 smoke 级诊断、审批、恢复与反馈链路。

## 当前职责

- 接收用户工单请求
- 调用 RAG 服务做知识检索与历史案例召回
- 分流到 `direct_answer` 或 `react_tool_first`
- 在 `supervisor_loop` 中直接基于 tool schema 做 ReAct 推理
- 在无 LLM 时使用 rule-based fallback 保证最小诊断链路可运行
- 处理 HITL 审批与执行动作
- 汇总最终回复

## 历史案例召回

当前项目里的历史案例召回不是“把工单全文当普通文档再搜一遍”，而是单独的 `case-memory recall`。

- `direct_answer` 默认不走历史案例召回，只走知识 RAG
- 诊断路径里，`context_collector` 会先做一轮自动案例预召回
- 但这轮预召回现在只在上下文足够具体时才触发：
  - `service` 已明确
  - 当前问题能推断出明确 `failure_mode`
  - 或消息里已经出现较具体的症状关键词
- 如果用户输入还很泛，比如“服务出问题了，帮我看看”，当前会跳过自动预召回，并在 `context_snapshot.case_recall` 里记录原因
- supervisor 后续可以显式暴露 `search_similar_incidents`，让模型在拿到更多 live evidence 后再主动查历史案例
- case-memory 外部服务失败时不会打穿诊断主链：
  - 自动预召回会降级为空 `similar_cases`，并记录 `prefetch_status / prefetch_error_type / case_memory_reason`
  - 显式 `search_similar_incidents` 即使无命中或失败，也会回写 `tool_search_count / last_tool_status / last_tool_hit_count / tool_failures`
  - 案例索引同步失败只记录 `last_sync_metadata` 和 warning，不影响工单完成与反馈回写

当前这条链路的设计原则是：

- 自动首跳召回只是 `background hint`
- `similar_cases` 只能辅助缩小候选面，不能替代现场证据
- 真正高质量的历史案例召回应发生在：
  - 已有更具体的 symptom / failure mode / root cause direction 之后
  - 或 retrieval planner 明确判断需要扩查案例时

## Tool Mock / 场景控制

当前验证链路支持两种控制方式：

- 请求级控制：在会话请求里显式传 `mock_scenario` / `mock_scenarios` / `mock_tool_responses`
- 环境变量级控制：用户继续使用普通问法，后台通过 `case` 统一切换整套故障现场

当前仓库里已经有 `31` 个默认注册 Tool，足够覆盖 CICD、K8s、日志、监控、网络、数据库、SDE、以及知识/历史案例检索的常见排障面。

- `mock_scenario`: 为当前服务设置全局场景，例如 `oom`、`health`、`normal`、`error`
- `mock_scenarios`: 为不同服务分别指定场景
- `mock_tool_responses`: 对某个具体 tool 直接覆盖返回值
- `mock_world_state`: 用一份共享事故世界统一投影多域工具结果

当前 mock 优先级为：

1. `mock_response` / `mock_tool_responses`
2. `mock_world_state`
3. `mock_case`
4. `mock_scenario` / profile

示例：让 `checkout-service` 走 OOM 场景

```bash
curl -X POST http://localhost:8000/api/v1/conversations \
  -H 'Content-Type: application/json' \
  -d '{
    "user_id":"u1",
    "message":"checkout-service pod OOMKilled，帮我排查",
    "service":"checkout-service",
    "mock_scenario":"oom"
  }'
```

示例：强行覆盖某个 tool 的返回

```bash
curl -X POST http://localhost:8000/api/v1/conversations \
  -H 'Content-Type: application/json' \
  -d '{
    "user_id":"u1",
    "message":"帮我检查服务日志",
    "service":"checkout-service",
    "mock_tool_responses":{
      "inspect_pod_logs":{
        "summary":"命中自定义日志 mock",
        "payload":{
          "error_pattern":"oom_killed",
          "oom_detected":true,
          "log_snippets":["java.lang.OutOfMemoryError: Java heap space"]
        },
        "evidence":["custom mock log"]
      }
    }
  }'
```

## Case 环境变量控制

如果你希望用户只说普通问题，例如“order service 为什么总是超时”，可以直接在环境变量里切 case。

- `IT_TICKET_AGENT_CASE=case1`
- `IT_TICKET_AGENT_CASE=case2`
- `IT_TICKET_AGENT_CASES='{"order-service":"case1","payment-service":"case2"}'`
- `IT_TICKET_AGENT_CASE_PROFILES_PATH=/path/to/mock_case_profiles.json`

当前内置了两个典型案例：

- `case1`: 日志与 Pod 事件表现为 `OOMKilled`，监控同步报错，网络排查基本正常
- `case2`: 日志基本正常，网络链路和监控抖动明显，上游依赖与线程池也会同步波动

示例：用户只问普通问题，但后台用环境变量切到 `case1`

```bash
export IT_TICKET_AGENT_CASE=case1
curl -X POST http://localhost:8000/api/v1/conversations \
  -H 'Content-Type: application/json' \
  -d '{
    "user_id":"u1",
    "message":"order service为什么总是超时"
  }'
```

对应的 case 配置文件在 [mock_case_profiles.json](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/data/mock_case_profiles.json)，可以继续按你自己的场景扩展。

## Agent Eval

当前仓库已新增一套面向 `真实 LLM + mocked tool outputs` 的离线评估入口：

- dataset: [tool_mock_cases.json](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/data/evals/tool_mock_cases.json)
- rag dataset: [rag_cases.json](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/data/evals/rag_cases.json)
- world dataset: [world_cases.json](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/data/evals/world_cases.json)
- session-flow contract dataset: [session_flow_cases.json](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/data/evals/session_flow_cases.json)
- session-flow live dataset: [session_flow_live_cases.json](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/data/evals/session_flow_live_cases.json)
- runner: [run_agent_eval.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/scripts/run_agent_eval.py)

当前静态 tool dataset 已覆盖 `13` 个 case，主要分成三类：

- 单域收敛：`network / k8s / cicd / db / sde`
- 跨域扩展：`network -> db`、`k8s -> cicd`、`db -> network`、`cicd -> k8s`
- 强证据不扩域：验证已有足够异常证据时不会继续漂移到邻接域

当前 world dataset 额外覆盖 `5` 个“共享事故世界” case：

- 工具结果不再逐个手写
- 同一个 case 下的 `network / db / k8s / cicd / sde` 结果由同一份 `world_state` 投影生成
- 更适合验证“真因 + 噪声 + 时间线”下的搜索路径是否合理

当前 rag dataset 额外覆盖 `10` 个知识链路 case：

- FAQ 命中强知识后直接走 `direct_answer`
- RAG 不足时回退到诊断主链路
- 诊断中允许携带知识背景，但根因仍由 live tool 决定
- 检索知识与实时证据冲突时，以实时证据为准
- case recall 扩展是否真的新增历史命中
- 诊断中 agent 是否会主动调用 `search_knowledge_base`
- 知识与历史案例都不足时，是否明确暴露 `missing_evidence`
- rules-based query rewrite 是否真的拉到更聚焦的新增 hit
- rules-based query rewrite 没有带来新增命中时，是否识别“rewrite 无收益”
- 多个 rewritten query 里只有部分有增量价值时，是否保留“partial gain”质量信号

当前 session-flow contract dataset 覆盖 `9` 个多轮会话 case：

- `clarification -> resume -> feedback`
- `approval -> execute -> feedback`
- `approval -> reject -> terminal`
- `approval -> expire -> terminal`
- `approval -> cancel -> terminal`
- `feedback resume -> incident case update`
- `topic shift -> supersede approval -> restart analysis`

当前 session-flow live dataset 额外覆盖 `4` 个真实 LLM 多轮 case：

- `clarification -> resume -> completed diagnosis`
- `follow-up supplement marker -> supplement`
- `explicit supplement mode -> keep supplement semantics while shifting to db`
- `new issue marker -> restart diagnosis into db`

设计原则：

- 保持 `LLM` 开启
- 默认关闭 `RAG`，避免把评估噪声混进来
- 只在工具边界注入 `mock_tool_responses`
- 支持 `tool_profile -> mock_tool_responses` 展开，复用 [mock_case_profiles.json](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/data/mock_case_profiles.json)
- 支持 `world_state` 驱动的共享事故仿真
- 支持 `mock_rag_context / mock_rag_context_by_query / mock_retrieval_expansion`
- 支持按 case 配置 `llm_mode`；当前 session-flow 回归默认用 `disabled` 跑确定性状态机链路
- live session-flow 只校验稳定的高层行为，例如 `message_event_type / incremental_tool_domains / pending_interrupt`，不把真实 LLM 的每一步推理顺序写死
- 既看最终是否命中根因，也记录搜索过程指标
- 每个 dataset 现在都可以带聚合 `gate`，用于卡住 pass rate、平均 tool 调用数、step pass rate 这类回归门槛

## 线上 Bad Case 候选闭环

当前项目已经补了一条最小闭环：把线上高风险样本先沉淀成 `bad_case_candidate`，再导成离线 eval skeleton，最后人工筛成正式数据集。

当前不会直接把线上样本自动塞进 gate，原因有两个：

- 线上 bad case 先要做归因，不能把“答错了”直接等价成“应该进回归”
- 自动导出的第一版通常只知道请求、上下文、工具路径和反馈信号，还需要人工补期望和 mock 边界

当前默认会在这两类场景自动打候选：

- `runtime_completion`
  主要看：
  - `tool_budget_reached / iteration_guardrail_reached`
  - `rejected_tool_call_count > 0`
  - `retrieval_subquery_count > 0` 但 `added_rag_hits = 0 && added_case_hits = 0`
  - 某条 rewritten query 虽然带来新增命中，但方向和最终主因 taxonomy 不一致
- `feedback_reopen`
  主要看：
  - 用户明确 `human_verified=false`
  - 用户给出 `actual_root_cause_hypothesis`
  - 用户拒绝当前建议并附带新信息重新分析

候选样本当前会保留这些关键信息：

- `request_payload`
- `response_payload`
- `incident_state_snapshot`
- `context_snapshot`
- `observations`
- `retrieval_expansion`
- `human_feedback`
- `conversation_turns`
- `system_events`

导出脚本：

- script: [export_bad_case_candidates.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/scripts/export_bad_case_candidates.py)
- 默认输出目录: [data/evals/generated](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/data/evals/generated)

示例：

```bash
cd projects/it-ticket-agent
uv run python scripts/export_bad_case_candidates.py
```

只导某个候选并更新导出状态：

```bash
cd projects/it-ticket-agent
uv run python scripts/export_bad_case_candidates.py \
  --candidate-id <candidate_id> \
  --mark-exported
```

把人工整理过的 skeleton 合回正式数据集：

```bash
cd projects/it-ticket-agent
uv run python scripts/merge_curated_bad_cases.py \
  --input ./data/evals/generated/<curated-file>.json
```

如果不传 `--input`，脚本会默认扫描 `data/evals/generated/*.json`。

当前 curated merge 会做两件事：

- 校验这个 skeleton 是否已经去掉占位信息
  - `case_id` 不能还是 `todo_*`
  - `description` 里不能还带 `TODO`
  - `eval_skeleton` 里不能还留 `_todo`
- 按 `target_dataset` 合回正式数据集
  - `tool_mock` -> `tool_mock_cases.json`
  - `rag` -> `rag_cases.json`
  - `session_flow` -> `session_flow_cases.json`

如果 merge 成功，对应 candidate 会从：

- `pending`
- `exported`

继续推进到：

- `merged`

这样后面就能区分：

- 只是已经导出过
- 还是已经真的进入正式回归资产

第一版导出会按简单规则给出 skeleton：

- 有 `retrieval_expansion` 的优先导成 `rag` skeleton
- `feedback_reopen` 或明显多轮的导成 `session_flow` skeleton
- 其余默认导成 `tool_mock` skeleton

这些 skeleton 先写文件，不直接并入正式数据集；等人工补齐 mock 和 expect 后，再决定是否进入：

- `tool_mock_cases.json`
- `rag_cases.json`
- `session_flow_cases.json`
- `session_flow_live_cases.json`

另外，`feedback_reopen` 后再次诊断时，当前实现已经会保留 `incident_case` 里已有的人工反馈字段：

- `human_verified`
- `actual_root_cause_hypothesis`
- `hypothesis_accuracy`

这样不会再出现：

- `bad_case_candidate` 里保留了人工真因
- 但 `incident_case` 被后续一次普通 upsert 覆盖回默认值

这一步是为了保证案例库、bad case 候选池、以及后续 case recall 的学习信号保持一致。

运行示例：

```bash
cd projects/it-ticket-agent
uv run python scripts/run_agent_eval.py
```

只跑单个 case：

```bash
uv run python scripts/run_agent_eval.py --case-id network_profile_prefers_network_tools
```

运行共享事故世界 dataset：

```bash
uv run python scripts/run_agent_eval.py --dataset ./data/evals/world_cases.json
```

运行知识链路 rag dataset：

```bash
uv run python scripts/run_agent_eval.py --dataset ./data/evals/rag_cases.json
```

运行多轮 session-flow dataset：

```bash
uv run python scripts/run_agent_eval.py \
  --dataset ./data/evals/session_flow_cases.json \
  --allow-llm-disabled \
  --output ./data/session-flow-eval-report.json
```

运行真实 LLM 的 session-flow live dataset：

```bash
uv run python scripts/run_agent_eval.py \
  --dataset ./data/evals/session_flow_live_cases.json \
  --output ./data/session-flow-live-eval-report.json
```

把结果写成 JSON：

```bash
uv run python scripts/run_agent_eval.py --output ./data/eval-report.json
```

如果只跑子集 case，dataset 级别 gate 会自动跳过；如果要临时忽略 gate，可以显式加：

```bash
uv run python scripts/run_agent_eval.py --ignore-gates
```

也可以直接用 Makefile：

```bash
make eval-agent
make eval-rag
make eval-world
make eval-session-flow
make eval-session-flow-live
make eval-regression
```

当前 report 会额外输出这些过程指标：

- `stop_reason`
- `expansion_probe_count`
- `expanded_domains`
- `rejected_tool_call_count`
- 汇总级别的 `avg_tool_calls_used`、`stop_reason_counts`
- 如果 dataset 配了门槛，还会输出 `gate_result`

`rag_cases` 当前主要看这几类信号：

- `intent=direct_answer` 时是否真的不进入工具诊断
- `min_sources_count`
- `min_retrieval_subquery_count`
- `min_added_rag_hits / min_added_case_hits`
- `max_added_rag_hits / max_added_case_hits`：可区分 rewrite 全无收益、部分有效、以及全部有效
- `retrieval_query_contains`：query rewrite 是否真的生成了更聚焦的子查询
- `retrieval_query_metrics`：每个 rewritten query 各自带来了多少新增 rag hit / case hit，以及它是否和最终主因方向一致
- `missing_evidence_contains`：当知识和历史案例都不足时，是否明确保留知识缺口
- 检索知识和 live evidence 冲突时，最终是否仍由 live evidence 收敛

其中与 query planning 直接相关的 case 可以按 case 配置把 `retrieval_planner` 强制切到 rules mode，避免环境里的 planner LLM 波动把主回归污染掉。

`world_state` case 的核心差异：

- 静态 mock dataset:
  每个 tool 的返回是直接写死的
- world dataset:
  每个 tool 的返回从同一个共享世界状态投影出来
  更适合验证“主因在 DB，但网络有轻微噪声”这类真实事故结构

`run_agent_eval.py` 会自动识别 dataset 类型：

- 普通 agent eval：看单轮诊断结果和搜索过程
- session-flow eval：看 step 级别的 `response_status / session_status / pending_interrupt / system_event / approval_event / message_event_type`

## 兄弟项目

- 编排项目：`projects/it-ticket-agent`
- RAG 项目：`projects/it-ticket-rag-service`

## 关键文件

```text
projects/it-ticket-agent/
├── README.md
├── pyproject.toml
├── .env.example
├── scripts/
│   └── dev.sh
└── src/it_ticket_agent/
    ├── main.py
    ├── approval_store.py
    ├── rag_client.py
    ├── agents/
    ├── runtime/
    ├── tools/
    ├── mcp/
    └── schemas.py
```

## 启动

```bash
cd projects/it-ticket-agent
uv pip install -e . --python .venv/bin/python
```

启动三件套：

```bash
make run-rag-service
make run-cicd-mcp
make run-orchestrator
```

使用 Runtime Postgres：

```bash
make pg-up
make run-orchestrator-pg
```

或者直接：

```bash
make dev
```

如果希望开发环境默认走 Runtime Postgres：

```bash
make dev-pg
```

把现有 SQLite 运行时数据迁到 Runtime Postgres：

```bash
make migrate-runtime-pg
```

验证 Runtime Postgres 存储：

```bash
make test-runtime-pg
```

## 配置

`.env` 里主要保留：

```bash
MCP_CONNECTIONS_PATH=./mcp_connections.yaml
RAG_ENABLED=true
RAG_SERVICE_BASE_URL=http://localhost:8200
RAG_SERVICE_TIMEOUT_SEC=30
STORAGE_BACKEND=sqlite
APPROVAL_DB_PATH=./data/approvals.db
POSTGRES_DSN=postgresql://app:app@127.0.0.1:5433/it_ticket_agent_runtime
```

## Runtime Postgres

当前 runtime 已支持把核心状态存到 Postgres。开发环境默认推荐用 Docker 启：

```bash
make pg-up
make pg-ps
make pg-logs
```

停止：

```bash
make pg-down
```

默认连接信息：

```bash
POSTGRES_DSN=postgresql://app:app@127.0.0.1:5433/it_ticket_agent_runtime
```

从现有 SQLite 迁移到 Postgres：

```bash
uv run python scripts/migrate_sqlite_to_postgres.py \
  --sqlite-path ./data/approvals.db \
  --postgres-dsn postgresql://app:app@127.0.0.1:5433/it_ticket_agent_runtime
```

## 部署

部署说明见 `docs/部署说明.md:1`。

## 重构方案

- Tool-first ReAct 迁移方案：`docs/Tool-First-ReAct迁移方案.md:1`
- 开发 TODO 清单：`docs/Tool-First-ReAct开发TODO.md:1`

## 当前验证基线

最小回归当前以 `unittest` 为准：

```bash
uv run python -m unittest discover -s tests -q
```

## API

```bash
curl http://localhost:8000/healthz
curl -X POST http://localhost:8000/api/v1/conversations \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"u1","message":"服务一直重启","service":"order-service"}'
```

## RAG 相关操作

RAG 索引、pgvector、知识库文档都已经移到 `projects/it-ticket-rag-service`。
在当前目录执行的这些命令会自动代理到兄弟项目：

```bash
make sync-kb
make reindex-kb
make pgvector-up
```
