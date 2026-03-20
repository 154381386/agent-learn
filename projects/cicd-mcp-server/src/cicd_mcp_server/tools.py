from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable


Handler = Callable[[dict[str, Any]], dict[str, Any]]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class ToolSpec:
    name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    annotations: dict[str, Any]
    handler: Handler

    def as_mcp_tool(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": self.input_schema,
            "annotations": self.annotations,
        }


def _tool_response(text: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": payload,
        "isError": False,
    }


def _gitlab_get_pipeline(arguments: dict[str, Any]) -> dict[str, Any]:
    project = arguments.get("project", "order-service")
    pipeline_id = arguments.get("pipeline_id", 582341)
    payload = {
        "provider": "gitlab",
        "project": project,
        "pipeline_id": pipeline_id,
        "status": "failed",
        "branch": "release/2026.03.19",
        "commit_sha": "b7af21c9d31d",
        "triggered_by": "release-bot",
        "duration_sec": 913,
        "failed_stage": "deploy_prod",
        "failed_job": "helm-upgrade-order-service",
        "web_url": f"https://gitlab.example.internal/{project}/-/pipelines/{pipeline_id}",
        "updated_at": _utc_now(),
    }
    return _tool_response(
        f"GitLab pipeline {pipeline_id} for {project} failed at stage deploy_prod.",
        payload,
    )


def _gitlab_list_merge_requests(arguments: dict[str, Any]) -> dict[str, Any]:
    project = arguments.get("project", "order-service")
    state = arguments.get("state", "merged")
    payload = {
        "provider": "gitlab",
        "project": project,
        "state": state,
        "items": [
            {
                "iid": 1042,
                "title": "fix: tighten db pool for prod",
                "author": "alice",
                "merged_at": "2026-03-18T12:11:00Z",
                "labels": ["release", "prod-change"],
                "web_url": f"https://gitlab.example.internal/{project}/-/merge_requests/1042",
            },
            {
                "iid": 1041,
                "title": "feat: order-service add circuit breaker",
                "author": "bob",
                "merged_at": "2026-03-18T09:42:00Z",
                "labels": ["release"],
                "web_url": f"https://gitlab.example.internal/{project}/-/merge_requests/1041",
            },
        ],
    }
    return _tool_response(
        f"Found 2 recent merge requests for {project} in state {state}.",
        payload,
    )


def _gitlab_get_job_trace(arguments: dict[str, Any]) -> dict[str, Any]:
    project = arguments.get("project", "order-service")
    job_id = arguments.get("job_id", 991821)
    payload = {
        "provider": "gitlab",
        "project": project,
        "job_id": job_id,
        "trace_excerpt": [
            "Pulling chart version 2.4.18",
            "Running helm upgrade --install order-service ...",
            "Error: UPGRADE FAILED: deployment order-service exceeded its progress deadline",
            "Hint: check rollout status and pod crash loops",
        ],
        "updated_at": _utc_now(),
    }
    return _tool_response(
        f"Fetched mock trace excerpt for GitLab job {job_id} in {project}.",
        payload,
    )


def _jenkins_get_build(arguments: dict[str, Any]) -> dict[str, Any]:
    job_name = arguments.get("job_name", "order-service-prod-release")
    build_number = arguments.get("build_number", 1872)
    payload = {
        "provider": "jenkins",
        "job_name": job_name,
        "build_number": build_number,
        "status": "UNSTABLE",
        "result": "FAILURE",
        "branch": "release/2026.03.19",
        "started_at": "2026-03-19T02:15:00Z",
        "duration_sec": 1288,
        "url": f"https://jenkins.example.internal/job/{job_name}/{build_number}/",
        "artifacts": [
            "helm-values-prod.yaml",
            "deploy-summary.json",
        ],
    }
    return _tool_response(
        f"Jenkins build {job_name} #{build_number} finished with FAILURE.",
        payload,
    )


