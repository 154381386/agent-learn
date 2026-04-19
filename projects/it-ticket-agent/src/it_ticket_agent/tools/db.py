from __future__ import annotations

from pathlib import Path

from ..runtime.contracts import TaskEnvelope
from .contracts import ReadOnlyTool, ToolExecutionResult
from .mock_helpers import build_context, match_any, resolve_profile_mock


DEFAULT_MOCK_PROFILES_PATH = Path(__file__).resolve().parents[3] / "data" / "mock_db_profiles.json"
ENV_VAR = "IT_TICKET_AGENT_MOCK_DB_PROFILES_PATH"


class InspectDBInstanceHealthTool(ReadOnlyTool):
    retryable = True
    timeout_sec = 15
    name = "inspect_db_instance_health"
    summary = "Inspect database instance health and role status"
    input_schema = {"type": "object", "properties": {"service": {"type": "string"}}}
    retryable = True

    async def run(self, task: TaskEnvelope, arguments: dict | None = None) -> ToolExecutionResult:
        mocked = resolve_profile_mock(task, self.name, DEFAULT_MOCK_PROFILES_PATH, ENV_VAR, arguments)
        if mocked is not None:
            return mocked
        ctx = build_context(task, arguments)
        health = "healthy"
        if match_any(ctx["message"], ["数据库", "db", "mysql", "postgres", "实例异常"]):
            health = "degraded"
        return ToolExecutionResult(
            tool_name=self.name,
            status="completed",
            summary="已汇总数据库实例健康状态。",
            payload={"service": ctx["service"], "db_health": health},
            evidence=[f"db_health={health}"],
        )


class InspectReplicationStatusTool(ReadOnlyTool):
    retryable = True
    timeout_sec = 15
    name = "inspect_replication_status"
    summary = "Inspect primary-replica replication lag and sync status"
    input_schema = {"type": "object", "properties": {"service": {"type": "string"}}}
    retryable = True

    async def run(self, task: TaskEnvelope, arguments: dict | None = None) -> ToolExecutionResult:
        mocked = resolve_profile_mock(task, self.name, DEFAULT_MOCK_PROFILES_PATH, ENV_VAR, arguments)
        if mocked is not None:
            return mocked
        ctx = build_context(task, arguments)
        lag_seconds = 0
        if match_any(ctx["message"], ["复制", "replication", "主从", "lag"]):
            lag_seconds = 38
        return ToolExecutionResult(
            tool_name=self.name,
            status="completed",
            summary="已汇总复制状态。",
            payload={"service": ctx["service"], "lag_seconds": lag_seconds},
            evidence=[f"replication_lag={lag_seconds}s"],
        )


class InspectSlowQueriesTool(ReadOnlyTool):
    retryable = True
    timeout_sec = 15
    name = "inspect_slow_queries"
    summary = "Inspect slow query signals and top offenders"
    input_schema = {"type": "object", "properties": {"service": {"type": "string"}}}
    retryable = True

    async def run(self, task: TaskEnvelope, arguments: dict | None = None) -> ToolExecutionResult:
        mocked = resolve_profile_mock(task, self.name, DEFAULT_MOCK_PROFILES_PATH, ENV_VAR, arguments)
        if mocked is not None:
            return mocked
        ctx = build_context(task, arguments)
        count = 0
        if match_any(ctx["message"], ["慢查询", "slow query", "sql", "查询慢"]):
            count = 3
        return ToolExecutionResult(
            tool_name=self.name,
            status="completed",
            summary="已汇总慢查询信息。",
            payload={"service": ctx["service"], "slow_query_count": count},
            evidence=[f"slow_queries={count}"],
        )


