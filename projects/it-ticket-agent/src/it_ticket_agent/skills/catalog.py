from __future__ import annotations

from ..state.models import SkillCategory, SkillSignature


SKILL_CATEGORIES: tuple[SkillCategory, ...] = (
    SkillCategory(
        name="k8s",
        description="Kubernetes 集群与工作负载诊断",
        skill_count=5,
        match_keywords=["pod", "k8s", "kubernetes", "重启", "oom", "crashloop", "副本", "deployment"],
    ),
    SkillCategory(
        name="cicd",
        description="发布、流水线与变更诊断",
        skill_count=3,
        match_keywords=["发布", "deploy", "pipeline", "回滚", "变更", "release", "构建"],
    ),
    SkillCategory(
        name="network",
        description="流量、网关、DNS 与链路排查",
        skill_count=3,
        match_keywords=["502", "503", "504", "ingress", "dns", "network", "超时", "延迟", "网关"],
    ),
    SkillCategory(
        name="db",
        description="数据库健康与复制诊断",
        skill_count=2,
        match_keywords=["db", "database", "mysql", "postgres", "慢查询", "连接池", "复制"],
    ),
    SkillCategory(
        name="monitor",
        description="日志、告警与 SLO 观测",
        skill_count=3,
        match_keywords=["日志", "告警", "error", "errors", "slo", "监控", "报警", "异常"],
    ),
)


SKILL_SIGNATURES: tuple[SkillSignature, ...] = (
    SkillSignature(name="check_pod_health", params="(service)", description="Pod 健康综合检查", risk_level="low", category="k8s"),
    SkillSignature(name="check_memory_trend", params="(service, window)", description="内存趋势分析", risk_level="low", category="k8s"),
    SkillSignature(name="check_resource_limits", params="(service)", description="资源配额与实际用量对比", risk_level="low", category="k8s"),
    SkillSignature(name="restart_pods", params="(service)", description="重启 Pod", risk_level="high", category="k8s"),
    SkillSignature(name="scale_replicas", params="(service, count)", description="扩缩容副本", risk_level="medium", category="k8s"),
    SkillSignature(name="check_recent_deploys", params="(service)", description="最近部署变更检查", risk_level="low", category="cicd"),
    SkillSignature(name="check_pipeline_status", params="(project, branch)", description="流水线状态检查", risk_level="low", category="cicd"),
    SkillSignature(name="rollback_deploy", params="(service, version)", description="回滚部署", risk_level="high", category="cicd"),
    SkillSignature(name="check_network_latency", params="(service, upstream)", description="链路延迟分析", risk_level="low", category="network"),
    SkillSignature(name="check_dns_resolution", params="(domain)", description="DNS 解析检查", risk_level="low", category="network"),
    SkillSignature(name="check_ingress_rules", params="(service)", description="Ingress 配置检查", risk_level="low", category="network"),
    SkillSignature(name="check_db_health", params="(service)", description="数据库连接池与慢查询检查", risk_level="low", category="db"),
    SkillSignature(name="check_replication_lag", params="(instance)", description="主从复制延迟检查", risk_level="low", category="db"),
    SkillSignature(name="check_log_errors", params="(service, window)", description="错误日志聚合分析", risk_level="low", category="monitor"),
    SkillSignature(name="check_alert_history", params="(service)", description="告警历史查询", risk_level="low", category="monitor"),
    SkillSignature(name="check_slo_status", params="(service)", description="SLO 达标状态检查", risk_level="low", category="monitor"),
)
