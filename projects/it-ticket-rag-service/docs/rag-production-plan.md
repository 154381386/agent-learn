# RAG Production Plan

## 目标

把当前项目升级为真正的外部向量数据库方案，并支持增量文档向量化。
当前版本同时将 RAG 从编排服务中拆出，作为独立 HTTP 微服务运行。

## 方案选择

选择 `PostgreSQL + pgvector`，原因：
- 最容易部署
- 与现有系统最容易集成
- 对中小规模知识库足够生产可用
- 天然适合 checksum 驱动的增量 upsert

## 已实现

### 存储层
- 新增 `pgvector_store.py`
- 文档表：保存 path、checksum、embedding_model、chunking_signature
- chunk 表：保存文本块与向量
- HNSW 向量索引

### 检索层
- sparse recall
- pgvector dense recall
- RRF 候选融合
- rerank
- MMR 去冗余

### 增量索引
- 新增/变更文档：重新切块并 upsert
- 未变化文档：跳过
- 删除文档：删除对应向量数据
- 保持“别的不动”

### 控制面
- 由独立 `rag-service` 提供
- `POST /api/v1/rag/sync`：增量同步
- `POST /api/v1/rag/reindex`：全量重建
- `GET /api/v1/rag/status`

## 下一步建议

1. 给 pgvector 表增加租户/环境维度
2. 把 chunk 文本与元数据拆分冷热存储
3. 加入召回评测数据集
4. 给同步接口加鉴权与审计日志
