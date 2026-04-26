from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .service_names import infer_service_name


IPV4_PATTERN = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")
HOST_KEYWORDS = ("机器", "主机", "服务器", "host", "ecs", "vm", "实例")
DATABASE_KEYWORDS = ("数据库", "database", "db", "mysql", "postgres", "redis")
ENVIRONMENT_ALIASES = (
    ("prod", ("生产环境", "生产", "线上", "prod", "production", "prod-")),
    ("staging", ("预发环境", "预发", "灰度", "staging", "stage", "preprod")),
    ("test", ("测试环境", "测试", "test", "testing")),
    ("dev", ("开发环境", "开发", "dev", "development")),
)

SERVICE_CONTEXT_REGISTRY = {
    "order-service": {"environment": "prod", "cluster": "prod-shanghai-1", "namespace": "default"},
    "支付服务": {"environment": "prod", "cluster": "prod-shanghai-1", "namespace": "default"},
    "车云服务": {"environment": "prod", "cluster": "prod-shanghai-1", "namespace": "default"},
}


@dataclass
class SlotField:
    name: str
    label: str
    description: str
    required: bool = True
    inferred_value: str = ""
    source: str = ""


@dataclass
class SlotResolution:
    issue_type: str
    resolved: dict[str, Any] = field(default_factory=dict)
    missing_fields: list[SlotField] = field(default_factory=list)
    inferred_fields: list[SlotField] = field(default_factory=list)
    allow_generic_guidance: bool = True

    @property
    def needs_clarification(self) -> bool:
        return bool(self.missing_fields or self.inferred_fields)



def infer_environment(message: str | None) -> str:
    text = str(message or "").lower()
    for environment, aliases in ENVIRONMENT_ALIASES:
        if any(alias in text for alias in aliases):
            return environment
    return ""


def infer_host_identifier(message: str | None) -> str:
    text = str(message or "")
    matched = IPV4_PATTERN.search(text)
    return matched.group(0) if matched else ""


def infer_db_type(message: str | None) -> str:
    lowered = str(message or "").lower()
    if "mysql" in lowered:
        return "mysql"
    if "postgres" in lowered or "pgsql" in lowered:
        return "postgres"
    if "redis" in lowered:
        return "redis"
    return ""


def infer_db_name(message: str | None) -> str:
    text = str(message or "")
    patterns = [
        re.compile(r"([A-Za-z0-9_-]+)\s*(?:数据库|db|database)", re.IGNORECASE),
        re.compile(r"(?:数据库|db|database)\s*([A-Za-z0-9_-]+)", re.IGNORECASE),
    ]
    for pattern in patterns:
        matched = pattern.search(text)
        if matched:
            return str(matched.group(1) or "")
    return ""


def resolve_slots(
    *,
    message: str,
    service: str | None,
    environment: str | None,
    cluster: str | None,
    namespace: str | None,
    host_identifier: str | None,
    db_name: str | None,
    db_type: str | None,
) -> SlotResolution:
    lowered = str(message or "").lower()
    resolved_service = str(service or infer_service_name(message) or "")
    resolved_host = str(host_identifier or infer_host_identifier(message) or "")
    resolved_db_type = str(db_type or infer_db_type(message) or "")
    resolved_db_name = str(db_name or infer_db_name(message) or "")
    resolved_environment = str(environment or infer_environment(message) or "").strip()
    resolved_cluster = str(cluster or "").strip()
    resolved_namespace = str(namespace or "").strip()

    is_host_issue = bool(resolved_host) or any(keyword in lowered for keyword in HOST_KEYWORDS)
    has_db_signal = bool(resolved_db_name or resolved_db_type) or any(keyword in lowered for keyword in DATABASE_KEYWORDS)
    is_db_issue = has_db_signal and not bool(resolved_service)
    issue_type = "host" if is_host_issue else "database" if is_db_issue else "service"

    inferred_fields: list[SlotField] = []
    if resolved_service:
        registry = SERVICE_CONTEXT_REGISTRY.get(resolved_service)
        if registry:
            if not resolved_environment and registry.get("environment"):
                resolved_environment = str(registry["environment"])
                inferred_fields.append(
                    SlotField(
                        name="environment",
                        label="环境",
                        description="请确认环境，或直接覆盖为正确值",
                        inferred_value=resolved_environment,
                        source="cmdb",
                    )
                )
            if not resolved_cluster and registry.get("cluster"):
                resolved_cluster = str(registry["cluster"])
                inferred_fields.append(
                    SlotField(
                        name="cluster",
                        label="集群",
                        description="请确认集群，或直接覆盖为正确值",
                        inferred_value=resolved_cluster,
                        source="cmdb",
                    )
                )
            if not resolved_namespace and registry.get("namespace"):
                resolved_namespace = str(registry["namespace"])
                inferred_fields.append(
                    SlotField(
                        name="namespace",
                        label="命名空间",
                        description="请确认命名空间，或直接覆盖为正确值",
                        inferred_value=resolved_namespace,
                        source="cmdb",
                    )
                )

    missing_fields: list[SlotField] = []
    if issue_type == "host":
        if not resolved_host:
            missing_fields.append(SlotField("host_identifier", "机器标识", "请提供机器 IP、实例 ID 或主机名"))
        if not resolved_environment:
            missing_fields.append(SlotField("environment", "环境", "请提供环境，例如 prod、staging、test"))
    elif issue_type == "database":
        if not resolved_db_name:
            missing_fields.append(SlotField("db_name", "数据库名", "请提供数据库名"))
        if not resolved_db_type:
            missing_fields.append(SlotField("db_type", "数据库类型", "请提供数据库类型，例如 mysql、postgres、redis"))
        if not resolved_environment:
            missing_fields.append(SlotField("environment", "环境", "请提供环境，例如 prod、staging、test"))
    else:
        if not resolved_service:
            missing_fields.append(SlotField("service", "服务名", "请提供标准服务名，例如 order-service"))
        if not resolved_environment:
            missing_fields.append(SlotField("environment", "环境", "请提供环境，例如 prod、staging、test"))
        if not resolved_cluster:
            missing_fields.append(SlotField("cluster", "集群", "请提供集群，例如 prod-shanghai-1"))

    return SlotResolution(
        issue_type=issue_type,
        resolved={
            "service": resolved_service,
            "environment": resolved_environment,
            "cluster": resolved_cluster,
            "namespace": resolved_namespace,
            "host_identifier": resolved_host,
            "db_name": resolved_db_name,
            "db_type": resolved_db_type,
        },
        missing_fields=missing_fields,
        inferred_fields=inferred_fields,
        allow_generic_guidance=True,
    )
