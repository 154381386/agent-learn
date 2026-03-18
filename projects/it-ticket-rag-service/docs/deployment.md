# Deployment Guide

这份文档说明 `projects/it-ticket-rag-service` 的部署方式。
当前项目是 **独立 RAG 微服务**，负责：
- 文档切块
- 增量索引
- pgvector 检索
- rerank / MMR
- HTTP 检索接口

## 1. 部署目标

默认部署结果：
- RAG API：`http://<host>:8200`
- PostgreSQL + pgvector：`localhost:5432`

## 2. 前置依赖

- Python `3.11+`
- Docker / Docker Compose
- embedding API
- rerank API

## 3. 目录准备

```bash
cd /srv/agent-learn/projects/it-ticket-rag-service
python3 -m venv .venv
./.venv/bin/pip install -e .
```

## 4. 环境变量

建议在项目根目录放置 `.env`：

```bash
APP_ENV=prod
HOST=0.0.0.0
PORT=8200

RAG_ENABLED=true
RAG_DOCS_PATH=./mock_kb
RAG_INDEX_DIR=./data/rag
RAG_AUTO_REINDEX_ON_BOOT=true
RAG_VECTOR_BACKEND=pgvector
PGVECTOR_DSN=postgresql://app:app@127.0.0.1:5432/it_ticket_agent
PGVECTOR_SCHEMA=rag
PGVECTOR_DOCUMENTS_TABLE=documents
PGVECTOR_CHUNKS_TABLE=chunks
RAG_CHUNK_SIZE=900
RAG_CHUNK_OVERLAP=160
RAG_TOP_K=5
RAG_DIRECT_ANSWER_MIN_SCORE=0.58
RAG_DIRECT_ANSWER_MIN_MARGIN=0.10
RAG_SPARSE_WEIGHT=0.55
RAG_DENSE_WEIGHT=0.45
RAG_SPARSE_CANDIDATES=40
RAG_DENSE_CANDIDATES=40
RAG_HYBRID_CANDIDATE_LIMIT=60
RAG_RRF_K=60
RAG_MMR_LAMBDA=0.72
RAG_FAIL_ON_EMBEDDING_ERROR=false

EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
EMBEDDING_API_KEY=your-embedding-api-key
EMBEDDING_MODEL=text-embedding-v4
EMBEDDING_TIMEOUT_SEC=30
EMBEDDING_BATCH_SIZE=10

RERANK_BASE_URL=https://dashscope.aliyuncs.com
RERANK_API_KEY=your-rerank-api-key
RERANK_MODEL=qwen3-rerank
RERANK_TIMEOUT_SEC=30
RERANK_TOP_N=20
RERANK_RETURN_DOCUMENTS=true
RERANK_INSTRUCT=Given a user query, rank the most relevant enterprise knowledge passages that best answer the question.
RERANK_FAIL_OPEN=true
```

## 5. 启动 pgvector

```bash
cd /srv/agent-learn/projects/it-ticket-rag-service
docker compose -f docker-compose.pgvector.yml up -d
```

确认：

```bash
docker compose -f docker-compose.pgvector.yml ps
```

## 6. 初始化索引

首次部署建议先做一次全量重建：

```bash
cd /srv/agent-learn/projects/it-ticket-rag-service
./.venv/bin/python scripts/reindex_kb.py --force --base-url http://127.0.0.1:8200
```

如果服务尚未启动，也可以先启动服务，再调用：

```bash
curl -X POST http://127.0.0.1:8200/api/v1/rag/reindex
```

## 7. 启动方式

### 7.1 前台启动

```bash
cd /srv/agent-learn/projects/it-ticket-rag-service
./.venv/bin/uvicorn it_ticket_rag_service.rag_service:app --host 0.0.0.0 --port 8200
```

### 7.2 systemd 启动示例

`/etc/systemd/system/it-ticket-rag.service`

```ini
[Unit]
Description=IT Ticket RAG Service
After=network.target docker.service
Requires=docker.service

[Service]
Type=simple
WorkingDirectory=/srv/agent-learn/projects/it-ticket-rag-service
ExecStart=/srv/agent-learn/projects/it-ticket-rag-service/.venv/bin/uvicorn it_ticket_rag_service.rag_service:app --host 0.0.0.0 --port 8200
Restart=always
RestartSec=3
User=deploy
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

启用：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now it-ticket-rag
sudo systemctl status it-ticket-rag
```

## 8. 健康检查

```bash
curl http://127.0.0.1:8200/healthz
curl http://127.0.0.1:8200/api/v1/rag/status
```

## 9. 日常运维

增量同步：

```bash
curl -X POST http://127.0.0.1:8200/api/v1/rag/sync
```

全量重建：

```bash
curl -X POST http://127.0.0.1:8200/api/v1/rag/reindex
```

## 10. 升级步骤

```bash
cd /srv/agent-learn
git pull
cd projects/it-ticket-rag-service
./.venv/bin/pip install -e .
sudo systemctl restart it-ticket-rag
```

如果修改了知识文档或 embedding 配置，升级后再执行一次：

```bash
curl -X POST http://127.0.0.1:8200/api/v1/rag/sync
```

## 11. 安全建议

- `:8200` 建议仅内网开放
- pgvector 数据库不要直接暴露公网
- `.env` 中 API Key 使用部署平台密钥管理能力托管
