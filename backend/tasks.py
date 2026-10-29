"""Almacenamiento de tareas en disco (JSON por tarea; suite oculta en un directorio aparte).

Estructura: `<TASK_STORE_DIR>/<task_id>/task.json`, `.../hidden/` (tests ocultos, nunca
se exponen por la API) y `.../verdicts/<submission_id>.json`.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from backend.config import get_settings
from backend.contract.models import TaskDraft

TaskStatus = Literal["awaiting_plan_approval", "open", "suite_rejected"]
_TASK_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class SuiteCriterionStatus(BaseModel):
    criterion_id: str
    attempts: int
    accepted: bool
    problems: list[str]


class TaskRecord(BaseModel):
    task_id: str
    created_at: str
    status: TaskStatus
    draft: TaskDraft
    rules_hash: str
    suite_hash: str
    plan: dict[str, list[str]]
    suite_validation: list[SuiteCriterionStatus]
    creation_usage: list[dict]

    def public_view(self) -> dict:
        """Lo que puede ver el freelancer: contrato y plan, nunca el código de los tests."""
        return {
            "task_id": self.task_id,
            "status": self.status,
            "rules_hash": self.rules_hash,
            "suite_hash": self.suite_hash,
            "contract": self.draft.model_dump(mode="json"),
            "test_plan": self.plan,
        }


def _root() -> Path:
    return get_settings().task_store_dir


def task_dir(task_id: str) -> Path:
    if not _TASK_ID_RE.match(task_id):
        raise KeyError(task_id)
    return _root() / task_id


def hidden_dir(task_id: str) -> Path:
    return task_dir(task_id) / "hidden"


def new_task_id() -> str:
    return uuid.uuid4().hex


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def save_task(record: TaskRecord) -> None:
    _write_atomic(task_dir(record.task_id) / "task.json", record.model_dump_json(indent=1))


def load_task(task_id: str) -> TaskRecord:
    path = task_dir(task_id) / "task.json"
    if not path.exists():
        raise KeyError(task_id)
    return TaskRecord.model_validate_json(path.read_text(encoding="utf-8"))


def save_verdict(task_id: str, submission_id: str, report: dict) -> Path:
    path = task_dir(task_id) / "verdicts" / f"{submission_id}.json"
    _write_atomic(path, json.dumps(report, ensure_ascii=False, indent=1))
    return path
