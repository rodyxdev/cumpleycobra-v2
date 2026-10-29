"""Implementación C (repo privado vía GitHub App) con la API de GitHub y git sustituidos."""

import base64
import json

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from backend.ingest import GitHubAppSource, IngestError, fetch_submission, github_app, github_public

SHA = "a" * 40


@pytest.fixture
def app_key(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    monkeypatch.setenv("GITHUB_APP_ID", "123456")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", pem.replace("\n", "\\n"))
    return key.public_key()


def test_app_jwt_is_rs256_signed_with_short_life(app_key):
    token = github_app.app_jwt(now=1_800_000_000)
    claims = jwt.decode(token, app_key, algorithms=["RS256"], options={"verify_exp": False, "verify_iat": False})
    assert claims == {"iat": 1_800_000_000 - 60, "exp": 1_800_000_000 + 540, "iss": "123456"}


def test_installation_token_is_scoped_to_one_repo_read_only(app_key, monkeypatch):
    calls = []

    def fake_request(method, url, token, body=None):
        calls.append((method, url, body))
        return {"token": "ghs_tokenSecreto123", "expires_at": "2026-10-29T00:00:00Z"}

    monkeypatch.setattr(github_app, "_request", fake_request)
    assert github_app.installation_token(42, "freelancer/entrega-privada") == "ghs_tokenSecreto123"
    method, url, body = calls[0]
    assert method == "POST" and url.endswith("/app/installations/42/access_tokens")
    assert body == {"repositories": ["entrega-privada"], "permissions": {"contents": "read"}}


def test_fetch_passes_token_only_through_env_and_masks_it(app_key, monkeypatch, tmp_path):
    monkeypatch.setattr(github_app, "_request", lambda *a, **k: {"token": "ghs_tokenSecreto123"})
    seen = []

    def fake_git(args, cwd, timeout_s, extra_env=None):
        seen.append((args, extra_env))
        if args[0] == "cat-file":
            return "commit"
        if args[0] == "rev-parse":
            return SHA
        if args[0] == "checkout":
            (cwd / "main.py").write_text("print('privado')\n", encoding="utf-8")
        return ""

    monkeypatch.setattr(github_public, "_git", fake_git)
    ws = fetch_submission(GitHubAppSource(installation_id=7, repo="freelancer/entrega-privada", sha=SHA))
    assert (ws.path / "main.py").exists() and ws.source["kind"] == "github_app"
    for args, _env in seen:
        assert "ghs_tokenSecreto123" not in " ".join(args)  # nunca en argumentos (lista de procesos)
        assert not any("x-access-token" in a for a in args)
    fetch_env = next(env for args, env in seen if args[0] == "fetch")
    header = base64.b64decode(fetch_env["GIT_CONFIG_VALUE_0"].split()[-1]).decode()
    assert header == "x-access-token:ghs_tokenSecreto123"
    assert fetch_env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"
    # El token solo acompaña al fetch.
    assert all(env is None for args, env in seen if args[0] != "fetch")
    assert "https://github.com/freelancer/entrega-privada.git" in seen[1][0]


def test_git_errors_never_echo_the_token(monkeypatch, tmp_path):
    import subprocess

    class Proc:
        returncode = 128
        stdout = ""
        stderr = "fatal: could not read from AUTHORIZATION: basic eC1hY2Nlc3MtdG9rZW46Z2hzX3Rva2VuU2VjcmV0bzEyMw=="

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Proc())
    env = github_app.auth_env("ghs_tokenSecreto123")
    with pytest.raises(IngestError) as info:
        github_public._git(["fetch"], tmp_path, 5, env)
    assert "eC1hY2Nlc3M" not in str(info.value) and "***" in str(info.value)


def test_repo_not_in_installation_is_rejected(app_key, monkeypatch):
    def fake_request(method, url, token, body=None):
        raise IngestError("GitHub respondió 422 en /app/installations/7/access_tokens: repositorio no instalado")

    monkeypatch.setattr(github_app, "_request", fake_request)
    with pytest.raises(IngestError, match="422"):
        fetch_submission(GitHubAppSource(installation_id=7, repo="otro/repo", sha=SHA))


def test_invalid_inputs_rejected_before_calling_github(app_key, monkeypatch):
    monkeypatch.setattr(github_app, "_request", lambda *a, **k: pytest.fail("no debería llamar a GitHub"))
    with pytest.raises(IngestError):
        fetch_submission(GitHubAppSource(installation_id=7, repo="o/n", sha="main"))
    with pytest.raises(IngestError):
        fetch_submission(GitHubAppSource(installation_id=0, repo="o/n", sha=SHA))


def test_config_reports_github_app_state(app_key):
    from fastapi.testclient import TestClient

    from backend.main import app

    assert TestClient(app).get("/config").json()["github_app_configured"] is True
    json.dumps({})  # silencia el import en linters
