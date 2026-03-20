# CICD MCP Server

这是一个可独立启动的 **Mock MCP Server**，用于给 `CICD Agent` 提供首批 CI/CD 领域能力。

当前实现目标：

- 独立项目，位于 `projects/cicd-mcp-server`
- 可直接本地启动访问
- 暴露 MCP 风格的 `tools/list`、`tools/call` 能力
- 提供 Jenkins / GitLab / Deployment 相关 mock 响应
- 支持只读查询和高风险变更动作的模拟返回

## 项目结构

```text
projects/cicd-mcp-server/
├── README.md
├── Makefile
├── pyproject.toml
├── scripts/
│   └── dev.sh
└── src/cicd_mcp_server/
    ├── __init__.py
    ├── __main__.py
    ├── cli.py
    ├── protocol.py
    ├── server.py
    └── tools.py
```

## 启动

不安装依赖也可以直接运行：

```bash
cd projects/cicd-mcp-server
PYTHONPATH=src python3 -m cicd_mcp_server --host 0.0.0.0 --port 8900
```

或使用：

```bash
cd projects/cicd-mcp-server
make run
```

## 健康检查

```bash
curl http://localhost:8900/healthz
```

## 浏览工具列表

```bash
curl http://localhost:8900/api/v1/tools | python3 -m json.tool
```

## MCP 初始化

```bash
curl -s http://localhost:8900/mcp \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
      "protocolVersion": "2025-03-26",
      "clientInfo": {"name": "demo-client", "version": "0.1.0"}
    }
  }' | python3 -m json.tool
```

## 列出 MCP 工具

```bash
curl -s http://localhost:8900/mcp \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/list",
    "params": {}
  }' | python3 -m json.tool
```

## 调用示例：查询 GitLab Pipeline

```bash
curl -s http://localhost:8900/mcp \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0",
    "id": 3,
    "method": "tools/call",
    "params": {
      "name": "gitlab.get_pipeline",
      "arguments": {
        "project": "order-service",
        "pipeline_id": 582341
      }
    }
  }' | python3 -m json.tool
```

## 调用示例：模拟回滚

```bash
curl -s http://localhost:8900/mcp \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0",
    "id": 4,
    "method": "tools/call",
    "params": {
      "name": "cicd.rollback_release",
      "arguments": {
        "service": "order-service",
        "environment": "prod-shanghai-1",
        "target_revision": "release-2026.03.18.2",
        "reason": "5xx 升高，先回滚止血"
      }
    }
  }' | python3 -m json.tool
```

## 当前已提供能力

- `gitlab.get_pipeline`
- `gitlab.list_merge_requests`
- `gitlab.get_job_trace`
- `jenkins.get_build`
- `jenkins.get_console_log`
- `cicd.get_deployment_status`
- `cicd.retry_pipeline`
- `cicd.rollback_release`

## 说明

- 这是一个用于联调和架构验证的 mock 版本，返回的是结构化假数据。
- 已保留 `read_only` / `mutating` / `requires_approval` 等治理元数据，便于后续接到真实平台。
- 当前实现的是轻量 HTTP + JSON-RPC 风格的 MCP 接口，足够支撑 `CICD Agent` 早期联调。
