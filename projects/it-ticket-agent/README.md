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
- 调用 RAG 服务做知识检索
- 分流到 `direct_answer` 或 `react_tool_first`
- 在 `supervisor_loop` 中直接基于 tool schema 做 ReAct 推理
- 在无 LLM 时使用 rule-based fallback 保证最小诊断链路可运行
- 处理 HITL 审批与执行动作
- 汇总最终回复

## Tool Mock / 场景控制

当前验证链路支持两种控制方式：

- 请求级控制：在会话请求里显式传 `mock_scenario` / `mock_scenarios` / `mock_tool_responses`
- 环境变量级控制：用户继续使用普通问法，后台通过 `case` 统一切换整套故障现场

当前仓库里已经有 `40` 个导出 Tool 名称，其中 `39` 个是具体 Tool 实现，足够覆盖 CICD、K8s、日志、监控、网络、数据库、SDE、FinOps 的常见排障面。

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
- world dataset: [world_cases.json](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/data/evals/world_cases.json)
- runner: [run_agent_eval.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/scripts/run_agent_eval.py)

当前 dataset 已覆盖 `13` 个 case，主要分成三类：

- 单域收敛：`network / k8s / cicd / db / sde`
- 跨域扩展：`network -> db`、`k8s -> cicd`、`db -> network`、`cicd -> k8s`
- 强证据不扩域：验证已有足够异常证据时不会继续漂移到邻接域

当前 world dataset 额外覆盖 `5` 个“共享事故世界” case：

- 工具结果不再逐个手写
- 同一个 case 下的 `network / db / k8s / cicd / sde` 结果由同一份 `world_state` 投影生成
- 更适合验证“真因 + 噪声 + 时间线”下的搜索路径是否合理

设计原则：

- 保持 `LLM` 开启
- 默认关闭 `RAG`，避免把评估噪声混进来
- 只在工具边界注入 `mock_tool_responses`
- 支持 `tool_profile -> mock_tool_responses` 展开，复用 [mock_case_profiles.json](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/data/mock_case_profiles.json)
- 支持 `world_state` 驱动的共享事故仿真
- 既看最终是否命中根因，也记录搜索过程指标

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

把结果写成 JSON：

```bash
uv run python scripts/run_agent_eval.py --output ./data/eval-report.json
```

当前 report 会额外输出这些过程指标：

- `stop_reason`
- `expansion_probe_count`
- `expanded_domains`
- `rejected_tool_call_count`
- 汇总级别的 `avg_tool_calls_used`、`stop_reason_counts`

`world_state` case 的核心差异：

- 静态 mock dataset:
  每个 tool 的返回是直接写死的
- world dataset:
  每个 tool 的返回从同一个共享世界状态投影出来
  更适合验证“主因在 DB，但网络有轻微噪声”这类真实事故结构

当前这批 case 仍聚焦诊断质量，不包含审批恢复评估。原因是当前主链路里，真实 `LLM` 路径还没有稳定地产生 `approval proposal`；审批链路回归目前仍主要依赖 smoke / runtime tests。

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
