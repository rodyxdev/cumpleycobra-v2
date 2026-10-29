"""Creación de tareas: congelar el contrato y preparar la suite oculta."""

from __future__ import annotations

from dataclasses import asdict

from backend.contract import TaskDraft, blocking_errors, rules_hash
from backend.llm import track_usage
from backend.tasks import SuiteCriterionStatus, TaskRecord, hidden_dir, new_task_id, now_iso, save_task
from backend.verification.suite import generate_suite


class TaskCreationError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def create_task(draft: TaskDraft) -> TaskRecord:
    """Valida el contrato, calcula `rules_hash` y genera la suite oculta (fuera de cualquier workspace)."""
    errors = blocking_errors(draft)
    if errors:
        raise TaskCreationError(errors)
    task_id = new_task_id()
    frozen_hash = rules_hash(draft)
    with track_usage() as usage:
        suite = generate_suite(draft, hidden_dir(task_id))
    record = TaskRecord(
        task_id=task_id,
        created_at=now_iso(),
        status="awaiting_plan_approval" if suite.accepted else "suite_rejected",
        draft=draft,
        rules_hash=frozen_hash,
        suite_hash=suite.suite_hash,
        plan=suite.plan,
        suite_validation=[SuiteCriterionStatus(**asdict(r)) for r in suite.reports],
        creation_usage=[asdict(u) for u in usage],
    )
    save_task(record)
    return record
