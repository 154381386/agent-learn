# Architecture

## 服务拆分

当前已经拆成两个独立项目：

- `projects/it-ticket-agent`：编排服务 / Agent 主体
- `projects/it-ticket-rag-service`：RAG 微服务

## Orchestrator 职责

- `LangGraph` 编排
- 工单归一化
- 调用 RAG 服务
- Agent 路由与并行诊断
- 审批中断与恢复
- 动作执行与最终回复

## RAG Service 职责

- 文档扫描与切块
- 增量索引
- sparse + dense hybrid retrieval
- rerank + MMR
- 对外提供 `/api/v1/rag/*`

## 调用链

```text
User
  -> orchestrator (:8000)
  -> rag-service (:8200)
  -> sample-agents (:8101)
  -> orchestrator
  -> User
```
