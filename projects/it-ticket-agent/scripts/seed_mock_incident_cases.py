from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from typing import Iterable

from it_ticket_agent.memory.models import IncidentCase
from it_ticket_agent.memory.pg_store import PostgresProcessMemoryStoreV2
from it_ticket_agent.memory_store import IncidentCaseStore
from it_ticket_agent.settings import Settings


def iso_hours_ago(hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def build_cases() -> list[IncidentCase]:
    return [
        IncidentCase(
            case_id="case-order-oom-001",
            session_id="seed-order-oom-001",
            thread_id="seed-order-oom-001",
            ticket_id="SEED-ORDER-OOM-001",
            service="order-service",
            cluster="prod-shanghai-1",
            namespace="default",
            current_agent="hypothesis_graph",
            symptom="order service 为什么总是超时，最近伴随 pod 重启和延迟抬升",
            root_cause="Pod 健康异常或资源不足导致服务不稳定",
            key_evidence=[
                "pod order-service-7f8da OOMKilled",
                "java.lang.OutOfMemoryError: Java heap space",
                "错误预算 burn rate 持续高于 3.0",
            ],
            final_action="restart_pods",
            approval_required=True,
            verification_passed=True,
            human_verified=True,
            hypothesis_accuracy={"H1": 0.12, "H2": 0.18, "H3": 1.0},
            actual_root_cause_hypothesis="H3",
            selected_hypothesis_id="H3",
            selected_ranker_features={
                "evidence_strength": 1.0,
                "confidence": 0.82,
                "history_match": 0.36,
            },
            final_conclusion="订单服务超时由 JVM 堆内存打满导致，重启后恢复，但根因仍需通过限额调整与内存优化修复。",
            created_at=iso_hours_ago(96),
            updated_at=iso_hours_ago(95),
            closed_at=iso_hours_ago(95),
        ),
        IncidentCase(
            case_id="case-order-network-001",
            session_id="seed-order-network-001",
            thread_id="seed-order-network-001",
            ticket_id="SEED-ORDER-NET-001",
            service="order-service",
            cluster="prod-shanghai-1",
            namespace="default",
            current_agent="hypothesis_graph",
            symptom="order service 高峰期出现大量 timeout，但 pod 与日志基本正常",
            root_cause="入口流量、Ingress 或上游网络链路异常导致 5xx / timeout",
            key_evidence=[
                "upstream dependency timeout ratio 0.31",
                "LB unhealthy backends 持续波动",
                "VPC connectivity intermittent timeout observed",
            ],
            final_action="observe_service",
            approval_required=False,
            verification_passed=True,
            human_verified=True,
            hypothesis_accuracy={"H1": 0.92, "H2": 0.24, "H3": 0.08},
            actual_root_cause_hypothesis="H1",
            selected_hypothesis_id="H1",
            selected_ranker_features={
                "evidence_strength": 0.88,
                "confidence": 0.79,
                "history_match": 0.41,
            },
            final_conclusion="订单服务超时主要由入口链路抖动和上游依赖波动引起，并非应用内存问题。",
            created_at=iso_hours_ago(88),
            updated_at=iso_hours_ago(87),
            closed_at=iso_hours_ago(87),
        ),
        IncidentCase(
            case_id="case-order-db-001",
            session_id="seed-order-db-001",
            thread_id="seed-order-db-001",
            ticket_id="SEED-ORDER-DB-001",
            service="order-service",
            cluster="prod-shanghai-1",
            namespace="default",
            current_agent="hypothesis_graph",
            symptom="order service 请求变慢，数据库连接池打满，慢查询升高",
            root_cause="数据库连接池或慢查询异常拖慢请求处理",
            key_evidence=[
                "connection pool usage 97%",
                "slow query count 14",
                "transaction rollback rate 0.17",
            ],
            final_action="observe_service",
            approval_required=False,
            verification_passed=True,
            human_verified=True,
            hypothesis_accuracy={"H1": 0.21, "H2": 0.95},
            actual_root_cause_hypothesis="H2",
            selected_hypothesis_id="H2",
            selected_ranker_features={
                "evidence_strength": 0.91,
                "confidence": 0.76,
                "history_match": 0.48,
            },
            final_conclusion="订单服务性能下降由数据库连接池耗尽和慢查询放大引起。",
            created_at=iso_hours_ago(80),
            updated_at=iso_hours_ago(79),
            closed_at=iso_hours_ago(79),
        ),
        IncidentCase(
            case_id="case-checkout-deploy-001",
            session_id="seed-checkout-deploy-001",
            thread_id="seed-checkout-deploy-001",
            ticket_id="SEED-CHECKOUT-DEPLOY-001",
            service="checkout-service",
            cluster="prod-shanghai-1",
            namespace="default",
            current_agent="hypothesis_graph",
            symptom="checkout-service 发布后 5xx 激增",
            root_cause="最近发布或流水线变更导致服务回归",
            key_evidence=[
                "pipeline deploy-prod failed",
                "rollback history 命中最近稳定版本",
                "变更窗口与故障开始时间重合",
            ],
            final_action="cicd.rollback_release",
            approval_required=True,
            verification_passed=True,
            human_verified=True,
            hypothesis_accuracy={"H1": 1.0, "H2": 0.2},
            actual_root_cause_hypothesis="H1",
            selected_hypothesis_id="H1",
            selected_ranker_features={
                "evidence_strength": 0.97,
                "confidence": 0.84,
                "history_match": 0.39,
            },
            final_conclusion="checkout-service 事故由最近发布引入的回归导致，回滚后恢复。",
            created_at=iso_hours_ago(72),
            updated_at=iso_hours_ago(71),
            closed_at=iso_hours_ago(71),
        ),
        IncidentCase(
            case_id="case-checkout-false-positive-001",
            session_id="seed-checkout-false-positive-001",
            thread_id="seed-checkout-false-positive-001",
            ticket_id="SEED-CHECKOUT-FP-001",
            service="checkout-service",
            cluster="prod-shanghai-1",
            namespace="default",
            current_agent="hypothesis_graph",
            symptom="checkout-service 出现 timeout，但后续确认为上游支付依赖抖动",
            root_cause="入口流量、Ingress 或上游网络链路异常导致 5xx / timeout",
            key_evidence=[
                "gateway upstream unhealthy",
                "upstream dependency degraded",
                "应用自身日志无显著 error pattern",
            ],
            final_action="observe_service",
            approval_required=False,
            verification_passed=True,
            human_verified=True,
            hypothesis_accuracy={"H1": 0.35, "H2": 0.91, "H3": 0.1},
            actual_root_cause_hypothesis="H2",
            selected_hypothesis_id="H1",
            selected_ranker_features={
                "evidence_strength": 0.44,
                "confidence": 0.72,
                "history_match": 0.12,
            },
            final_conclusion="最初错误地倾向发布回归，人工确认后修正为上游依赖网络波动。",
            created_at=iso_hours_ago(60),
            updated_at=iso_hours_ago(59),
            closed_at=iso_hours_ago(59),
        ),
        IncidentCase(
            case_id="case-payment-db-001",
            session_id="seed-payment-db-001",
            thread_id="seed-payment-db-001",
            ticket_id="SEED-PAYMENT-DB-001",
            service="payment-service",
            cluster="prod-shanghai-1",
            namespace="default",
            current_agent="hypothesis_graph",
            symptom="payment-service 超时，数据库慢查询与复制延迟同时升高",
            root_cause="数据库连接池或慢查询异常拖慢请求处理",
            key_evidence=[
                "replication lag 65s",
                "slow query count 5",
                "connection pool saturated",
            ],
            final_action="observe_service",
            approval_required=False,
            verification_passed=True,
            human_verified=True,
            hypothesis_accuracy={"H1": 0.93, "H2": 0.17},
            actual_root_cause_hypothesis="H1",
            selected_hypothesis_id="H1",
            selected_ranker_features={
                "evidence_strength": 0.94,
                "confidence": 0.77,
                "history_match": 0.51,
            },
            final_conclusion="支付服务超时由数据库侧性能退化引起。",
            created_at=iso_hours_ago(48),
            updated_at=iso_hours_ago(47),
            closed_at=iso_hours_ago(47),
        ),
        IncidentCase(
            case_id="case-payment-alert-001",
            session_id="seed-payment-alert-001",
            thread_id="seed-payment-alert-001",
            ticket_id="SEED-PAYMENT-ALERT-001",
            service="payment-service",
            cluster="prod-shanghai-1",
            namespace="default",
            current_agent="hypothesis_graph",
            symptom="payment-service 大量告警但用户侧体验未明显异常",
            root_cause="日志与告警显示应用运行时异常正在放大",
            key_evidence=[
                "payment transaction failures alert firing",
                "error budget burn rate 2.8",
                "thread pool queue depth 74",
            ],
            final_action="observe_service",
            approval_required=False,
            verification_passed=False,
            human_verified=True,
            hypothesis_accuracy={"H1": 0.72, "H2": 0.54},
            actual_root_cause_hypothesis="H1",
            selected_hypothesis_id="H1",
            selected_ranker_features={
                "evidence_strength": 0.63,
                "confidence": 0.69,
                "history_match": 0.28,
            },
            final_conclusion="监控与线程池确有波动，但最终影响有限，归因为短时运行时异常放大。",
            created_at=iso_hours_ago(36),
            updated_at=iso_hours_ago(35),
            closed_at=iso_hours_ago(35),
        ),
        IncidentCase(
            case_id="case-payment-oom-001",
            session_id="seed-payment-oom-001",
            thread_id="seed-payment-oom-001",
            ticket_id="SEED-PAYMENT-OOM-001",
            service="payment-service",
            cluster="prod-shanghai-1",
            namespace="default",
            current_agent="hypothesis_graph",
            symptom="payment-service pod 重启且出现 OOMKilled",
            root_cause="Pod 健康异常或资源不足导致服务不稳定",
            key_evidence=[
                "OOMKilled event observed",
                "heap usage 0.97",
                "critical 告警持续 firing",
            ],
            final_action="restart_pods",
            approval_required=True,
            verification_passed=True,
            human_verified=True,
            hypothesis_accuracy={"H1": 0.98, "H2": 0.11},
            actual_root_cause_hypothesis="H1",
            selected_hypothesis_id="H1",
            selected_ranker_features={
                "evidence_strength": 0.96,
                "confidence": 0.81,
                "history_match": 0.33,
            },
            final_conclusion="支付服务不稳定由内存压力导致的 OOMKilled 引起。",
            created_at=iso_hours_ago(24),
            updated_at=iso_hours_ago(23),
            closed_at=iso_hours_ago(23),
        ),
    ]


def build_store(backend: str, sqlite_path: str, postgres_dsn: str) -> IncidentCaseStore:
    backend = backend.lower()
    if backend == "postgres":
        if not postgres_dsn:
            raise ValueError("postgres backend requires --postgres-dsn or POSTGRES_DSN")
        return IncidentCaseStore(sqlite_path, backend=PostgresProcessMemoryStoreV2(postgres_dsn))
    return IncidentCaseStore(sqlite_path)


def seed_cases(store: IncidentCaseStore, cases: Iterable[IncidentCase]) -> int:
    count = 0
    for case in cases:
        store.upsert(case)
        count += 1
    return count


def main() -> None:
    settings = Settings()
    parser = argparse.ArgumentParser(description="Seed mock incident cases into the configured runtime database.")
    parser.add_argument("--backend", default=settings.storage_backend, choices=["sqlite", "postgres"])
    parser.add_argument("--sqlite-path", default=settings.approval_db_path)
    parser.add_argument("--postgres-dsn", default=settings.postgres_dsn)
    args = parser.parse_args()

    store = build_store(args.backend, args.sqlite_path, args.postgres_dsn)
    cases = build_cases()
    count = seed_cases(store, cases)
    print(f"seeded_incident_cases={count}")


if __name__ == "__main__":
    main()
