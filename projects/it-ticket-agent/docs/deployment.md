# Deployment Guide

这份文档说明 `projects/it-ticket-agent` 的部署方式。
当前项目是 **编排服务（orchestrator）**，依赖外部：
- `projects/it-ticket-rag-service` 提供的 RAG HTTP 服务
- 诊断 Agent Runtime（默认 `:8101`）

## 1. 部署目标

默认部署结果：
- Orchestrator：`http://<host>:8000`
- RAG Service：`http://<host>:8200`
- Agent Runtime：`http://<host>:8101`

## 2. 前置依赖

- Python `3.11+`
- `uv` 或可用的 `pip`
- 可访问的 LLM API
- 已部署好的 RAG 服务
- 已部署好的 Agent Runtime

## 3. 目录准备

```bash
cd /srv/agent-learn/projects/it-ticket-agent
python3 -m venv .venv
./.venv/bin/pip install -e .
```

## 4. 环境变量

建议在项目根目录放置 `.env`：

```bash
APP_ENV=prod
HOST=0.0.0.0
PORT=8000
APPROVAL_DB_PATH=./data/approvals.db
LANGGRAPH_CHECKPOINT_DB=./data/langgraph.db

AGENT_TRANSPORT=http
POD_AGENT_URL=http://127.0.0.1:8101/api/v1/agents/pod-analysis/run
RCA_AGENT_URL=http://127.0.0.1:8101/api/v1/agents/root-cause/run
NETWORK_AGENT_URL=http://127.0.0.1:8101/api/v1/agents/network-diagnosis/run

LLM_BASE_URL=https://your-openai-compatible-endpoint/v1
LLM_API_KEY=your-chat-api-key
LLM_MODEL=gpt-5.4
LLM_TIMEOUT_SEC=30
LLM_TEMPERATURE=0.2

RAG_ENABLED=true
RAG_SERVICE_BASE_URL=http://127.0.0.1:8200
RAG_SERVICE_TIMEOUT_SEC=30
```

## 5. 启动方式

### 5.1 前台启动

```bash
cd /srv/agent-learn/projects/it-ticket-agent
./.venv/bin/uvicorn it_ticket_agent.main:app --host 0.0.0.0 --port 8000
```

### 5.2 systemd 启动示例

`/etc/systemd/system/it-ticket-orchestrator.service`

```ini
[Unit]
Description=IT Ticket Orchestrator
After=network.target

[Service]
Type=simple
WorkingDirectory=/srv/agent-learn/projects/it-ticket-agent
ExecStart=/srv/agent-learn/projects/it-ticket-agent/.venv/bin/uvicorn it_ticket_agent.main:app --host 0.0.0.0 --port 8000
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
sudo systemctl enable --now it-ticket-orchestrator
sudo systemctl status it-ticket-orchestrator
```

## 6. 健康检查

```bash
curl http://127.0.0.1:8000/healthz
curl http://127.0.0.1:8200/healthz
curl http://127.0.0.1:8101/healthz
```

工单链路验证：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/tickets \
  -H 'Content-Type: application/json' \
  -d '{
    "ticket_id": "INC-DEPLOY-001",
    "user_id": "deploy-check",
    "message": "order-service 最近一直重启，帮我看下",
    "service": "order-service"
  }'
```

## 7. 升级步骤

```bash
cd /srv/agent-learn
git pull
cd projects/it-ticket-agent
./.venv/bin/pip install -e .
sudo systemctl restart it-ticket-orchestrator
```

## 8. 回滚建议

- 保留上一个稳定 commit
- 回滚代码后重启服务
- 如果仅 RAG 侧异常，优先回滚 `it-ticket-rag-service`

## 9. 反向代理建议

如果需要对外暴露，建议使用 Nginx/Ingress：
- 对用户开放 `:8000`
- `:8200` 和 `:8101` 建议仅内网开放
