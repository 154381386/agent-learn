# Operations Runbook

这份手册是给“下次直接照着操作”的。

适用范围：
- 启动 / 停止 pgvector Docker
- 启动 / 停止编排服务与示例 Agent
- 检查服务状态
- 增量同步知识库
- 全量重建知识库
- 验证向量是否已经落库
- 常见故障排查

## 1. 目录与前提

项目根目录：

```bash
cd /Users/lyb/workspace/agent-learn/projects/it-ticket-agent
```

默认约定：
- 编排服务：`http://localhost:8000`
- 示例 Agent：`http://localhost:8101`
- pgvector(PostgreSQL)：`localhost:5432`
- 默认数据库：`it_ticket_agent`
- 默认用户：`app`
- 默认密码：`app`

## 2. 第一次初始化

### 2.1 安装 Python 依赖

如果 `.venv` 已存在，直接执行：

```bash
cd /Users/lyb/workspace/agent-learn/projects/it-ticket-agent
uv pip install -e . --python .venv/bin/python
```

如果 `.venv` 不存在：

```bash
cd /Users/lyb/workspace/agent-learn/projects/it-ticket-agent
python3 -m venv .venv
uv pip install -e . --python .venv/bin/python
```

### 2.2 检查 `.env`

必须确保 `.env` 至少包含这些关键项：

```bash
RAG_VECTOR_BACKEND=pgvector
PGVECTOR_DSN=postgresql://app:app@localhost:5432/it_ticket_agent

LLM_BASE_URL=...
LLM_API_KEY=...
LLM_MODEL=...

EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
EMBEDDING_API_KEY=...
EMBEDDING_MODEL=text-embedding-v4

RERANK_BASE_URL=https://dashscope.aliyuncs.com
RERANK_API_KEY=...
RERANK_MODEL=qwen3-rerank
```

说明：
- `LLM_*` 继续用于聊天、路由、最终回复
- `EMBEDDING_*` 用于向量召回
- `RERANK_*` 用于候选重排

## 3. 启动与停止 pgvector Docker

### 3.1 启动 pgvector

```bash
cd /Users/lyb/workspace/agent-learn/projects/it-ticket-agent
docker compose -f docker-compose.pgvector.yml up -d
```

### 3.2 查看 pgvector 状态

```bash
cd /Users/lyb/workspace/agent-learn/projects/it-ticket-agent
docker compose -f docker-compose.pgvector.yml ps
```

你应该看到类似：
- 容器名：`it-ticket-agent-pgvector`
- 状态：`Up`
- 最好带 `healthy`

### 3.3 查看 pgvector 日志

```bash
cd /Users/lyb/workspace/agent-learn/projects/it-ticket-agent
docker compose -f docker-compose.pgvector.yml logs -f pgvector
```

退出日志跟随：
- `Ctrl + C`

### 3.4 停止 pgvector

```bash
cd /Users/lyb/workspace/agent-learn/projects/it-ticket-agent
docker compose -f docker-compose.pgvector.yml down
```

说明：
- 这会停止并移除容器
- **不会删除卷数据**，所以数据库内容会保留

### 3.5 停止并删除数据库卷（危险）

只有你明确想清空向量数据库时才执行：

```bash
cd /Users/lyb/workspace/agent-learn/projects/it-ticket-agent
docker compose -f docker-compose.pgvector.yml down -v
```

说明：
- 这会删除 `pgvector-data` 卷
- 会导致数据库里的向量和元数据全部清空

## 4. 启动与停止应用服务

项目里有两个进程：
- 编排服务（主服务）
- 示例 Agent Runtime（本地诊断 agent）

建议用两个终端分别启动。

### 4.1 启动编排服务

```bash
cd /Users/lyb/workspace/agent-learn/projects/it-ticket-agent
uv run uvicorn it_ticket_agent.main:app --reload --port 8000
```

启动成功后可访问：
- 页面：`http://localhost:8000`
- 健康检查：`http://localhost:8000/healthz`

