from __future__ import annotations

from ..settings import Settings
from ..tools.db import (
    InspectConnectionPoolTool,
    InspectDBInstanceHealthTool,
    InspectReplicationStatusTool,
    InspectSchemaChangeRecordsTool,
    InspectSlowQueriesTool,
)
from .local_tool_agent import LocalToolDomainAgent


class DBAgent(LocalToolDomainAgent):
    name = "db_agent"
    domain = "db"
    system_prompt = (
        "你是企业内部的 DB Agent。"
        "你负责排查数据库实例健康、复制延迟、慢查询、连接池和 schema 变更问题。"
        "请优先通过本地工具获取事实，再输出 JSON。"
    )
    fallback_summary = "{service} 工单已进入数据库诊断，建议先检查实例健康、复制状态、慢查询和连接池。"
    local_tool_note = "DB Agent 当前不连接外部系统，统一通过本地 tools 与 JSON mock/fallback 响应提供诊断事实。"
    domain_keywords = ("数据库", "db", "mysql", "postgres", "replication", "慢查询", "连接池")

    def __init__(self, settings: Settings) -> None:
        super().__init__(
            settings,
            tools=[
                InspectDBInstanceHealthTool(),
                InspectReplicationStatusTool(),
                InspectSlowQueriesTool(),
                InspectConnectionPoolTool(),
                InspectSchemaChangeRecordsTool(),
            ],
        )
