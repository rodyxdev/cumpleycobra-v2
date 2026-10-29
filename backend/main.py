"""API HTTP de Cumple&Cobra v2.

Roles por tarea: cliente (quien la crea), freelancer (quien la acepta) y árbitro (el backend,
ARBITER_TOKEN). Toda ruta que devuelve un reporte o evidencia lo hace a través de `views.py`:
el cliente nunca recibe código de la entrega antes del pago, y nadie salvo el árbitro ve el
código de los tests ocultos.
"""

from __future__ import annotations

import io
import logging
import re
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Iterator

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from backend import escrow as chain
from backend.auth import CurrentUser, NewUser, User, create_user, get_user, role_in_task, update_address
from backend.config import get_settings
from backend.contract import DraftReview, TaskDraft, draft_task, review_draft
from backend.contract.models import ConfirmRequest, ReviewRequest, TaskRequest
from backend.ingest import (
    GitHubAppSource,
    GitHubPublicSource,
    IngestError,
    Workspace,
    ZipSource,
    fetch_submission,
    hash_workspace,
    rmtree_force,
)
from backend.ingest.hashing import deterministic_zip
from backend.llm import LLMError
from backend.sandbox import SandboxUnavailable, run_in_sandbox
from backend.tasks import (
    TaskRecord,
    list_verdicts,
    load_task,
    load_verdict,
    now_iso,
    save_task,
    save_verdict,
    source_archive_path,
    task_dir,
)
from backend.views import evidence_allowed, view_for
from backend.workflow import TaskCreationError, create_task

log = logging.getLogger(__name__)

DEFAULT_COMMAND = "ls -la && find . -type f | wc -l"

app = FastAPI(title="Cumple&Cobra v2", version="0.4.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Workspace-Hash"],
)


def escrow_service():
    """Punto único para obtener el cliente del contrato (los tests lo sustituyen)."""
    if not chain.configured():
        raise HTTPException(503, "El escrow no está configurado (ESCROW_CONTRACT_ID / ARBITER_SECRET)")
    return chain.EscrowClient()


# --- Sistema y usuarios --------------------------------------------------------------------

@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/config")
def public_config() -> dict:
    import os

    contract = os.getenv("ESCROW_CONTRACT_ID")
    return {
        "network": "testnet",
        "escrow_contract_id": contract,
        "escrow_contract_url": chain.contract_url(contract) if contract else None,
        "usdc_contract_id": os.getenv("USDC_CONTRACT_ID"),
        "usdc_asset": os.getenv("USDC_ASSET"),
        "explorer": chain.EXPLORER,
        "demo_wallets": chain.EscrowClient.demo_addresses() if chain.configured() else {},
        "github_app_configured": bool(os.getenv("GITHUB_APP_ID")),
    }


@app.post("/users", status_code=201)
def post_user(body: NewUser) -> dict:
    """Registro de la demo: devuelve un token bearer que no se vuelve a mostrar."""
    user, token = create_user(body)
    return {"user": user.model_dump(), "token": token}


@app.get("/me")
def get_me(user: CurrentUser) -> dict:
    return user.model_dump()


class DemoWalletRequest(BaseModel):
    role: str = Field(pattern="^(client|freelancer)$")


@app.post("/me/demo-wallet")
def use_demo_wallet(body: DemoWalletRequest, user: CurrentUser) -> dict:
    """Asocia al usuario la billetera de prueba (testnet) que el backend puede firmar por él."""
    address = chain.EscrowClient.demo_addresses().get(body.role)
    if not address:
        raise HTTPException(503, "No hay billetera de demo configurada")
    return update_address(user.user_id, address).model_dump()


def _demo_signer(user: User, role: str):
    """Firmante de demo si la dirección del usuario es la billetera de prueba de ese rol."""
    addresses = chain.EscrowClient.demo_addresses()
    if not user.stellar_address or user.stellar_address != addresses.get(role):
        raise HTTPException(409, "Tu dirección no es una billetera de demo: firma la transacción con tu billetera "
                                 "(POST .../escrow/prepare y .../escrow/submit)")
    return chain.EscrowClient.signer(role)


# --- Contrato ------------------------------------------------------------------------------

