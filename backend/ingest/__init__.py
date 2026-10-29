"""Capa de entrada: convierte una fuente (repo+SHA, ZIP, ...) en un `Workspace` con hash."""

from __future__ import annotations

import uuid
from pathlib import Path

from backend.config import get_settings
from backend.ingest.base import (
    Fetcher,
    GitHubAppSource,
    GitHubPublicSource,
    IngestError,
    SubmissionSource,
    Workspace,
    ZipSource,
    rmtree_force,
)
from backend.ingest.github_app import GitHubAppFetcher
from backend.ingest.github_public import GitHubPublicFetcher
from backend.ingest.hashing import hash_workspace, tree_size
from backend.ingest.zip_upload import ZipFetcher

FETCHERS: dict[str, Fetcher] = {
    "github_public": GitHubPublicFetcher(),
    "zip": ZipFetcher(),
    "github_app": GitHubAppFetcher(),
}


def _describe(source: GitHubPublicSource | ZipSource | GitHubAppSource) -> dict:
    # La ruta local del ZIP es un detalle del servidor; no se expone.
    return source.model_dump(mode="json", exclude={"path"})


def fetch_submission(
    source: GitHubPublicSource | ZipSource | GitHubAppSource,
    dest_root: Path | None = None,
) -> Workspace:
    """Materializa la entrega en un directorio nuevo y calcula su hash de contenido.

    Si algo falla, el directorio parcial se borra y se propaga la excepción.
    """
    s = get_settings()
    dest_root = dest_root or s.work_dir / "workspaces"
    dest = dest_root / uuid.uuid4().hex
    dest.mkdir(parents=True)
    try:
        FETCHERS[source.kind].fetch(source, dest)
        size = tree_size(dest)
        if size > s.workspace_max_bytes:
            raise IngestError(f"el workspace supera {s.workspace_max_bytes} bytes")
        return Workspace(path=dest, content_hash=hash_workspace(dest), source=_describe(source), size_bytes=size)
    except BaseException:
        rmtree_force(dest)
        raise


__all__ = [
    "GitHubAppSource",
    "GitHubPublicSource",
    "IngestError",
    "SubmissionSource",
    "Workspace",
    "ZipSource",
    "fetch_submission",
    "hash_workspace",
    "rmtree_force",
]