### 4.2 启动示例 Agent Runtime

```bash
cd /Users/lyb/workspace/agent-learn/projects/it-ticket-agent
uv run uvicorn it_ticket_agent.sample_agents:app --reload --port 8101
```

健康检查：
- `http://localhost:8101/healthz`

### 4.3 停止服务

如果服务就是在当前终端前台跑的：
- 直接按 `Ctrl + C`

如果你不确定进程还在不在，可以查：

```bash
ps -axo pid,command | grep "uvicorn it_ticket_agent.main:app" | grep -v grep
ps -axo pid,command | grep "uvicorn it_ticket_agent.sample_agents:app" | grep -v grep
```

如果需要强制结束，先查 PID，再执行：

```bash
kill <PID>
```

## 5. 服务状态检查

### 5.1 检查主服务

```bash
curl http://localhost:8000/healthz
```

预期：

```json
{"status":"ok"}
```

### 5.2 检查示例 Agent

```bash
curl http://localhost:8101/healthz
```

### 5.3 检查 RAG 状态

```bash
curl http://localhost:8000/api/v1/rag/status
```

重点字段说明：
- `vector_backend`：当前是否走 `pgvector`
- `documents`：当前加载到应用内存的文档数
- `chunks`：当前加载到应用内存的 chunk 数
- `embedding_enabled`：当前索引是否带向量
- `index_path`：如果是 pgvector，会显示 `pgvector://...`

## 6. 增量更新知识库

这是日常最常用的操作。

### 6.1 什么时候用增量同步

当你做了下面任一操作时，用增量同步：
- 新增了一个 Markdown 知识文档
- 修改了某个现有文档内容
- 删除了某个文档

### 6.2 增量同步命令

```bash
cd /Users/lyb/workspace/agent-learn/projects/it-ticket-agent
make sync-kb
```

它的行为是：
- 扫描 `mock_kb/`
- 计算每个文档的 `checksum`
- 对比数据库里已记录的 `checksum + chunking_signature + embedding_model`
- **未变化文档：跳过**
- **变化文档：只重建该文档自己的 chunks**
- **删除文档：只删除该文档对应记录**
- 其它文档完全不动

### 6.3 增量同步 API

```bash
curl -X POST http://localhost:8000/api/v1/rag/sync
```

返回里重点看：
- `new_documents`
- `updated_documents`
- `removed_documents`
- `skipped_documents`

## 7. 全量重建知识库

### 7.1 什么时候用全量重建

当你做了这些操作时，建议全量重建：
- 更换了 embedding 模型
- 大幅修改了 chunk 策略
- 想彻底清理历史索引
- 怀疑数据库里的索引状态不一致

### 7.2 全量重建命令

```bash
cd /Users/lyb/workspace/agent-learn/projects/it-ticket-agent
make reindex-kb
```

### 7.3 全量重建 API

```bash
curl -X POST http://localhost:8000/api/v1/rag/reindex
```

## 8. 如何确认向量已经落库

### 8.1 看同步进程是否结束

```bash
ps -axo pid,command | grep "scripts/reindex_kb.py" | grep -v grep
```

- 有输出：还在跑
- 没输出：已经结束

### 8.2 直接查数据库行数

```bash
cd /Users/lyb/workspace/agent-learn/projects/it-ticket-agent
./.venv/bin/python - <<'PY'
import os
import psycopg
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path('.env'))
dsn = os.getenv('PGVECTOR_DSN')
with psycopg.connect(dsn) as conn:
    with conn.cursor() as cur:
        cur.execute("select count(*) from rag.documents")
        docs = cur.fetchone()[0]
        cur.execute("select count(*) from rag.chunks")
        chunks = cur.fetchone()[0]
        cur.execute("select count(distinct doc_id) from rag.chunks")
        chunk_docs = cur.fetchone()[0]
        print({"documents": docs, "chunks": chunks, "chunk_docs": chunk_docs})
PY
```