@app.post("/mockups", status_code=201)
def post_mockup(file: Annotated[UploadFile, File()], user: CurrentUser) -> dict:
    """Sube un mockup (PNG o JPEG). Devuelve su sha256 para referenciarlo en el contrato web."""
    from backend.web.mockups import MAX_BYTES, MockupError, save_mockup

    data = file.file.read(MAX_BYTES + 1)
    try:
        return save_mockup(data)
    except MockupError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/tasks/draft", response_model=TaskDraft)
def post_task_draft(body: TaskRequest, user: CurrentUser) -> TaskDraft:
    """Pedido en texto libre → borrador de contrato propuesto por Muse Spark."""
    try:
        return draft_task(body.request, [m.model_dump() for m in body.mockups])
    except LLMError as exc:
        raise HTTPException(502, f"El modelo no respondió correctamente: {exc}") from exc


@app.post("/tasks/draft/review", response_model=DraftReview)
def post_task_draft_review(body: ReviewRequest, user: CurrentUser) -> DraftReview:
    """Marca criterios vagos, no verificables o que imponen implementación, con una sugerencia."""
    try:
        return review_draft(body.draft)
    except LLMError as exc:
        raise HTTPException(502, f"El modelo no respondió correctamente: {exc}") from exc


@app.post("/tasks", status_code=201)
def post_task(body: ConfirmRequest, user: CurrentUser) -> dict:
    """Confirma el contrato: congela `rules_hash` y prepara la suite. Quien la crea es el cliente."""
    try:
        record = create_task(body.draft)
    except TaskCreationError as exc:
        raise HTTPException(422, {"errors": exc.errors}) from exc
    except LLMError as exc:
        raise HTTPException(502, f"El modelo no respondió correctamente: {exc}") from exc
    except SandboxUnavailable as exc:
        raise HTTPException(503, f"Sandbox no disponible: {exc}") from exc
    record = record.model_copy(update={"client_id": user.user_id})
    save_task(record)
    return {**_task_view(record, user), "suite_validation": [s.model_dump() for s in record.suite_validation]}


@app.get("/tasks")
def list_tasks(user: CurrentUser) -> list[dict]:
    """Tareas donde participo y tareas abiertas con depósito y sin freelancer (para aceptar)."""
    root = get_settings().task_store_dir
    out = []
    for path in sorted(root.glob("*/task.json")) if root.exists() else []:
        record = TaskRecord.model_validate_json(path.read_text(encoding="utf-8"))
        role = role_in_task(user, record)
        available = (record.status == "open" and record.freelancer_id is None
                     and record.escrow.get("status") == "funded")
        if role != "visitor" or available:
            out.append({"task_id": record.task_id, "title": record.draft.title, "profile": record.draft.profile,
                        "status": record.status, "role": role, "escrow_status": record.escrow.get("status"),
                        "amount": record.escrow.get("amount"), "created_at": record.created_at})
    return sorted(out, key=lambda t: t["created_at"], reverse=True)


@app.get("/tasks/{task_id}")
def get_task(task_id: str, user: CurrentUser) -> dict:
    """Contrato y plan de pruebas (nunca el código de los tests)."""
    record = _load(task_id)
    role = role_in_task(user, record)
    if role == "visitor" and not (record.status == "open" and record.freelancer_id is None):
        raise HTTPException(404, "Tarea no encontrada")
    return _task_view(record, user)


@app.post("/tasks/{task_id}/plan/approve")
def approve_plan(task_id: str, user: CurrentUser) -> dict:
    record = _require(task_id, user, "client")
    if record.status != "awaiting_plan_approval":
        raise HTTPException(409, f"La tarea está en estado {record.status}")
    record = record.model_copy(update={"status": "open"})
    save_task(record)
    return _task_view(record, user)


# --- Escrow --------------------------------------------------------------------------------

class DepositRequest(BaseModel):
    amount: str = Field(pattern=r"^\d+(\.\d{1,7})?$", description="USDC, p. ej. '25' o '12.5'")
    deadline_days: int = Field(ge=1, le=90)


