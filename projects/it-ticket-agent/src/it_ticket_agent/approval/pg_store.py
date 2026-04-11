from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .models import ApprovalAuditEvent, ApprovalDecisionRecord, ApprovalRequest, utc_now
from .store import ALLOWED_APPROVAL_TRANSITIONS, ApprovalStateError
from ..execution.security import bind_request_snapshots
from ..storage.postgres import postgres_connection


class PostgresApprovalStoreV2:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._init_db()

    def _init_db(self) -> None:
        with postgres_connection(self.dsn) as conn:
            conn.execute(
                """
                create table if not exists approval_request_v2 (
                    approval_id text primary key,
                    ticket_id text not null,
                    thread_id text not null,
                    status text not null,
                    highest_risk text not null,
                    summary text not null,
                    context_json jsonb not null,
                    proposals_json jsonb not null,
                    created_at timestamptz not null,
                    updated_at timestamptz not null,
                    decided_at timestamptz,
                    decided_by text,
                    comment text
                )
                """
            )
            conn.execute(
                """
                create table if not exists approval_audit_event (
                    event_id bigserial primary key,
                    approval_id text not null,
                    event_type text not null,
                    actor_id text not null,
                    detail_json jsonb not null,
                    created_at timestamptz not null
                )
                """
            )
            conn.execute(
                """
                create index if not exists idx_approval_request_status_updated
                on approval_request_v2 (status, updated_at)
                """
            )
            conn.execute(
                """
                create index if not exists idx_approval_audit_event_approval_created
                on approval_audit_event (approval_id, created_at, event_id)
                """
            )

    def create_request(self, request: ApprovalRequest) -> ApprovalRequest:
        now = utc_now()
        prepared = bind_request_snapshots(request)
        payload = prepared.model_copy(update={"created_at": now, "updated_at": now, "status": "pending"})
        with postgres_connection(self.dsn) as conn:
            conn.execute(
                """
                insert into approval_request_v2 (
                    approval_id, ticket_id, thread_id, status, highest_risk, summary,
                    context_json, proposals_json, created_at, updated_at, decided_at, decided_by, comment
                ) values (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s)
                """,
                (
                    payload.approval_id,
                    payload.ticket_id,
                    payload.thread_id,
                    payload.status,
                    payload.highest_risk,
                    payload.summary,
                    json.dumps(payload.context, ensure_ascii=False),
                    json.dumps(payload.model_dump()["proposals"], ensure_ascii=False),
                    payload.created_at,
                    payload.updated_at,
                    payload.decided_at,
                    payload.approver_id,
                    payload.comment,
                ),
            )
            self._insert_event(
                conn,
                ApprovalAuditEvent(
                    approval_id=payload.approval_id,
                    event_type="created",
                    detail={"status": payload.status, "proposal_count": len(payload.proposals)},
                ),
            )
        return payload

    def get_request(self, approval_id: str) -> Optional[ApprovalRequest]:
        with postgres_connection(self.dsn) as conn:
            row = conn.execute(
                """
                select approval_id, ticket_id, thread_id, status, highest_risk, summary,
                       context_json, proposals_json, decided_by, comment, decided_at, created_at, updated_at
                from approval_request_v2
                where approval_id = %s
                """,
                (approval_id,),
            ).fetchone()
        if row is None:
            return None
        return ApprovalRequest(
            approval_id=row["approval_id"],
            ticket_id=row["ticket_id"],
            thread_id=row["thread_id"],
            status=row["status"],
            highest_risk=row["highest_risk"],
            summary=row["summary"],
            context=row["context_json"],
            proposals=row["proposals_json"],
            approver_id=row["decided_by"],
            comment=row["comment"],
            decided_at=str(row["decided_at"]) if row["decided_at"] is not None else None,
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def record_decision(self, decision: ApprovalDecisionRecord) -> ApprovalRequest:
        next_status = "approved" if decision.approved else "rejected"
        return self._transition_request(
            decision.approval_id,
            next_status=next_status,
            actor_id=decision.approver_id,
            comment=decision.comment,
            decided_at=decision.decided_at,
            detail={"approved": decision.approved, "comment": decision.comment, "decided_at": decision.decided_at},
        )

    def expire_request(
        self,
        approval_id: str,
        *,
        actor_id: str = "system",
        comment: str | None = None,
        decided_at: str | None = None,
    ) -> ApprovalRequest:
        return self._transition_request(
            approval_id,
            next_status="expired",
            actor_id=actor_id,
            comment=comment,
            decided_at=decided_at or utc_now(),
            detail={"comment": comment},
        )

    def cancel_request(
        self,
        approval_id: str,
        *,
        actor_id: str = "system",
        comment: str | None = None,
        decided_at: str | None = None,
    ) -> ApprovalRequest:
        return self._transition_request(
            approval_id,
            next_status="cancelled",
            actor_id=actor_id,
            comment=comment,
            decided_at=decided_at or utc_now(),
            detail={"comment": comment},
        )

    def record_resumed(
        self,
        approval_id: str,
        *,
        actor_id: str = "system",
        detail: dict[str, Any] | None = None,
        created_at: str | None = None,
    ) -> None:
        with postgres_connection(self.dsn) as conn:
            row = conn.execute(
                "select approval_id from approval_request_v2 where approval_id = %s",
                (approval_id,),
            ).fetchone()
            if row is None:
                raise KeyError("approval not found")
            self._insert_event(
                conn,
                ApprovalAuditEvent(
                    approval_id=approval_id,
                    event_type="resumed",
                    actor_id=actor_id,
                    detail=dict(detail or {}),
                    created_at=created_at or utc_now(),
                ),
            )

    def list_events(self, approval_id: str) -> List[Dict[str, Any]]:
        with postgres_connection(self.dsn) as conn:
            rows = conn.execute(
                """
                select approval_id, event_type, actor_id, detail_json, created_at
                from approval_audit_event
                where approval_id = %s
                order by event_id asc
                """,
                (approval_id,),
            ).fetchall()
        return [
            {
                "approval_id": row["approval_id"],
                "event_type": row["event_type"],
                "actor_id": row["actor_id"],
                "detail": row["detail_json"],
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]

    def _transition_request(
        self,
        approval_id: str,
        *,
        next_status: str,
        actor_id: str,
        comment: str | None,
        decided_at: str,
        detail: dict[str, Any] | None = None,
    ) -> ApprovalRequest:
        with postgres_connection(self.dsn) as conn:
            row = conn.execute(
                "select status from approval_request_v2 where approval_id = %s",
                (approval_id,),
            ).fetchone()
            if row is None:
                raise KeyError("approval not found")
            current_status = str(row["status"])
            if next_status not in ALLOWED_APPROVAL_TRANSITIONS.get(current_status, set()):
                raise ApprovalStateError(f"illegal approval state transition: {current_status} -> {next_status}")
            updated_at = utc_now()
            conn.execute(
                """
                update approval_request_v2
                set status = %s, updated_at = %s, decided_at = %s, decided_by = %s, comment = %s
                where approval_id = %s and status = 'pending'
                """,
                (next_status, updated_at, decided_at, actor_id, comment, approval_id),
            )
            self._insert_event(
                conn,
                ApprovalAuditEvent(
                    approval_id=approval_id,
                    event_type=next_status,
                    actor_id=actor_id,
                    detail={**dict(detail or {}), "status": next_status},
                    created_at=decided_at,
                ),
            )
        record = self.get_request(approval_id)
        if record is None:
            raise KeyError("approval not found")
        return record

    @staticmethod
    def _insert_event(conn, event: ApprovalAuditEvent) -> None:
        conn.execute(
            """
            insert into approval_audit_event (
                approval_id, event_type, actor_id, detail_json, created_at
            ) values (%s, %s, %s, %s::jsonb, %s)
            """,
            (
                event.approval_id,
                event.event_type,
                event.actor_id,
                json.dumps(event.detail, ensure_ascii=False),
                event.created_at,
            ),
        )
