import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.sandbox import SandboxResult, docker_available

from .conftest import ARBITER_TOKEN, make_zip
from .helpers import make_user


@pytest.fixture
def client():
    # /submissions (fase 1) ejecuta comandos arbitrarios: es una herramienta del árbitro.
    return TestClient(main.app, headers={"Authorization": f"Bearer {ARBITER_TOKEN}"})


def test_raw_command_endpoint_is_arbiter_only(fake_sandbox):
    anon = TestClient(main.app)
    assert anon.post("/submissions", data={"command": "id"}).status_code == 401
    user = TestClient(main.app, headers=make_user("alguien"))
    resp = user.post("/submissions", files={"zip": ("e.zip", make_zip({"a": b"a"}), "application/zip")})
    assert resp.status_code == 403 and fake_sandbox == []


@pytest.fixture
def fake_sandbox(monkeypatch):
    calls = []

    def fake_run(workspace, command, output_dir, **_):
        calls.append({"files": sorted(p.name for p in workspace.iterdir()), "command": command})
        return SandboxResult(0, "ok\n", "", False, False, 0.01, False, False, "fake", command)

    monkeypatch.setattr(main, "run_in_sandbox", fake_run)
    return calls


def post_zip(client, data: bytes, **form):
    return client.post("/submissions", files={"zip": ("entrega.zip", data, "application/zip")}, data=form)


def test_zip_submission_returns_hash_and_result(client, fake_sandbox, tmp_path):
    resp = post_zip(client, make_zip({"a.txt": b"A"}), command="cat a.txt")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["workspace_hash"].startswith("sha256:")
    assert body["source"] == {"kind": "zip"}
    assert body["sandbox"]["exit_code"] == 0
    assert fake_sandbox == [{"files": ["a.txt"], "command": "cat a.txt"}]
    # Nada queda en disco tras responder.
    assert not any((tmp_path / "work").rglob("a.txt"))


def test_same_zip_twice_same_hash(client, fake_sandbox):
    data = make_zip({"a.txt": b"A", "b/c.txt": b"C"})
    assert post_zip(client, data).json()["workspace_hash"] == post_zip(client, data).json()["workspace_hash"]


def test_zip_slip_is_rejected_with_422(client, fake_sandbox):
    resp = post_zip(client, make_zip({"../../evil.txt": b"x"}))
    assert resp.status_code == 422
    assert fake_sandbox == []


@pytest.mark.parametrize(
    "form,with_zip",
    [
        ({}, False),                                     # nada
        ({"repo": "o/n", "sha": "0" * 40}, True),        # ambos
        ({"repo": "o/n"}, False),                        # falta sha
    ],
)
def test_exactly_one_source_required(client, fake_sandbox, form, with_zip):
    files = {"zip": ("e.zip", make_zip({"a": b"a"}), "application/zip")} if with_zip else None
    resp = client.post("/submissions", data=form, files=files)
    assert resp.status_code == 422


def test_invalid_sha_rejected(client, fake_sandbox):
    resp = client.post("/submissions", data={"repo": "o/n", "sha": "main"})
    assert resp.status_code == 422
    assert "sha" in resp.json()["detail"]


@pytest.mark.skipif(not docker_available(), reason="Docker no está disponible")
def test_end_to_end_zip_through_real_sandbox(client):
    resp = post_zip(client, make_zip({"main.py": b"print(2 + 2)\n"}), command="python main.py")
    assert resp.status_code == 200, resp.text
    sandbox = resp.json()["sandbox"]
    assert sandbox["exit_code"] == 0
    assert sandbox["stdout"] == "4\n"