@app.post("/tasks/{task_id}/escrow/deposit")
def escrow_deposit(task_id: str, body: DepositRequest, user: CurrentUser) -> dict:
    record = _require(task_id, user, "client")
    if record.status != "open":
        raise HTTPException(409, "Aprueba el plan de pruebas antes de depositar")
    if record.escrow.get("status"):
        raise HTTPException(409, f"El escrow ya está en estado {record.escrow['status']}")
    signer = _demo_signer(user, "client")
    service = escrow_service()
    deadline = int(time.time()) + body.deadline_days * 86400
    try:
        tx = service.deposit(signer, task_id, chain.to_units(body.amount), deadline, record.rules_hash)
    except chain.EscrowError as exc:
        raise HTTPException(502, str(exc)) from exc
    escrow = {"status": "funded", "contract_id": service.contract_id, "task_key": chain.task_key(task_id).hex(),
              "amount": body.amount, "deadline": deadline, "client_address": signer.public_key,
              "rules_hash": record.rules_hash, "txs": [_tx(tx)]}
    record = record.model_copy(update={"escrow": escrow})
    save_task(record)
    return _task_view(record, user)


@app.post("/tasks/{task_id}/accept")
def accept_task(task_id: str, user: CurrentUser) -> dict:
    """El freelancer acepta: on-chain queda amarrado a su dirección y desde ahí puede entregar."""
    record = _load(task_id)
    if role_in_task(user, record) == "client":
        raise HTTPException(409, "El cliente no puede aceptar su propia tarea")
    if record.freelancer_id or record.status != "open":
        raise HTTPException(409, "La tarea ya no está disponible")
    if record.escrow.get("status") != "funded":
        raise HTTPException(409, "La tarea todavía no tiene depósito")
    signer = _demo_signer(user, "freelancer")
    try:
        tx = escrow_service().accept(signer, task_id)
    except chain.EscrowError as exc:
        raise HTTPException(502, str(exc)) from exc
    escrow = {**record.escrow, "status": "accepted", "freelancer_address": signer.public_key,
              "txs": [*record.escrow.get("txs", []), _tx(tx)]}
    record = record.model_copy(update={"freelancer_id": user.user_id, "escrow": escrow})
    save_task(record)
    return _task_view(record, user)


@app.get("/tasks/{task_id}/escrow")
def escrow_state(task_id: str, user: CurrentUser) -> dict:
    record = _require(task_id, user, "client", "freelancer", "arbiter")
    onchain = None
    if record.escrow.get("status") and chain.configured():
        try:
            onchain = escrow_service().get(task_id)
        except chain.EscrowError as exc:
            onchain = {"error": str(exc)}
    return {"recorded": record.escrow, "onchain": onchain}


@app.post("/tasks/{task_id}/escrow/client-release")
def escrow_client_release(task_id: str, user: CurrentUser) -> dict:
    """El cliente libera el pago a mano (p. ej. tras revisar un veredicto en revisión manual)."""
    record = _require(task_id, user, "client")
    if record.escrow.get("status") != "accepted":
        raise HTTPException(409, "No hay un pago aceptado que liberar")
    reports = list_verdicts(task_id)
    if not reports:
        raise HTTPException(409, "Todavía no hay ninguna entrega verificada")
    signer = _demo_signer(user, "client")
    try:
        tx = escrow_service().client_release(signer, task_id)
    except chain.EscrowError as exc:
        raise HTTPException(502, str(exc)) from exc
    latest = reports[-1]
    record = _mark_released(record, latest, tx, by="client")
    return _task_view(record, user)


class ReleaseRequest(BaseModel):
    submission_id: str


@app.post("/tasks/{task_id}/escrow/release")
def escrow_release(task_id: str, body: ReleaseRequest, user: CurrentUser) -> dict:
    """Solo el árbitro, y solo para un veredicto aprobado (reintento de la liberación automática)."""
    record = _require(task_id, user, "arbiter")
    report = _verdict(task_id, body.submission_id)
    return _task_view(_arbiter_release(record, report), user)


@app.post("/tasks/{task_id}/escrow/refund")
def escrow_refund(task_id: str, user: CurrentUser) -> dict:
    record = _require(task_id, user, "client", "freelancer", "arbiter")
    if record.escrow.get("status") not in ("funded", "accepted"):
        raise HTTPException(409, "No hay depósito que reembolsar")
    try:
        tx = escrow_service().refund(chain.EscrowClient.signer("arbiter"), task_id)
    except chain.EscrowError as exc:
        raise HTTPException(409, f"No se pudo reembolsar (¿aún no vence el plazo?): {exc}") from exc
    escrow = {**record.escrow, "status": "refunded", "txs": [*record.escrow.get("txs", []), _tx(tx)]}
    record = record.model_copy(update={"escrow": escrow})
    save_task(record)
    return _task_view(record, user)


