"""API HTTP: contrato de tareas (fase 2) e ingesta + ejecución aislada de entregas (fase 1)."""

from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Iterator

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from backend.config import get_settings
from backend.contract import DraftReview, TaskDraft, draft_task, review_draft
from backend.contract.models import ConfirmRequest, ReviewRequest, TaskRequest
from backend.ingest import (
    GitHubPublicSource,
    IngestError,
    Workspace,
    ZipSource,
    fetch_submission,
    rmtree_force,
)
from backend.llm import LLMError
from backend.sandbox import SandboxUnavailable, run_in_sandbox
from backend.tasks import load_task, save_task, save_verdict
from backend.workflow import TaskCreationError, create_task

log = logging.getLogger(__name__)

DEFAULT_COMMAND = "ls -la && find . -type f | wc -l"

app = FastAPI(title="Cumple&Cobra v2", version="0.2.0")


class SandboxRun(BaseModel):
    exit_code: int | None
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    duration_s: float
    timed_out: bool
    oom_killed: bool
    image: str
    command: str
    network: bool
    output_files: list[str]


class SubmissionResponse(BaseModel):
    submission_id: str
    workspace_hash: str
    size_bytes: int
    source: dict
    sandbox: SandboxRun
    output_files: list[str]


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# --- Contrato ------------------------------------------------------------------------------

@app.post("/tasks/draft", response_model=TaskDraft)
def post_task_draft(body: TaskRequest) -> TaskDraft:
    """Pedido en texto libre → borrador de contrato propuesto por Muse Spark."""
    try:
        return draft_task(body.request)
    except LLMError as exc:
        raise HTTPException(502, f"El modelo no respondió correctamente: {exc}") from exc


@app.post("/tasks/draft/review", response_model=DraftReview)
def post_task_draft_review(body: ReviewRequest) -> DraftReview:
    """Marca criterios vagos o no verificables, con una sugerencia para cada uno."""
    try:
        return review_draft(body.draft)
    except LLMError as exc:
        raise HTTPException(502, f"El modelo no respondió correctamente: {exc}") from exc


@app.post("/tasks", status_code=201)
def post_task(body: ConfirmRequest) -> dict:
    """Confirma el contrato: congela `rules_hash` y genera y valida la suite oculta."""
    try:
        record = create_task(body.draft)
    except TaskCreationError as exc:
        raise HTTPException(422, {"errors": exc.errors}) from exc
    except LLMError as exc:
        raise HTTPException(502, f"El modelo no respondió correctamente: {exc}") from exc
    except SandboxUnavailable as exc:
        raise HTTPException(503, f"Sandbox no disponible: {exc}") from exc
    return {**record.public_view(), "suite_validation": [s.model_dump() for s in record.suite_validation]}


@app.get("/tasks/{task_id}")
def get_task(task_id: str) -> dict:
    """Vista del freelancer: contrato y plan de pruebas, nunca el código de los tests."""
    return _load(task_id).public_view()


@app.post("/tasks/{task_id}/plan/approve")
def approve_plan(task_id: str) -> dict:
    record = _load(task_id)
    if record.status != "awaiting_plan_approval":
        raise HTTPException(409, f"La tarea está en estado {record.status}")
    record = record.model_copy(update={"status": "open"})
    save_task(record)
    return record.public_view()


@app.post("/tasks/{task_id}/submissions")
def post_task_submission(
    task_id: str,
    repo: Annotated[str | None, Form()] = None,
    sha: Annotated[str | None, Form()] = None,
    zip_file: Annotated[UploadFile | None, File(alias="zip")] = None,
) -> dict:
    """Ingiere la entrega y corre las 5 capas de verificación. Devuelve el veredicto."""
    from backend.verification.verdict import LegacySuiteError, verify_submission

    record = _load(task_id)
    if record.status != "open":
        raise HTTPException(409, f"La tarea no acepta entregas (estado {record.status}); ¿falta aprobar el plan?")
    if record.draft.profile == "web":
        raise HTTPException(501, "La verificación del perfil web aún no está implementada")
    with _ingested(repo, sha, zip_file) as workspace:
        try:
            report = verify_submission(record, workspace)
        except SandboxUnavailable as exc:
            raise HTTPException(503, f"Sandbox no disponible: {exc}") from exc
        except LegacySuiteError as exc:
            raise HTTPException(409, str(exc)) from exc
    save_verdict(task_id, report["submission_id"], report)
    return report


def _load(task_id: str):
    try:
        return load_task(task_id)
    except KeyError as exc:
        raise HTTPException(404, "Tarea no encontrada") from exc


# --- Ingesta + ejecución (fase 1) ----------------------------------------------------------

@app.post("/submissions", response_model=SubmissionResponse)
def create_submission(
    repo: Annotated[str | None, Form()] = None,
    sha: Annotated[str | None, Form()] = None,
    command: Annotated[str, Form()] = DEFAULT_COMMAND,
    zip_file: Annotated[UploadFile | None, File(alias="zip")] = None,
) -> SubmissionResponse:
    """Acepta `repo`+`sha` (repo público de GitHub) **o** un archivo `zip` (multipart)."""
    if not command.strip():
        raise HTTPException(422, "'command' no puede estar vacío")
    with _ingested(repo, sha, zip_file) as workspace:
        try:
            result = run_in_sandbox(workspace.path, command, workspace.path.parent / f"{workspace.path.name}-out")
        except SandboxUnavailable as exc:
            raise HTTPException(503, f"Sandbox no disponible: {exc}") from exc
        finally:
            rmtree_force(workspace.path.parent / f"{workspace.path.name}-out")
        return SubmissionResponse(
            submission_id=uuid.uuid4().hex,
            workspace_hash=workspace.content_hash,
            size_bytes=workspace.size_bytes,
            source=workspace.source,
            sandbox=SandboxRun(**asdict(result)),
            output_files=result.output_files,
        )


@contextmanager
def _ingested(repo: str | None, sha: str | None, zip_file: UploadFile | None) -> Iterator[Workspace]:
    """Valida la fuente, la ingiere y borra todo (workspace y ZIP) al salir."""
    s = get_settings()
    has_repo = bool(repo or sha)
    if has_repo == (zip_file is not None):
        raise HTTPException(422, "Envía 'repo' y 'sha', o bien un archivo 'zip' (exactamente uno de los dos)")
    if has_repo and not (repo and sha):
        raise HTTPException(422, "'repo' y 'sha' van juntos")

    run_dir = s.work_dir / "uploads" / uuid.uuid4().hex
    run_dir.mkdir(parents=True)
    workspace = None
    try:
        if zip_file is not None:
            upload_path = run_dir / "upload.zip"
            _save_upload(zip_file, upload_path, s.zip_max_upload_bytes)
            source = ZipSource(path=upload_path)
        else:
            source = GitHubPublicSource(repo=repo, sha=sha)
        workspace = fetch_submission(source)
        yield workspace
    except IngestError as exc:
        raise HTTPException(422, f"Entrega inválida: {exc}") from exc
    except NotImplementedError as exc:
        raise HTTPException(501, str(exc)) from exc
    finally:
        if workspace is not None:
            rmtree_force(workspace.path)
        rmtree_force(run_dir)


def _save_upload(upload: UploadFile, dest: Path, max_bytes: int) -> None:
    written = 0
    with dest.open("wb") as out:
        while chunk := upload.file.read(1024 * 1024):
            written += len(chunk)
            if written > max_bytes:
                raise IngestError(f"el ZIP supera {max_bytes} bytes")
            out.write(chunk)
    upload.file.close()
