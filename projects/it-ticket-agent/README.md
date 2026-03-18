# IT Ticket Orchestrator

当前项目现在只负责 **编排与 Agent 主流程**，RAG 已拆到兄弟项目 `projects/it-ticket-rag-service`。

## 当前职责

- 接收用户工单请求
- 调用 RAG 服务做知识检索
- 根据结果路由诊断 Agent
- 处理审批与执行动作
- 汇总最终回复

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
    ├── graph.py
    ├── rag_client.py
    ├── llm.py
    ├── agent_clients.py
    ├── approval_store.py
    ├── executor.py
    ├── registry.py
    ├── sample_agents.py
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
make run-agent
make run-orchestrator
```

或者直接：

```bash
make dev
```

## 配置

`.env` 里主要保留：

```bash
RAG_ENABLED=true
RAG_SERVICE_BASE_URL=http://localhost:8200
RAG_SERVICE_TIMEOUT_SEC=30
```

## 部署

部署说明见 `docs/deployment.md:1`。

## API

```bash
curl http://localhost:8000/healthz
curl -X POST http://localhost:8000/api/v1/tickets \
  -H 'Content-Type: application/json' \
  -d '{"ticket_id":"INC-1","user_id":"u1","message":"服务一直重启","service":"order-service"}'
```

## RAG 相关操作

RAG 索引、pgvector、知识库文档都已经移到 `projects/it-ticket-rag-service`。
在当前目录执行的这些命令会自动代理到兄弟项目：

```bash
make sync-kb
make reindex-kb
make pgvector-up
```