def _arbiter_release(record: TaskRecord, report: dict) -> TaskRecord:
    if not report["verdict"]["approved"]:
        raise HTTPException(409, "Solo se libera automáticamente un veredicto aprobado")
    if record.escrow.get("status") != "accepted":
        raise HTTPException(409, f"El escrow está en estado {record.escrow.get('status')}")
    try:
        tx = escrow_service().release(record.task_id, report["verdict"]["workspace_hash"], report["verdict_hash"])
    except chain.EscrowError as exc:
        raise HTTPException(502, str(exc)) from exc
    return _mark_released(record, report, tx, by="arbiter")


def _mark_released(record: TaskRecord, report: dict, tx, by: str) -> TaskRecord:
    escrow = {**record.escrow, "status": "released", "released_by": by,
              "released_submission_id": report["submission_id"],
              "workspace_hash": report["verdict"]["workspace_hash"], "verdict_hash": report["verdict_hash"],
              "txs": [*record.escrow.get("txs", []), _tx(tx)]}
    record = record.model_copy(update={"escrow": escrow})
    save_task(record)
    return record


def _tx(tx) -> dict:
    return {**tx.as_dict(), "at": now_iso()}


# --- Entregas y reportes -------------------------------------------------------------------

@app.post("/tasks/{task_id}/submissions")
def post_task_submission(
    task_id: str,
    user: CurrentUser,
    repo: Annotated[str | None, Form()] = None,
    sha: Annotated[str | None, Form()] = None,
    installation_id: Annotated[int | None, Form()] = None,
    zip_file: Annotated[UploadFile | None, File(alias="zip")] = None,
) -> dict:
    """El freelancer entrega (repo+SHA público, repo privado vía GitHub App, o ZIP)."""
    from backend.verification.verdict import LegacySuiteError, verify_submission
    from backend.web.verify import verify_web_submission

    record = _require(task_id, user, "freelancer")
    if record.escrow.get("status") in ("released", "refunded"):
        raise HTTPException(409, f"La tarea ya está cerrada (pago {record.escrow['status']})")
    verify = verify_web_submission if record.draft.profile == "web" else verify_submission
    with _ingested(repo, sha, zip_file, installation_id) as workspace:
        try:
            report = verify(record, workspace)
        except SandboxUnavailable as exc:
            raise HTTPException(503, f"Sandbox no disponible: {exc}") from exc
        except LegacySuiteError as exc:
            raise HTTPException(409, str(exc)) from exc
        report["received_at"] = now_iso()
        report["source"] = workspace.source
        archive = source_archive_path(task_id, report["submission_id"])
        archive.write_bytes(deterministic_zip(workspace.path))
    save_verdict(task_id, report["submission_id"], report)

    payment = None
    if report["verdict"]["approved"] and record.escrow.get("status") == "accepted":
        try:
            record = _arbiter_release(record, report)
            payment = record.escrow
        except HTTPException as exc:
            payment = {"error": exc.detail}
    return {**view_for("freelancer", report), "payment": payment}


@app.get("/tasks/{task_id}/submissions")
def list_submissions(task_id: str, user: CurrentUser) -> list[dict]:
    record = _require(task_id, user, "client", "freelancer", "arbiter")
    role = role_in_task(user, record)
    return [view_for(role, r) for r in list_verdicts(task_id)]


@app.get("/tasks/{task_id}/submissions/{submission_id}")
def get_submission(task_id: str, submission_id: str, user: CurrentUser) -> dict:
    record = _require(task_id, user, "client", "freelancer", "arbiter")
    return view_for(role_in_task(user, record), _verdict(task_id, submission_id))


