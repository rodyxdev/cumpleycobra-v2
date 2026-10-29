"""Endpoints de tareas, con el LLM, la verificación y el escrow sustituidos."""

import pytest
from fastapi.testclient import TestClient

from backend import main, workflow
from backend.contract import drafting
from backend.contract.models import DraftReview
from backend.llm import LLMError
from backend.verification.suite import CriterionSuiteReport, HiddenSuite, write_suite_dir

from .conftest import make_zip
from .helpers import FIXTURES, FakeEscrow, make_user, ventas_draft

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


@pytest.fixture
def fake_chain(monkeypatch):
    fake = FakeEscrow()
    monkeypatch.setattr(main, "escrow_service", lambda: fake)
    return fake


@pytest.fixture
def people():
    return {"client": make_user("Cliente", "client"), "freelancer": make_user("Freelancer", "freelancer"),
            "other": make_user("Otro")}


def create(client, headers):
    resp = client.post("/tasks", json={"draft": ventas_draft().model_dump(mode="json")}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_create_task_freezes_rules_and_hides_test_code(client, fake_suite, people):
    from backend.contract import rules_hash

    task = create(client, people["client"])
    assert task["rules_hash"] == rules_hash(ventas_draft())
    assert task["status"] == "awaiting_plan_approval" and task["role"] == "client"
    public = client.get(f"/tasks/{task['task_id']}", headers=people["client"]).json()
    assert public["test_plan"] == {"C1": ["Verifica un caso nuevo."]}
    assert "test_secreto" not in str(public) and "zz,1,1" not in str(public)


def test_endpoints_require_a_token(client):
    assert client.post("/tasks", json={}).status_code == 401
    assert client.get("/tasks").status_code == 401
    assert client.get("/tasks", headers={"Authorization": "Bearer inventado"}).status_code == 401


def test_invalid_contract_is_rejected_before_generating(client, fake_suite, people):
    bad = ventas_draft()
    bad = bad.model_copy(update={"criteria": [*bad.criteria, bad.criteria[0]]})
    resp = client.post("/tasks", json={"draft": bad.model_dump(mode="json")}, headers=people["client"])
    assert resp.status_code == 422
    assert "repetido" in str(resp.json())


def test_full_flow_with_escrow(client, fake_suite, fake_chain, people, monkeypatch):
    seen = {}

    def fake_verify(record, workspace):
        seen["files"] = sorted(p.name for p in workspace.path.iterdir())
        return {"submission_id": "5" * 32, "profile": "script", "verdict_hash": "sha256:" + "c" * 64,
                "verdict": {"status": "approved", "approved": True, "stage": "final", "flags": {},
                            "workspace_hash": workspace.content_hash, "criteria": []},
                "criteria": [], "reasons": [], "signals": []}

    from backend.verification import verdict as verdict_mod

    monkeypatch.setattr(verdict_mod, "verify_submission", fake_verify)
    task = create(client, people["client"])
    tid = task["task_id"]
    data = make_zip({"ventas.py": (FIXTURES / "correcta" / "ventas.py").read_bytes()})

    # Sin plan aprobado no hay depósito; sin depósito nadie puede aceptar.
    assert client.post(f"/tasks/{tid}/escrow/deposit", json={"amount": "10", "deadline_days": 7},
                       headers=people["client"]).status_code == 409
    assert client.post(f"/tasks/{tid}/plan/approve", headers=people["freelancer"]).status_code == 404
    assert client.post(f"/tasks/{tid}/plan/approve", headers=people["client"]).status_code == 200
    assert client.post(f"/tasks/{tid}/accept", headers=people["freelancer"]).status_code == 409
    dep = client.post(f"/tasks/{tid}/escrow/deposit", json={"amount": "10", "deadline_days": 7}, headers=people["client"])
    assert dep.status_code == 200 and dep.json()["escrow"]["status"] == "funded"
    assert fake_chain.calls[0][:3] == ("deposit", tid, 10 * 10**7)

    # La tarea aparece como disponible para otros; el cliente no puede aceptarla.
    listed = client.get("/tasks", headers=people["freelancer"]).json()
    assert [t["task_id"] for t in listed] == [tid] and listed[0]["role"] == "visitor"
    assert client.post(f"/tasks/{tid}/accept", headers=people["client"]).status_code == 409
    # Un usuario sin billetera de demo no puede firmar por el backend.
    assert client.post(f"/tasks/{tid}/accept", headers=people["other"]).status_code == 409
    acc = client.post(f"/tasks/{tid}/accept", headers=people["freelancer"])
    assert acc.status_code == 200 and acc.json()["role"] == "freelancer"

    # Solo el freelancer entrega; al aprobarse, el árbitro libera con los hashes del veredicto.
    assert client.post(f"/tasks/{tid}/submissions", files={"zip": ("e.zip", data, "application/zip")},
                       headers=people["client"]).status_code == 403
    resp = client.post(f"/tasks/{tid}/submissions", files={"zip": ("e.zip", data, "application/zip")},
                       headers=people["freelancer"])
    assert resp.status_code == 200, resp.text
    assert seen["files"] == ["ventas.py"]
    assert resp.json()["payment"]["status"] == "released"
    release = [c for c in fake_chain.calls if c[0] == "release"][0]
    assert release[2].startswith("sha256:") and release[3] == "sha256:" + "c" * 64


def test_unknown_task_is_404(client, people):
    assert client.get("/tasks/" + "0" * 32, headers=people["client"]).status_code == 404
    assert client.get("/tasks/../../etc", headers=people["client"]).status_code == 404


def test_draft_and_review_endpoints(client, monkeypatch, people):
    monkeypatch.setattr(main, "draft_task", lambda text, mockups=None: ventas_draft())
    body = {"request": "Necesito una función de ventas"}
    assert client.post("/tasks/draft", json=body, headers=people["client"]).json()["title"]
    monkeypatch.setattr(drafting, "structured_completion", lambda *a, **k: DraftReview(ok=True, issues=[], summary="ok"))
    review = client.post("/tasks/draft/review", json={"draft": ventas_draft().model_dump(mode="json")},
                         headers=people["client"]).json()
    assert review["ok"] is True


def test_llm_failure_maps_to_502(client, monkeypatch, people):
    def boom(text, mockups=None):
        raise LLMError("429 agotado")

    monkeypatch.setattr(main, "draft_task", boom)
    resp = client.post("/tasks/draft", json={"request": "Necesito una función de ventas"}, headers=people["client"])
    assert resp.status_code == 502


def test_legacy_suite_is_refused(client, fake_suite, fake_chain, people):
    from backend.tasks import load_task, save_task

    task = create(client, people["client"])
    tid = task["task_id"]
    record = load_task(tid)
    freelancer_id = client.get("/me", headers=people["freelancer"]).json()["user_id"]
    save_task(record.model_copy(update={"suite_format": "inprocess/v1", "status": "open", "freelancer_id": freelancer_id,
                                        "escrow": {"status": "accepted"}}))
    data = make_zip({"ventas.py": (FIXTURES / "correcta" / "ventas.py").read_bytes()})
    resp = client.post(f"/tasks/{tid}/submissions", files={"zip": ("e.zip", data, "application/zip")},
                       headers=people["freelancer"])
    assert resp.status_code == 409 and "migrate" in resp.text
