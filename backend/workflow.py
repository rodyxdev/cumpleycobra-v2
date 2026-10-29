"""Creación de tareas: congelar el contrato y preparar la suite oculta."""

from __future__ import annotations

from dataclasses import asdict

from backend.contract import TaskDraft, blocking_errors, rules_hash
from backend.llm import track_usage
from backend.tasks import SuiteCriterionStatus, TaskRecord, hidden_dir, new_task_id, now_iso, save_task
from backend.verification.suite import SUITE_FORMAT, generate_suite


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
    if draft.profile == "web":
        return _create_web_task(task_id, draft, frozen_hash)
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
        suite_format=SUITE_FORMAT,
    )
    save_task(record)
    return record


def _create_web_task(task_id: str, draft: TaskDraft, frozen_hash: str) -> TaskRecord:
    """Perfil web: no hay suite generada por LLM; los chequeos son el catálogo del contrato,
    ejecutado por un runner fijo. Los mockups deben estar subidos (su hash ya está en el contrato)."""
    from backend.web.verify import SUITE_FORMAT_WEB, contract_mockups_missing, web_plan, web_suite_hash

    missing = contract_mockups_missing(draft)
    if missing:
        raise TaskCreationError([f"mockup no subido a /mockups: {m}" for m in missing])
    record = TaskRecord(
        task_id=task_id,
        created_at=now_iso(),
        status="awaiting_plan_approval",
        draft=draft,
        rules_hash=frozen_hash,
        suite_hash=web_suite_hash(draft),
        plan=web_plan(draft),
        suite_validation=[],
        creation_usage=[],
        suite_format=SUITE_FORMAT_WEB,
    )
    save_task(record)
    return record