def _jenkins_get_console_log(arguments: dict[str, Any]) -> dict[str, Any]:
    job_name = arguments.get("job_name", "order-service-prod-release")
    build_number = arguments.get("build_number", 1872)
    payload = {
        "provider": "jenkins",
        "job_name": job_name,
        "build_number": build_number,
        "log_excerpt": [
            "[Deploy] kubectl rollout status deployment/order-service -n prod",
            "Waiting for deployment \"order-service\" rollout to finish: 2 old replicas are pending termination...",
            "error: deployment \"order-service\" exceeded its progress deadline",
            "[Post] Marking build as FAILURE",
        ],
        "updated_at": _utc_now(),
    }
    return _tool_response(
        f"Fetched mock Jenkins console log for {job_name} #{build_number}.",
        payload,
    )


def _cicd_get_deployment_status(arguments: dict[str, Any]) -> dict[str, Any]:
    service = arguments.get("service", "order-service")
    environment = arguments.get("environment", "prod-shanghai-1")
    payload = {
        "provider": "cicd",
        "service": service,
        "environment": environment,
        "current_revision": "release-2026.03.19.1",
        "previous_revision": "release-2026.03.18.2",
        "rollout_status": "degraded",
        "healthy_replicas": 3,
        "desired_replicas": 6,
        "active_alerts": ["5xx-rate-high", "pod-crashloop"],
        "last_successful_deploy_at": "2026-03-18T18:20:00Z",
        "updated_at": _utc_now(),
    }
    return _tool_response(
        f"Deployment status for {service} in {environment} is degraded.",
        payload,
    )


def _cicd_retry_pipeline(arguments: dict[str, Any]) -> dict[str, Any]:
    project = arguments.get("project", "order-service")
    pipeline_id = arguments.get("pipeline_id", 582341)
    payload = {
        "provider": "cicd",
        "operation": "retry_pipeline",
        "project": project,
        "source_pipeline_id": pipeline_id,
        "new_pipeline_id": 582399,
        "status": "accepted",
        "approval_required": True,
        "submitted_at": _utc_now(),
    }
    return _tool_response(
        f"Mock retry submitted for GitLab pipeline {pipeline_id} in {project}.",
        payload,
    )


def _cicd_rollback_release(arguments: dict[str, Any]) -> dict[str, Any]:
    service = arguments.get("service", "order-service")
    environment = arguments.get("environment", "prod-shanghai-1")
    target_revision = arguments.get("target_revision", "release-2026.03.18.2")
    reason = arguments.get("reason", "rollback requested")
    payload = {
        "provider": "cicd",
        "operation": "rollback_release",
        "service": service,
        "environment": environment,
        "target_revision": target_revision,
        "status": "pending_approval",
        "approval_required": True,
        "risk_level": "high",
        "reason": reason,
        "ticket_ref": f"RB-{service}-20260319-001",
        "submitted_at": _utc_now(),
    }
    return _tool_response(
        f"Mock rollback for {service} in {environment} is pending approval.",
        payload,
    )


