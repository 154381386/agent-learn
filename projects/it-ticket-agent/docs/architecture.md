# Architecture

## 生产方案选择

### 编排层
- 采用 `LangGraph` 作为中心化 orchestrator
- 每张工单使用一个 `thread_id`
- 通过 checkpointer 保证中断审批后可恢复

### 外部向量数据库
- 推荐：`PostgreSQL + pgvector`
- Dense recall 存储在 PostgreSQL 外部库中
- Sparse recall 继续由应用层 BM25-like 检索完成
- Rerank 使用 DashScope `qwen3-rerank`
- Final ranking 使用 `MMR`

### 增量索引策略
- 文档按 `path + checksum + chunking_signature + embedding_model` 判断是否需要重建
- 未变更文档跳过
- 变更文档只覆盖自己的 chunks
- 已删除文档从向量库删除
- 其它文档完全不动

### 模型职责分离
- `LLM_*`：继续负责聊天、路由与最终回复
- `EMBEDDING_*`：负责向量召回
- `RERANK_*`：负责候选重排

## LangGraph 节点

```text
START
  → normalize_ticket
  → retrieve_knowledge
  → [direct_answer?] yes → finalize_response → END
  → route_agents
  → diagnose_parallel
  → fuse_diagnosis
  → [needs_approval?] yes → approval_gate (interrupt)
  → execute_action
  → finalize_response
  → END
```
