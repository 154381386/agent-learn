# IT Ticket Agent

一个面向生产场景的 IT 工单多 Agent 项目骨架。

## 新增：外部向量数据库方案

当前项目已升级为真正的外部向量数据库方案，推荐使用：
- **PostgreSQL + pgvector**：最好部署、最好维护、最适合现有项目演进
- **召回链路**：`BM25-like sparse recall + pgvector dense recall`
- **重排链路**：DashScope `qwen3-rerank`
- **最终排序**：`MMR`
- **模型职责分离**：
  - `LLM_*`：继续负责聊天、路由、最终回复
  - `EMBEDDING_*`：负责向量召回
  - `RERANK_*`：负责候选重排

## 为什么选 pgvector

- 部署简单：一个 PostgreSQL 服务即可
- 运维简单：备份、监控、权限控制都沿用 PostgreSQL 体系
- 适合中小规模生产：不必为了几千到几十万 chunk 单独引入 Milvus / ES 集群
- 易做增量更新：文档 checksum 对比后只重算新增/变更文档

## 目录

```text
projects/it-ticket-agent/
├── README.md
├── pyproject.toml
├── .env.example
├── docker-compose.pgvector.yml
├── docker/
│   └── init-pgvector.sql
├── docs/
│   ├── architecture.md
│   └── rag-production-plan.md
├── mock_kb/
├── scripts/
│   ├── dev.sh
│   └── reindex_kb.py
└── src/it_ticket_agent/
    ├── main.py
    ├── graph.py
    ├── knowledge.py
    ├── pgvector_store.py
    ├── settings.py
    └── ...
```

## 快速启动 pgvector

```bash
cd projects/it-ticket-agent
docker compose -f docker-compose.pgvector.yml up -d
```

默认连接串：

```bash
PGVECTOR_DSN=postgresql://app:app@localhost:5432/it_ticket_agent
```

## 运行方式

1. 安装依赖

```bash
cd projects/it-ticket-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

2. 配置 `.env`
- 聊天模型继续使用原来的 `LLM_*`
- 向量召回与重排单独配置 `EMBEDDING_*`、`RERANK_*`
- 如果要启用外部向量库：

```bash
RAG_VECTOR_BACKEND=pgvector
PGVECTOR_DSN=postgresql://app:app@localhost:5432/it_ticket_agent
```

3. 启动编排服务

```bash
uvicorn it_ticket_agent.main:app --reload --port 8000
```

## 增量文档向量化

这是当前版本的重点：**只处理新增/变更文档，不动其它文档**。

实现方式：
- 扫描知识库文档
- 计算每个文档的 `checksum`
- 对比 PostgreSQL 里已入库的 `checksum + chunking_signature + embedding_model`
- 未变化文档：直接跳过
- 新增/变化文档：重新切块、重新 embedding、覆盖该文档旧 chunks
- 已删除文档：从向量库中删除对应 chunks

### 命令方式

**增量同步**：

```bash
make sync-kb
```

**全量重建**：

```bash
make reindex-kb
```

### API 方式

**增量同步**：

```bash
curl -X POST http://localhost:8000/api/v1/rag/sync
```

**全量重建**：

```bash
curl -X POST http://localhost:8000/api/v1/rag/reindex
```

**查看状态**：

```bash
curl http://localhost:8000/api/v1/rag/status
```

## RAG 配置

### 向量数据库

```bash
RAG_VECTOR_BACKEND=pgvector
PGVECTOR_DSN=postgresql://app:app@localhost:5432/it_ticket_agent
PGVECTOR_SCHEMA=rag
PGVECTOR_DOCUMENTS_TABLE=documents
PGVECTOR_CHUNKS_TABLE=chunks
```

### 向量召回模型

```bash
EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
EMBEDDING_API_KEY=...
EMBEDDING_MODEL=text-embedding-v4
EMBEDDING_TIMEOUT_SEC=30
EMBEDDING_BATCH_SIZE=10
```

### 重排模型

```bash
RERANK_BASE_URL=https://dashscope.aliyuncs.com
RERANK_API_KEY=...
RERANK_MODEL=qwen3-rerank
RERANK_TIMEOUT_SEC=30
RERANK_TOP_N=20
RERANK_FAIL_OPEN=true
```

## 运维手册

完整操作手册见 `docs/operations-runbook.md:1`。

适合直接查这些问题：
- 怎么启动 / 停止 pgvector Docker
- 怎么启动 / 停止主服务和示例 Agent
- 怎么做增量更新与全量重建
- 怎么确认向量已经成功入库
- 怎么排查常见问题

## 当前实现范围

- 已实现：外部 pgvector 存储、增量向量化、稀疏+向量召回、rerank、MMR、LangGraph 编排、审批 interrupt、Agent 调用
- 后续建议：ACL、多租户、评测集、OpenTelemetry、pgvector 分片/只读副本