def build_tool_registry() -> dict[str, ToolSpec]:
    return {
        "gitlab.get_pipeline": ToolSpec(
            name="gitlab.get_pipeline",
            title="Get GitLab Pipeline",
            description="Query a GitLab pipeline execution result for release diagnosis.",
            input_schema={
                "type": "object",
                "properties": {
                    "project": {"type": "string"},
                    "pipeline_id": {"type": "integer"},
                },
                "required": ["project", "pipeline_id"],
            },
            annotations={
                "read_only": True,
                "mutating": False,
                "sensitive": False,
                "requires_approval": False,
                "provider": "gitlab",
            },
            handler=_gitlab_get_pipeline,
        ),
        "gitlab.list_merge_requests": ToolSpec(
            name="gitlab.list_merge_requests",
            title="List GitLab Merge Requests",
            description="List recent merge requests to identify suspicious changes.",
            input_schema={
                "type": "object",
                "properties": {
                    "project": {"type": "string"},
                    "state": {"type": "string", "default": "merged"},
                },
                "required": ["project"],
            },
            annotations={
                "read_only": True,
                "mutating": False,
                "sensitive": False,
                "requires_approval": False,
                "provider": "gitlab",
            },
            handler=_gitlab_list_merge_requests,
        ),
        "gitlab.get_job_trace": ToolSpec(
            name="gitlab.get_job_trace",
            title="Get GitLab Job Trace",
            description="Fetch job trace excerpts for failed CI/CD jobs.",
            input_schema={
                "type": "object",
                "properties": {
                    "project": {"type": "string"},
                    "job_id": {"type": "integer"},
                },
                "required": ["project", "job_id"],
            },
            annotations={
                "read_only": True,
                "mutating": False,
                "sensitive": False,
                "requires_approval": False,
                "provider": "gitlab",
            },
            handler=_gitlab_get_job_trace,
        ),
        "jenkins.get_build": ToolSpec(
            name="jenkins.get_build",
            title="Get Jenkins Build",
            description="Query Jenkins build metadata for a release job.",
            input_schema={
                "type": "object",
                "properties": {
                    "job_name": {"type": "string"},
                    "build_number": {"type": "integer"},
                },
                "required": ["job_name", "build_number"],
            },
            annotations={
                "read_only": True,
                "mutating": False,
                "sensitive": False,
                "requires_approval": False,
                "provider": "jenkins",
            },
            handler=_jenkins_get_build,
        ),
        "jenkins.get_console_log": ToolSpec(
            name="jenkins.get_console_log",
            title="Get Jenkins Console Log",
            description="Fetch console log excerpts from a Jenkins build.",
            input_schema={
                "type": "object",
                "properties": {
                    "job_name": {"type": "string"},
                    "build_number": {"type": "integer"},
                },
                "required": ["job_name", "build_number"],
            },
            annotations={
                "read_only": True,
                "mutating": False,
                "sensitive": False,
                "requires_approval": False,
                "provider": "jenkins",
            },
            handler=_jenkins_get_console_log,
        ),
        "cicd.get_deployment_status": ToolSpec(
            name="cicd.get_deployment_status",
            title="Get Deployment Status",
            description="Query current deployment health and active rollout status.",
            input_schema={
                "type": "object",
                "properties": {
                    "service": {"type": "string"},
                    "environment": {"type": "string"},
                },
                "required": ["service", "environment"],
            },
            annotations={
                "read_only": True,
                "mutating": False,
                "sensitive": False,
                "requires_approval": False,
                "provider": "cicd",
            },
            handler=_cicd_get_deployment_status,
        ),
        "cicd.retry_pipeline": ToolSpec(
            name="cicd.retry_pipeline",
            title="Retry Pipeline",
            description="Retry a failed pipeline execution. This is a mock mutating action.",
            input_schema={
                "type": "object",
                "properties": {
                    "project": {"type": "string"},
                    "pipeline_id": {"type": "integer"},
                },
                "required": ["project", "pipeline_id"],
            },
            annotations={
                "read_only": False,
                "mutating": True,
                "sensitive": True,
                "requires_approval": True,
                "provider": "cicd",
            },
            handler=_cicd_retry_pipeline,
        ),
        "cicd.rollback_release": ToolSpec(
            name="cicd.rollback_release",
            title="Rollback Release",
            description="Rollback a deployment to a previous revision. This is a mock high-risk action.",
            input_schema={
                "type": "object",
                "properties": {
                    "service": {"type": "string"},
                    "environment": {"type": "string"},
                    "target_revision": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["service", "environment", "target_revision", "reason"],
            },
            annotations={
                "read_only": False,
                "mutating": True,
                "sensitive": True,
                "requires_approval": True,
                "provider": "cicd",
            },
            handler=_cicd_rollback_release,
        ),
    }