当前这套知识库的预期值是：
- `documents = 25`
- `chunks = 2677`
- `chunk_docs = 25`

## 9. 常用日常操作清单

### 场景 A：每天正常启动

1. 启动 pgvector：
```bash
cd /Users/lyb/workspace/agent-learn/projects/it-ticket-agent
docker compose -f docker-compose.pgvector.yml up -d
```

2. 启动主服务：
```bash
cd /Users/lyb/workspace/agent-learn/projects/it-ticket-agent
uv run uvicorn it_ticket_agent.main:app --reload --port 8000
```

3. 启动示例 Agent：
```bash
cd /Users/lyb/workspace/agent-learn/projects/it-ticket-agent
uv run uvicorn it_ticket_agent.sample_agents:app --reload --port 8101
```

### 场景 B：只改了知识文档

```bash
cd /Users/lyb/workspace/agent-learn/projects/it-ticket-agent
make sync-kb
```

### 场景 C：改了 embedding 模型

1. 修改 `.env` 里的 `EMBEDDING_MODEL`
2. 执行：

```bash
cd /Users/lyb/workspace/agent-learn/projects/it-ticket-agent
make reindex-kb
```

### 场景 D：下班停服务

1. 停主服务和 Agent：
- 在各自终端 `Ctrl + C`

2. 停 pgvector：
```bash
cd /Users/lyb/workspace/agent-learn/projects/it-ticket-agent
docker compose -f docker-compose.pgvector.yml down
```

## 10. 常见故障排查

### 10.1 `docker compose up` 失败

先看：

```bash
docker compose -f docker-compose.pgvector.yml logs pgvector
```

常见原因：
- 5432 端口被占用
- Docker Desktop 未启动
- 镜像拉取网络失败

### 10.2 `make sync-kb` 很慢

第一次全量 embedding 正常会慢。
原因是：
- 要对所有 chunks 调 embedding
- 还要写入 PostgreSQL

判断是否真的卡死：
- 看进程是否还在：
```bash
ps -axo pid,command | grep "scripts/reindex_kb.py" | grep -v grep
```
- 看数据库行数是否增长：
```bash
./.venv/bin/python - <<'PY'
import os, psycopg
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path('.env'))
with psycopg.connect(os.getenv('PGVECTOR_DSN')) as conn:
    with conn.cursor() as cur:
        cur.execute("select count(*) from rag.chunks")
        print(cur.fetchone()[0])
PY
```

### 10.3 `rag/status` 显示不是 `pgvector`

检查 `.env`：

```bash
RAG_VECTOR_BACKEND=pgvector
PGVECTOR_DSN=postgresql://app:app@localhost:5432/it_ticket_agent
```

改完后重启主服务。

### 10.4 embedding 或 rerank 报错

优先检查：
- `EMBEDDING_API_KEY`
- `EMBEDDING_MODEL`
- `RERANK_API_KEY`
- `RERANK_MODEL`
- 外网网络是否可达

## 11. 推荐的最短操作路径

### 启动整套服务

```bash
cd /Users/lyb/workspace/agent-learn/projects/it-ticket-agent
docker compose -f docker-compose.pgvector.yml up -d
```

```bash
cd /Users/lyb/workspace/agent-learn/projects/it-ticket-agent
uv run uvicorn it_ticket_agent.main:app --reload --port 8000
```

```bash
cd /Users/lyb/workspace/agent-learn/projects/it-ticket-agent
uv run uvicorn it_ticket_agent.sample_agents:app --reload --port 8101
```

### 只同步文档改动

```bash
cd /Users/lyb/workspace/agent-learn/projects/it-ticket-agent
make sync-kb
```

### 完整停掉

```bash
cd /Users/lyb/workspace/agent-learn/projects/it-ticket-agent
docker compose -f docker-compose.pgvector.yml down
```

主服务和 Agent 在前台终端里按 `Ctrl + C`。
