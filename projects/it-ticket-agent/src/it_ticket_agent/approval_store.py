import ast
import json
import os
import sqlite3
import uuid
from typing import Any, Dict, Optional


class ApprovalStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        folder = os.path.dirname(db_path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                create table if not exists approval_request (
                    approval_id text primary key,
                    ticket_id text not null,
                    thread_id text not null,
                    action text not null,
                    risk text not null,
                    reason text not null,
                    params_json text not null,
                    status text not null,
                    approver_id text,
                    comment text
                )
                """
            )
            conn.commit()

    def create(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        approval_id = str(uuid.uuid4())
        record = dict(payload)
        record["approval_id"] = approval_id
        params_json = json.dumps(record.get("params", {}), ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                """
                insert into approval_request
                (approval_id, ticket_id, thread_id, action, risk, reason, params_json, status)
                values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval_id,
                    record["ticket_id"],
                    record["thread_id"],
                    record["action"],
                    record["risk"],
                    record["reason"],
                    params_json,
                    "pending",
                ),
            )
            conn.commit()
        return record

    def get(self, approval_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                """
                select approval_id, ticket_id, thread_id, action, risk, reason,
                       params_json, status, approver_id, comment
                from approval_request
                where approval_id = ?
                """,
                (approval_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "approval_id": row[0],
            "ticket_id": row[1],
            "thread_id": row[2],
            "action": row[3],
            "risk": row[4],
            "reason": row[5],
            "params": self._load_params(row[6]),
            "status": row[7],
            "approver_id": row[8],
            "comment": row[9],
        }

    def decide(self, approval_id: str, approved: bool, approver_id: str, comment: Optional[str]) -> Dict[str, Any]:
        status = "approved" if approved else "rejected"
        with self._connect() as conn:
            conn.execute(
                """
                update approval_request
                set status = ?, approver_id = ?, comment = ?
                where approval_id = ?
                """,
                (status, approver_id, comment, approval_id),
            )
            conn.commit()
        record = self.get(approval_id)
        if record is None:
            raise KeyError("approval not found")
        return record

    @staticmethod
    def _load_params(params_json: str) -> Dict[str, Any]:
        if not params_json:
            return {}
        try:
            return json.loads(params_json)
        except json.JSONDecodeError:
            try:
                parsed = ast.literal_eval(params_json)
            except (ValueError, SyntaxError):
                return {}
            return parsed if isinstance(parsed, dict) else {}
