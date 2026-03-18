# IT Ticket RAG Service

独立的 RAG 微服务项目，负责：
- 文档切块
- 增量索引
- sparse + dense hybrid retrieval
- rerank + MMR
- 对外提供 HTTP 检索接口

## 目录

```text
projects/it-ticket-rag-service/
├── README.md
├── pyproject.toml
├── .env.example
├── docker-compose.pgvector.yml
├── docker/
├── mock_kb/
├── scripts/
│   └── reindex_kb.py
└── src/it_ticket_rag_service/
    ├── rag_service.py
    ├── knowledge.py
    ├── pgvector_store.py
    ├── schemas.py
    └── settings.py
```

## 启动

```bash
cd projects/it-ticket-rag-service
uv pip install -e . --python .venv/bin/python
uv run uvicorn it_ticket_rag_service.rag_service:app --reload --port 8200
```

## 常用命令

```bash
make run
make sync-kb
make reindex-kb
make pgvector-up
```

## 部署

部署说明见 `docs/deployment.md:1`。

## API

```bash
curl http://localhost:8200/healthz
curl http://localhost:8200/api/v1/rag/status
curl -X POST http://localhost:8200/api/v1/rag/sync
curl -X POST http://localhost:8200/api/v1/rag/reindex
```