@app.get("/tasks/{task_id}/submissions/{submission_id}/evidence/{name}")
def get_evidence(task_id: str, submission_id: str, name: str, user: CurrentUser) -> FileResponse:
    """Capturas y video para todas las partes; results.json solo para freelancer y árbitro."""
    record = _require(task_id, user, "client", "freelancer", "arbiter")
    if not re.fullmatch(r"(S\d{1,3}\.png|video-(desktop|mobile)(-\d+)?\.webm|results\.json)", name):
        raise HTTPException(404, "evidencia no encontrada")
    if not evidence_allowed(role_in_task(user, record), name):
        raise HTTPException(404, "evidencia no encontrada")
    if not re.fullmatch(r"[0-9a-f]{32}", submission_id):
        raise HTTPException(404, "evidencia no encontrada")
    path = task_dir(task_id) / "verdicts" / submission_id / name
    if not path.is_file():
        raise HTTPException(404, "evidencia no encontrada")
    return FileResponse(path)


@app.get("/tasks/{task_id}/submissions/{submission_id}/source.zip")
def get_source(task_id: str, submission_id: str, user: CurrentUser) -> StreamingResponse:
    """Código verificado. El cliente solo puede descargarlo cuando se liberó el pago de ESA entrega."""
    record = _require(task_id, user, "client", "freelancer", "arbiter")
    role = role_in_task(user, record)
    report = _verdict(task_id, submission_id)
    if role == "client" and not (record.escrow.get("status") == "released"
                                 and record.escrow.get("released_submission_id") == submission_id):
        raise HTTPException(403, "El código se entrega al cliente cuando se libera el pago")
    path = source_archive_path(task_id, submission_id)
    if not path.exists():
        raise HTTPException(404, "archivo no disponible")
    data = path.read_bytes()
    expected = report["verdict"]["workspace_hash"]
    if _zip_hash(data) != expected:  # nunca se entrega un archivo que no coincide con lo verificado
        raise HTTPException(500, "el archivo no coincide con el workspace_hash registrado")
    return StreamingResponse(io.BytesIO(data), media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="{task_id[:8]}-{submission_id[:8]}.zip"',
        "X-Workspace-Hash": expected,
    })


def _zip_hash(data: bytes) -> str:
    import tempfile
    import zipfile

    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            zf.extractall(tmp)
        return hash_workspace(Path(tmp))


# --- Helpers -------------------------------------------------------------------------------

def _load(task_id: str) -> TaskRecord:
    try:
        return load_task(task_id)
    except KeyError as exc:
        raise HTTPException(404, "Tarea no encontrada") from exc


def _require(task_id: str, user: User, *roles: str) -> TaskRecord:
    """La tarea, si el usuario tiene uno de los roles. Si no, 404 (no se revela que existe)."""
    record = _load(task_id)
    role = role_in_task(user, record)
    if role not in roles:
        if role == "visitor":
            raise HTTPException(404, "Tarea no encontrada")
        raise HTTPException(403, f"Acción no permitida para el rol {role}")
    return record


def _verdict(task_id: str, submission_id: str) -> dict:
    try:
        return load_verdict(task_id, submission_id)
    except KeyError as exc:
        raise HTTPException(404, "Entrega no encontrada") from exc


def _task_view(record: TaskRecord, user: User) -> dict:
    view = record.public_view()
    client = get_user(record.client_id) if record.client_id else None
    freelancer = get_user(record.freelancer_id) if record.freelancer_id else None
    view["role"] = role_in_task(user, record)
    view["client_name"] = client.name if client else None
    view["freelancer_name"] = freelancer.name if freelancer else None
    return view


# --- Ingesta + ejecución (fase 1, solo árbitro) --------------------------------------------

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


@app.post("/submissions", response_model=SubmissionResponse)
def create_submission(
    user: CurrentUser,
    repo: Annotated[str | None, Form()] = None,
    sha: Annotated[str | None, Form()] = None,
    command: Annotated[str, Form()] = DEFAULT_COMMAND,
    zip_file: Annotated[UploadFile | None, File(alias="zip")] = None,
) -> SubmissionResponse:
    """Herramienta de diagnóstico del árbitro: ingiere y corre un comando en el sandbox."""
    if not user.is_arbiter:
        raise HTTPException(403, "Solo el árbitro puede ejecutar comandos arbitrarios")
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
def _ingested(repo: str | None, sha: str | None, zip_file: UploadFile | None,
              installation_id: int | None = None) -> Iterator[Workspace]:
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
        elif installation_id is not None:
            source = GitHubAppSource(installation_id=installation_id, repo=repo, sha=sha)
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
