"""Endpoints de tareas, con el LLM y la verificación sustituidos."""

import pytest
from fastapi.testclient import TestClient

from backend import main, workflow
from backend.contract import drafting
from backend.contract.models import DraftReview
from backend.llm import LLMError
from backend.verification import verdict as verdict_mod
from backend.verification.suite import CriterionSuiteReport, HiddenSuite, write_suite_dir

from .conftest import make_zip
from .helpers import FIXTURES, ventas_draft

SECRET_TEST = "def test_secreto():\n    assert resumir_ventas('zz,1,1') == {'zz': 1}\n"


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture
def fake_suite(monkeypatch):
    def fake_generate(draft, dest):
        files = {"test_c1.py": SECRET_TEST}
        write_suite_dir(dest, files)
        reports = [CriterionSuiteReport(c.id, 1, True) for c in draft.criteria if c.type == "test"]
        return HiddenSuite(files=files, plan={"C1": ["Verifica un caso nuevo."]}, suite_hash="sha256:fake", reports=reports)

    monkeypatch.setattr(workflow, "generate_suite", fake_generate)


def create(client):
    resp = client.post("/tasks", json={"draft": ventas_draft().model_dump(mode="json")})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_create_task_freezes_rules_and_hides_test_code(client, fake_suite):
    from backend.contract import rules_hash

    task = create(client)
    assert task["rules_hash"] == rules_hash(ventas_draft())
    assert task["status"] == "awaiting_plan_approval"
    public = client.get(f"/tasks/{task['task_id']}").json()
    assert public["test_plan"] == {"C1": ["Verifica un caso nuevo."]}
    assert "test_secreto" not in str(public) and "zz,1,1" not in str(public)


def test_invalid_contract_is_rejected_before_generating(client, fake_suite):
    bad = ventas_draft()
    bad = bad.model_copy(update={"criteria": [*bad.criteria, bad.criteria[0]]})
    resp = client.post("/tasks", json={"draft": bad.model_dump(mode="json")})
    assert resp.status_code == 422
    assert "repetido" in str(resp.json())


def test_submissions_require_plan_approval(client, fake_suite, monkeypatch):
    seen = {}

    def fake_verify(record, workspace):
        seen["files"] = sorted(p.name for p in workspace.path.iterdir())
        return {"submission_id": "s" * 32, "verdict": {"status": "approved"}}

    monkeypatch.setattr(verdict_mod, "verify_submission", fake_verify)
    task = create(client)
    data = make_zip({"ventas.py": (FIXTURES / "correcta" / "ventas.py").read_bytes()})
    url = f"/tasks/{task['task_id']}/submissions"
    assert client.post(url, files={"zip": ("e.zip", data, "application/zip")}).status_code == 409
    assert client.post(f"/tasks/{task['task_id']}/plan/approve").status_code == 200
    assert client.post(f"/tasks/{task['task_id']}/plan/approve").status_code == 409
    resp = client.post(url, files={"zip": ("e.zip", data, "application/zip")})
    assert resp.status_code == 200 and resp.json()["verdict"]["status"] == "approved"
    assert seen["files"] == ["ventas.py"]


def test_unknown_task_is_404(client):
    assert client.get("/tasks/" + "0" * 32).status_code == 404
    assert client.get("/tasks/../../etc").status_code == 404


def test_draft_and_review_endpoints(client, monkeypatch):
    monkeypatch.setattr(main, "draft_task", lambda text: ventas_draft())
    assert client.post("/tasks/draft", json={"request": "Necesito una función de ventas"}).json()["title"]
    monkeypatch.setattr(drafting, "structured_completion", lambda *a, **k: DraftReview(ok=True, issues=[], summary="ok"))
    review = client.post("/tasks/draft/review", json={"draft": ventas_draft().model_dump(mode="json")}).json()
    assert review["ok"] is True


def test_llm_failure_maps_to_502(client, monkeypatch):
    def boom(text):
        raise LLMError("429 agotado")

    monkeypatch.setattr(main, "draft_task", boom)
    assert client.post("/tasks/draft", json={"request": "Necesito una función de ventas"}).status_code == 502


def test_legacy_suite_is_refused(client, fake_suite):
    from backend.tasks import load_task, save_task

    task = create(client)
    record = load_task(task["task_id"])
    save_task(record.model_copy(update={"suite_format": "inprocess/v1", "status": "open"}))
    data = make_zip({"ventas.py": (FIXTURES / "correcta" / "ventas.py").read_bytes()})
    resp = client.post(f"/tasks/{task['task_id']}/submissions", files={"zip": ("e.zip", data, "application/zip")})
    assert resp.status_code == 409 and "migrate" in resp.text
