from __future__ import annotations

from dataclasses import dataclass

from ..approval_store import ApprovalStore
from ..checkpoint_store import CheckpointStore
from ..execution_store import ExecutionStore
from ..interrupt_store import InterruptStore
from ..memory_store import IncidentCaseStore, ProcessMemoryStore
from ..session_store import SessionStore
from ..settings import Settings
from ..system_event_store import SystemEventStore
from ..approval.pg_store import PostgresApprovalStoreV2
from ..checkpoints.pg_store import PostgresCheckpointStoreV2
from ..events.pg_store import PostgresSystemEventStore
from ..execution.pg_store import PostgresExecutionStoreV2
from ..interrupts.pg_store import PostgresInterruptStoreV2
from ..memory.pg_store import PostgresProcessMemoryStoreV2
from ..session.pg_store import PostgresSessionStore


@dataclass
class StoreBundle:
    approval_store: ApprovalStore
    session_store: SessionStore | PostgresSessionStore
    interrupt_store: InterruptStore
    checkpoint_store: CheckpointStore
    process_memory_store: ProcessMemoryStore
    execution_store: ExecutionStore
    incident_case_store: IncidentCaseStore
    system_event_store: SystemEventStore | PostgresSystemEventStore


class StoreProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def build(self) -> StoreBundle:
        db_path = self.settings.approval_db_path
        backend = str(self.settings.storage_backend or "sqlite").lower()
        if backend == "postgres":
            if not self.settings.postgres_dsn:
                raise ValueError("POSTGRES_DSN is required when STORAGE_BACKEND=postgres")
            memory_backend = PostgresProcessMemoryStoreV2(self.settings.postgres_dsn)
            return StoreBundle(
                approval_store=ApprovalStore(db_path, backend=PostgresApprovalStoreV2(self.settings.postgres_dsn)),
                session_store=PostgresSessionStore(self.settings.postgres_dsn),
                interrupt_store=InterruptStore(db_path, backend=PostgresInterruptStoreV2(self.settings.postgres_dsn)),
                checkpoint_store=CheckpointStore(db_path, backend=PostgresCheckpointStoreV2(self.settings.postgres_dsn)),
                process_memory_store=ProcessMemoryStore(db_path, backend=memory_backend),
                execution_store=ExecutionStore(db_path, backend=PostgresExecutionStoreV2(self.settings.postgres_dsn)),
                incident_case_store=IncidentCaseStore(db_path, backend=memory_backend),
                system_event_store=PostgresSystemEventStore(self.settings.postgres_dsn),
            )
        return StoreBundle(
            approval_store=ApprovalStore(db_path),
            session_store=SessionStore(db_path),
            interrupt_store=InterruptStore(db_path),
            checkpoint_store=CheckpointStore(db_path),
            process_memory_store=ProcessMemoryStore(db_path),
            execution_store=ExecutionStore(db_path),
            incident_case_store=IncidentCaseStore(db_path),
            system_event_store=SystemEventStore(db_path),
        )
