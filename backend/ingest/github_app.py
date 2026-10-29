"""Implementación C (pendiente): repo privado vía GitHub App.

Plan previsto: con el `installation_id` pedir un token de instalación de corta vida
(JWT firmado con la clave privada de la App → POST /app/installations/{id}/access_tokens),
y reutilizar el mismo flujo init/fetch-por-SHA/checkout de `github_public`, pasando el
token por `http.extraHeader` (nunca en la URL, para que no acabe en logs).
"""

from __future__ import annotations

from pathlib import Path

from backend.ingest.base import GitHubAppSource


class GitHubAppFetcher:
    def fetch(self, source: GitHubAppSource, dest: Path) -> None:
        raise NotImplementedError("La ingesta de repos privados vía GitHub App aún no está implementada")
