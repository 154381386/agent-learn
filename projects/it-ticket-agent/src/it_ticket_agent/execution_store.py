from __future__ import annotations

from typing import Any, Optional

from .execution import ExecutionPlan, ExecutionStep, ExecutionStoreV2


class ExecutionStore:
    def __init__(self, db_path: str, *, backend: ExecutionStoreV2 | None = None) -> None:
        self.db_path = db_path
        self.v2_store = backend or ExecutionStoreV2(db_path)

    def create_plan(self, plan: ExecutionPlan | dict[str, Any]) -> dict[str, Any]:
        record = plan if isinstance(plan, ExecutionPlan) else ExecutionPlan.model_validate(plan)
        saved = self.v2_store.create_plan(record)
        return saved.model_dump()

    def update_plan(self, plan_id: str, **changes: Any) -> Optional[dict[str, Any]]:
        saved = self.v2_store.update_plan(plan_id, **changes)
        return None if saved is None else saved.model_dump()

    def get_plan(self, plan_id: str) -> Optional[dict[str, Any]]:
        record = self.v2_store.get_plan(plan_id)
        return None if record is None else record.model_dump()

    def list_plans(self, session_id: str) -> list[dict[str, Any]]:
        return [record.model_dump() for record in self.v2_store.list_plans(session_id)]

    def create_step(self, step: ExecutionStep | dict[str, Any]) -> dict[str, Any]:
        record = step if isinstance(step, ExecutionStep) else ExecutionStep.model_validate(step)
        saved = self.v2_store.create_step(record)
        return saved.model_dump()

    def update_step(self, step_id: str, **changes: Any) -> Optional[dict[str, Any]]:
        saved = self.v2_store.update_step(step_id, **changes)
        return None if saved is None else saved.model_dump()

    def get_step(self, step_id: str) -> Optional[dict[str, Any]]:
        record = self.v2_store.get_step(step_id)
        return None if record is None else record.model_dump()

    def list_steps(self, plan_id: str) -> list[dict[str, Any]]:
        return [record.model_dump() for record in self.v2_store.list_steps(plan_id)]
