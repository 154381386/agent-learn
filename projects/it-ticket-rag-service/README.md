# IT Ticket RAG Service

独立的 RAG 微服务项目，负责：
- 文档切块
- 增量索引
- sparse + dense hybrid retrieval
- rerank + MMR
- 对外提供 HTTP 检索接口

## 当前检索结构

知识文档采用 parent-child retrieval：

- 父块：按 Markdown section 生成，保存完整章节上下文，pgvector 后端落到 `parent_blocks` 表，本地后端落到 `index.json.parents`。
- 子块：在父块内继续按 `RAG_CHUNK_SIZE / RAG_CHUNK_OVERLAP` 切分并做 embedding，pgvector 后端落到 `chunks` 表，字段包含 `parent_id`。
- 查询：sparse/dense/rerank/MMR 仍以子块为检索粒度，命中后通过 `parent_id` hydrate 父块窗口返回。
- API：`hits/context` 同时返回 `child_snippet`、`parent_snippet`、`parent_id`、`parent_section` 和 `retrieval_granularity`，Agent 可以知道“哪个子块命中、最终给模型的是哪段父上下文”。

这样可以保留小块向量召回的精度，同时避免只把孤立片段塞给 Agent 导致上下文缺失。

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