class InspectConnectionPoolTool(ReadOnlyTool):
    retryable = True
    timeout_sec = 15
    name = "inspect_connection_pool"
    summary = "Inspect connection pool saturation and timeout signals"
    input_schema = {"type": "object", "properties": {"service": {"type": "string"}}}
    retryable = True

    async def run(self, task: TaskEnvelope, arguments: dict | None = None) -> ToolExecutionResult:
        mocked = resolve_profile_mock(task, self.name, DEFAULT_MOCK_PROFILES_PATH, ENV_VAR, arguments)
        if mocked is not None:
            return mocked
        ctx = build_context(task, arguments)
        pool_state = "healthy"
        if match_any(ctx["message"], ["连接池", "pool", "too many connections", "timeout"]):
            pool_state = "saturated"
        return ToolExecutionResult(
            tool_name=self.name,
            status="completed",
            summary="已汇总连接池状态。",
            payload={"service": ctx["service"], "pool_state": pool_state},
            evidence=[f"pool={pool_state}"],
        )


class InspectSchemaChangeRecordsTool(ReadOnlyTool):
    name = "inspect_schema_change_records"
    summary = "Inspect recent schema change records and migration status"
    input_schema = {"type": "object", "properties": {"service": {"type": "string"}}}
    retryable = True

    async def run(self, task: TaskEnvelope, arguments: dict | None = None) -> ToolExecutionResult:
        mocked = resolve_profile_mock(task, self.name, DEFAULT_MOCK_PROFILES_PATH, ENV_VAR, arguments)
        if mocked is not None:
            return mocked
        ctx = build_context(task, arguments)
        change_count = 0
        if match_any(ctx["message"], ["schema", "migration", "ddl", "变更"]):
            change_count = 1
        return ToolExecutionResult(
            tool_name=self.name,
            status="completed",
            summary="已汇总 schema 变更记录。",
            payload={"service": ctx["service"], "schema_change_count": change_count},
            evidence=[f"schema_changes={change_count}"],
        )


class InspectDeadlockSignalsTool(ReadOnlyTool):
    retryable = True
    timeout_sec = 15
    name = "inspect_deadlock_signals"
    summary = "Inspect database deadlock and lock wait signals"
    input_schema = {"type": "object", "properties": {"service": {"type": "string"}}}
    retryable = True

    async def run(self, task: TaskEnvelope, arguments: dict | None = None) -> ToolExecutionResult:
        mocked = resolve_profile_mock(task, self.name, DEFAULT_MOCK_PROFILES_PATH, ENV_VAR, arguments)
        if mocked is not None:
            return mocked
        ctx = build_context(task, arguments)
        deadlock_count = 0
        if match_any(ctx["message"], ["deadlock", "锁等待", "行锁", "数据库超时"]):
            deadlock_count = 2
        return ToolExecutionResult(
            tool_name=self.name,
            status="completed",
            summary="已汇总死锁信号。",
            payload={"service": ctx["service"], "deadlock_count": deadlock_count},
            evidence=[f"deadlocks={deadlock_count}"],
        )


class InspectTransactionRollbackRateTool(ReadOnlyTool):
    retryable = True
    timeout_sec = 15
    name = "inspect_transaction_rollback_rate"
    summary = "Inspect transaction rollback rate and database abort ratio"
    input_schema = {"type": "object", "properties": {"service": {"type": "string"}}}
    retryable = True

    async def run(self, task: TaskEnvelope, arguments: dict | None = None) -> ToolExecutionResult:
        mocked = resolve_profile_mock(task, self.name, DEFAULT_MOCK_PROFILES_PATH, ENV_VAR, arguments)
        if mocked is not None:
            return mocked
        ctx = build_context(task, arguments)
        rollback_rate = 0.0
        if match_any(ctx["message"], ["rollback", "事务回滚", "数据库超时", "写失败"]):
            rollback_rate = 0.18
        return ToolExecutionResult(
            tool_name=self.name,
            status="completed",
            summary="已汇总事务回滚率。",
            payload={"service": ctx["service"], "rollback_rate": rollback_rate},
            evidence=[f"rollback_rate={rollback_rate}"],
        )
