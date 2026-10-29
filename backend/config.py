"""Configuración leída de variables de entorno (y de `.env` en la raíz del repo)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw else default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw else default


@dataclass(frozen=True)
class Settings:
    # LLM
    model_api_key: str | None
    model_base_url: str
    muse_model: str
    llm_timeout_s: float
    llm_max_attempts: int
    # Ingesta
    work_dir: Path
    zip_max_upload_bytes: int
    zip_max_uncompressed_bytes: int
    zip_max_files: int
    git_timeout_s: float
    workspace_max_bytes: int
    # Sandbox
    sandbox_image: str
    sandbox_cpus: float
    sandbox_memory: str
    sandbox_pids: int
    sandbox_timeout_s: float
    sandbox_output_limit: int


def get_settings() -> Settings:
    return Settings(
        model_api_key=os.getenv("MODEL_API_KEY"),
        model_base_url=os.getenv("MODEL_BASE_URL", "https://api.meta.ai/v1"),
        muse_model=os.getenv("MUSE_MODEL", "muse-spark-1.3"),
        llm_timeout_s=_float("LLM_TIMEOUT_S", 120.0),
        llm_max_attempts=_int("LLM_MAX_ATTEMPTS", 4),
        work_dir=Path(os.getenv("WORK_DIR", str(REPO_ROOT / ".data"))),
        zip_max_upload_bytes=_int("ZIP_MAX_UPLOAD_BYTES", 50 * 1024 * 1024),
        zip_max_uncompressed_bytes=_int("ZIP_MAX_UNCOMPRESSED_BYTES", 200 * 1024 * 1024),
        zip_max_files=_int("ZIP_MAX_FILES", 10_000),
        git_timeout_s=_float("GIT_TIMEOUT_S", 120.0),
        workspace_max_bytes=_int("WORKSPACE_MAX_BYTES", 500 * 1024 * 1024),
        sandbox_image=os.getenv("SANDBOX_IMAGE", "python:3.12-slim"),
        sandbox_cpus=_float("SANDBOX_CPUS", 1.0),
        sandbox_memory=os.getenv("SANDBOX_MEMORY", "512m"),
        sandbox_pids=_int("SANDBOX_PIDS", 256),
        sandbox_timeout_s=_float("SANDBOX_TIMEOUT_S", 60.0),
        sandbox_output_limit=_int("SANDBOX_OUTPUT_LIMIT", 64 * 1024),
    )
