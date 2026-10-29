"""API HTTP de la fase 1: recibir una entrega, ingerirla y ejecutar un comando de prueba aislado."""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from backend.config import get_settings
from backend.ingest import (
    GitHubPublicSource,
    IngestError,
    ZipSource,
    fetch_submission,
    rmtree_force,
)
from backend.sandbox import SandboxUnavailable, run_in_sandbox

log = logging.getLogger(__name__)

DEFAULT_COMMAND = "ls -la && find . -type f | wc -l"

app = FastAPI(title="Cumple&Cobra v2", version="0.1.0")


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


@app.post("/submissions", response_model=SubmissionResponse)
def create_submission(
    repo: Annotated[str | None, Form()] = None,
    sha: Annotated[str | None, Form()] = None,
    command: Annotated[str, Form()] = DEFAULT_COMMAND,
    zip_file: Annotated[UploadFile | None, File(alias="zip")] = None,
) -> SubmissionResponse:
    """Acepta `repo`+`sha` (repo público de GitHub) **o** un archivo `zip` (multipart)."""
    s = get_settings()
    has_repo = bool(repo or sha)
    if has_repo == (zip_file is not None):
        raise HTTPException(422, "Envía 'repo' y 'sha', o bien un archivo 'zip' (exactamente uno de los dos)")
    if has_repo and not (repo and sha):
        raise HTTPException(422, "'repo' y 'sha' van juntos")
    if not command.strip():
        raise HTTPException(422, "'command' no puede estar vacío")

    submission_id = uuid.uuid4().hex
    run_dir = s.work_dir / "submissions" / submission_id
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
        output_dir = run_dir / "out"
        result = run_in_sandbox(workspace.path, command, output_dir)
        output_files = sorted(p.relative_to(output_dir).as_posix() for p in output_dir.rglob("*") if p.is_file())
        return SubmissionResponse(
            submission_id=submission_id,
            workspace_hash=workspace.content_hash,
            size_bytes=workspace.size_bytes,
            source=workspace.source,
            sandbox=SandboxRun(**asdict(result)),
            output_files=output_files,
        )
    except IngestError as exc:
        raise HTTPException(422, f"Entrega inválida: {exc}") from exc
    except NotImplementedError as exc:
        raise HTTPException(501, str(exc)) from exc
    except SandboxUnavailable as exc:
        raise HTTPException(503, f"Sandbox no disponible: {exc}") from exc
    finally:
        # En la fase 1 no se persiste nada: el workspace y el ZIP se descartan.
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
