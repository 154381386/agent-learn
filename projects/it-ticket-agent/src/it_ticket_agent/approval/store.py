from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Dict, List, Optional

from .models import ApprovalAuditEvent, ApprovalDecisionRecord, ApprovalRequest, utc_now


class ApprovalStateError(RuntimeError):
    pass


class ApprovalStoreV2:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        folder = os.path.dirname(db_path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                create table if not exists approval_request_v2 (
                    approval_id text primary key,
                    ticket_id text not null,
                    thread_id text not null,
                    status text not null,
                    highest_risk text not null,
                    summary text not null,
                    context_json text not null,
                    proposals_json text not null,
                    created_at text not null,
                    updated_at text not null,
                    decided_at text,
                    decided_by text,
                    comment text
                )
                """
            )
            conn.execute(
                """
                create table if not exists approval_audit_event (
                    event_id integer primary key autoincrement,
                    approval_id text not null,
                    event_type text not null,
                    actor_id text not null,
                    detail_json text not null,
                    created_at text not null,
                    foreign key (approval_id) references approval_request_v2 (approval_id)
                )
                """
            )
            conn.commit()

    def create_request(self, request: ApprovalRequest) -> ApprovalRequest:
        now = utc_now()
        payload = request.model_copy(update={"created_at": now, "updated_at": now, "status": "pending"})
        with self._connect() as conn:
            conn.execute(
                """
                insert into approval_request_v2 (
                    approval_id, ticket_id, thread_id, status, highest_risk, summary,
                    context_json, proposals_json, created_at, updated_at,
                    decided_at, decided_by, comment
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    detail={
                        "status": payload.status,
                        "proposal_count": len(payload.proposals),
                    },
                ),
            )
            conn.commit()
        return payload

    def get_request(self, approval_id: str) -> Optional[ApprovalRequest]:
        with self._connect() as conn:
            row = conn.execute(
                """
                select approval_id, ticket_id, thread_id, status, highest_risk, summary,
                       context_json, proposals_json, approver_id, decided_by, comment,
                       decided_at, created_at, updated_at
                from (
                    select approval_id, ticket_id, thread_id, status, highest_risk, summary,
                           context_json, proposals_json, null as approver_id, decided_by, comment,
                           decided_at, created_at, updated_at
                    from approval_request_v2
                )
                where approval_id = ?
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
            context=json.loads(row["context_json"]),
            proposals=json.loads(row["proposals_json"]),
            approver_id=row["decided_by"] or row["approver_id"],
            comment=row["comment"],
            decided_at=row["decided_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def record_decision(self, decision: ApprovalDecisionRecord) -> ApprovalRequest:
        with self._connect() as conn:
            row = conn.execute(
                "select status from approval_request_v2 where approval_id = ?",
                (decision.approval_id,),
            ).fetchone()
            if row is None:
                raise KeyError("approval not found")
            if row["status"] != "pending":
                raise ApprovalStateError("approval decision already recorded")

            next_status = "approved" if decision.approved else "rejected"
            updated_at = utc_now()
            conn.execute(
                """
                update approval_request_v2
                set status = ?, updated_at = ?, decided_at = ?, decided_by = ?, comment = ?
                where approval_id = ? and status = 'pending'
                """,
                (
                    next_status,
                    updated_at,
                    decision.decided_at,
                    decision.approver_id,
                    decision.comment,
                    decision.approval_id,
                ),
            )
            self._insert_event(
                conn,
                ApprovalAuditEvent(
                    approval_id=decision.approval_id,
                    event_type="decision_recorded",
                    actor_id=decision.approver_id,
                    detail={
                        "approved": decision.approved,
                        "comment": decision.comment,
                        "decided_at": decision.decided_at,
                    },
                    created_at=decision.decided_at,
                ),
            )
            conn.commit()
        record = self.get_request(decision.approval_id)
        if record is None:
            raise KeyError("approval not found")
        return record

    def list_events(self, approval_id: str) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select approval_id, event_type, actor_id, detail_json, created_at
                from approval_audit_event
                where approval_id = ?
                order by event_id asc
                """,
                (approval_id,),
            ).fetchall()
        return [
            {
                "approval_id": row["approval_id"],
                "event_type": row["event_type"],
                "actor_id": row["actor_id"],
                "detail": json.loads(row["detail_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    @staticmethod
    def _insert_event(conn: sqlite3.Connection, event: ApprovalAuditEvent) -> None:
        conn.execute(
            """
            insert into approval_audit_event (
                approval_id, event_type, actor_id, detail_json, created_at
            ) values (?, ?, ?, ?, ?)
            """,
            (
                event.approval_id,
                event.event_type,
                event.actor_id,
                json.dumps(event.detail, ensure_ascii=False),
                event.created_at,
            ),
        )
