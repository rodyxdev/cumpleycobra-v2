"""Implementación C: repo PRIVADO vía GitHub App.

El freelancer instala la App de la plataforma solo en su repo de entrega. Para cada entrega:
1. JWT RS256 firmado con la clave privada de la App (iss = App ID, vida de 9 minutos).
2. POST /app/installations/{id}/access_tokens pidiendo un token limitado a ESE repo y a
   `contents: read`. Si la instalación no incluye el repo, GitHub responde 422 y se rechaza.
3. El mismo init/fetch-por-SHA/checkout del flujo público. El token va a git como cabecera
   `http.<url>.extraHeader` por variables de entorno GIT_CONFIG_*: nunca en la URL ni en los
   argumentos (no aparece en la lista de procesos ni en logs), y se enmascara en los errores.
El token expira en una hora y no se guarda.

Configuración: GITHUB_APP_ID y GITHUB_APP_PRIVATE_KEY_PATH (o GITHUB_APP_PRIVATE_KEY con el PEM).
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

import jwt

from backend.ingest.base import GitHubAppSource, IngestError
from backend.ingest.github_public import checkout_sha, validate

API = "https://api.github.com"


def _private_key() -> str:
    pem = os.getenv("GITHUB_APP_PRIVATE_KEY")
    if pem:
        return pem.replace("\\n", "\n")
    path = os.getenv("GITHUB_APP_PRIVATE_KEY_PATH")
    if path and Path(path).exists():
        return Path(path).read_text(encoding="utf-8")
    raise IngestError("La GitHub App no está configurada (GITHUB_APP_ID + clave privada)")


def app_jwt(now: int | None = None) -> str:
    app_id = os.getenv("GITHUB_APP_ID")
    if not app_id:
        raise IngestError("La GitHub App no está configurada (GITHUB_APP_ID)")
    now = int(now or time.time())
    # iat 60 s en el pasado por desfase de relojes; GitHub acepta como máximo 10 minutos.
    return jwt.encode({"iat": now - 60, "exp": now + 9 * 60, "iss": app_id}, _private_key(), algorithm="RS256")


def _request(method: str, url: str, token: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}",
                                          "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "cumpleycobra-v2"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode("utf-8", "replace")
        raise IngestError(f"GitHub respondió {exc.code} en {url.removeprefix(API)}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise IngestError(f"no se pudo contactar a GitHub: {exc.reason}") from exc


def installation_token(installation_id: int, repo: str) -> str:
    """Token de instalación limitado a un único repo y a lectura de contenido."""
    name = repo.split("/", 1)[1]
    data = _request("POST", f"{API}/app/installations/{installation_id}/access_tokens", app_jwt(),
                    {"repositories": [name], "permissions": {"contents": "read"}})
    token = data.get("token")
    if not token:
        raise IngestError("GitHub no devolvió un token de instalación")
    return token


def auth_env(token: str) -> dict[str, str]:
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}",
    }


class GitHubAppFetcher:
    def fetch(self, source: GitHubAppSource, dest: Path) -> None:
        if source.installation_id <= 0:
            raise IngestError("installation_id inválido")
        repo, sha = validate(source)
        token = installation_token(source.installation_id, repo)
        checkout_sha(repo, sha, dest, auth_env(token))
