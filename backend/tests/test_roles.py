"""Roles y confidencialidad del código, comprobados contra TODAS las rutas de la API.

CANARY aparece en el código entregado y en todo lugar del reporte donde un reporte real lleva
texto de la entrega. HIDDEN aparece donde un reporte real lleva contenido de los tests ocultos.
- El cliente no puede obtener CANARY por ninguna ruta GET hasta que se libera el pago.
- El freelancer ve su código (CANARY) pero nunca el contenido de los tests (HIDDEN).
- El árbitro ve todo.
"""

import io
import json
import re
import zipfile

import pytest
from fastapi.testclient import TestClient
from starlette.routing import Route

from backend import main, workflow
from backend.ingest import hash_workspace
from backend.tasks import evidence_dir
from backend.verification import verdict as verdict_mod
from backend.verification.suite import CriterionSuiteReport, HiddenSuite, write_suite_dir

from .conftest import ARBITER_TOKEN, make_zip
from .helpers import FakeEscrow, make_user, ventas_draft

CANARY = "CANARIO-7f3a9c"
HIDDEN = "OCULTO-test-4b2e"
SID = "a1" * 16

CODE = f'''"""Entrega con canario: {CANARY}."""


def resumir_ventas(texto: str) -> dict[str, int]:
    # {CANARY} en un comentario
    raise ValueError("{CANARY} en un mensaje de error")
'''


def canary_report(workspace_hash: str, approved: bool) -> dict:
    """Reporte con la forma de uno real, con CANARY/HIDDEN en cada campo sensible."""
    status = "approved" if approved else "manual_review"
    return {
        "submission_id": SID, "profile": "script", "verdict_hash": "sha256:" + "d" * 64,
        "verdict": {"schema": "cumpleycobra/verdict/v1", "status": status, "approved": approved, "stage": "final",
                    "workspace_hash": workspace_hash, "rules_hash": "sha256:" + "e" * 64, "suite_hash": "sha256:" + "f" * 64,
                    "criteria": [{"id": "C1", "status": "pass", "stage": "judge"}],
                    "flags": {"injection": False, "cheating": False, "tampering": True}},
        "reasons": [f"criterios ... {CANARY}"],
        "criteria": [
            {"id": "C1", "type": "test", "required": True, "status": "pass", "stage": "judge",
             "reasons": [f"razón con {CANARY}"],
             "tests": [{"nodeid": "test_c1.py::test_a", "name": "test_a", "outcome": "passed",
                        "summary": f"AssertionError: {CANARY}",
                        "message": f"ValueError: {CANARY}\n>   assert x == 1  # {HIDDEN}"}],
             "judge_verdict": "pass", "judge_reasoning": f"el código hace {CANARY}",
             "judge_evidence": [
                 {"kind": "code", "ref": "ventas.py:6", "quote": f"raise ValueError('{CANARY}')", "verified": True},
                 {"kind": "test_code", "ref": "test_c1.py:3", "quote": f"assert f() == 1  # {HIDDEN}", "verified": True},
                 {"kind": "test_output", "ref": "test_c1.py::test_a", "quote": f"{HIDDEN} {CANARY}", "verified": True},
             ],
             "static_evidence": [f"ventas.py:4: {CANARY}"]},
            {"id": "C7", "type": "static", "required": True, "status": "fail", "stage": "static",
             "reasons": [], "tests": [], "static_evidence": [f"ventas.py:2: import requests  # {CANARY}"]},
        ],
        "hygiene": {"blockers": [], "warnings": [f"requirements.txt: requisito {CANARY}"], "requirements": []},
        "signals": [{"kind": "tampering", "file": "ventas.py", "line": 3, "snippet": f"open('/hidden') {CANARY}",
                     "detail": "referencia a la infraestructura de test (/hidden)", "ref": "ventas.py:3"}],
        "judge": {"summary": f"resumen {CANARY}", "injection_detected": False, "error": None,
                  "injection_evidence": [], "cheating_detected": False,
                  "cheating_evidence": [{"kind": "test_code", "ref": "test_c1.py:1", "quote": HIDDEN}]},
        "test_run": {"stdout_tail": f"{CANARY}", "stderr_tail": f"{HIDDEN}"},
        "sut": {"state": {}, "logs_tail": f"[entrega] {CANARY}"},
        "source": {"kind": "zip"},
        "screenshots": [{"id": "S1", "page": "/", "viewport": "desktop", "label": "página completa", "file": "S1.png"}],
        "videos": ["video-desktop.webm"],
        "timings_s": {"total": 1.0},
        "llm_usage": [],
    }


@pytest.fixture
def world(monkeypatch):
    """Tarea aceptada, con una entrega verificada que contiene el canario."""
    fake = FakeEscrow()
    monkeypatch.setattr(main, "escrow_service", lambda: fake)

    def fake_generate(draft, dest):
        write_suite_dir(dest, {"test_c1.py": f"def test_a():\n    assert True  # {HIDDEN}\n"})
        return HiddenSuite(files={}, plan={"C1": ["Verifica algo."]}, suite_hash="sha256:fake",
                           reports=[CriterionSuiteReport(c.id, 1, True) for c in draft.criteria if c.type == "test"])

    monkeypatch.setattr(workflow, "generate_suite", fake_generate)
    approved = {"value": False}

    def fake_verify(record, workspace):
        ev = evidence_dir(record.task_id, SID)
        (ev / "S1.png").write_bytes(b"\x89PNG captura")
        (ev / "video-desktop.webm").write_bytes(b"webm")
        (ev / "results.json").write_text(json.dumps({"visible_text": CANARY}), encoding="utf-8")
        return canary_report(workspace.content_hash, approved["value"])

    monkeypatch.setattr(verdict_mod, "verify_submission", fake_verify)
    http = TestClient(main.app)
    h = {"client": make_user("Cliente", "client"), "freelancer": make_user("Freelancer", "freelancer"),
         "visitor": make_user("Curioso"), "arbiter": {"Authorization": f"Bearer {ARBITER_TOKEN}"}}
    tid = http.post("/tasks", json={"draft": ventas_draft().model_dump(mode="json")}, headers=h["client"]).json()["task_id"]
    http.post(f"/tasks/{tid}/plan/approve", headers=h["client"])
    http.post(f"/tasks/{tid}/escrow/deposit", json={"amount": "5", "deadline_days": 3}, headers=h["client"])
    http.post(f"/tasks/{tid}/accept", headers=h["freelancer"])
    resp = http.post(f"/tasks/{tid}/submissions", headers=h["freelancer"],
                     files={"zip": ("e.zip", make_zip({"ventas.py": CODE.encode()}), "application/zip")})
    assert resp.status_code == 200, resp.text
    return {"http": http, "h": h, "tid": tid, "fake": fake, "approved": approved}


def get_routes():
    return [r for r in main.app.routes if isinstance(r, Route) and "GET" in r.methods]


def all_get_responses(http, headers, tid):
    """Llama a cada ruta GET de la app, rellenando sus parámetros con valores reales."""
    out = {}
    for route in get_routes():
        params = re.findall(r"{(\w+)}", route.path)
        names = ["S1.png", "video-desktop.webm", "results.json"] if "name" in params else [None]
        for name in names:
            path = route.path.replace("{task_id}", tid).replace("{submission_id}", SID)
            if name:
                path = path.replace("{name}", name)
            resp = http.get(path, headers=headers)
            out[path] = resp
    return out


def test_every_get_route_is_covered(world):
    paths = {r.path for r in get_routes()}
    # Si se agrega una ruta GET nueva, este test la recorre automáticamente; esto solo documenta las actuales.
    assert {"/tasks/{task_id}/submissions/{submission_id}", "/tasks/{task_id}/submissions",
            "/tasks/{task_id}/submissions/{submission_id}/evidence/{name}",
            "/tasks/{task_id}/submissions/{submission_id}/source.zip", "/tasks/{task_id}/escrow"} <= paths


def test_client_cannot_obtain_code_by_any_route(world):
    responses = all_get_responses(world["http"], world["h"]["client"], world["tid"])
    for path, resp in responses.items():
        assert CANARY.encode() not in resp.content, path
        assert HIDDEN.encode() not in resp.content, path
    base = f"/tasks/{world['tid']}/submissions/{SID}"
    assert responses[base].status_code == 200
    assert responses[f"{base}/evidence/S1.png"].status_code == 200          # capturas sí
    assert responses[f"{base}/evidence/video-desktop.webm"].status_code == 200
    assert responses[f"{base}/evidence/results.json"].status_code == 404    # texto de la página no
    assert responses[f"{base}/source.zip"].status_code == 403               # código no, antes del pago


def test_client_view_keeps_what_the_client_needs(world):
    view = world["http"].get(f"/tasks/{world['tid']}/submissions/{SID}", headers=world["h"]["client"]).json()
    assert view["verdict"]["status"] == "manual_review" and view["verdict_hash"].startswith("sha256:")
    c1 = view["criteria"][0]
    assert c1["tests"] == [{"name": "test_a", "outcome": "passed"}] and "1/1" in c1["summary"]
    assert view["criteria"][1]["summary"] == "Static check failed at 1 location(s)."
    assert view["signals"] == [{"kind": "tampering", "detail": "The delivery appears to tamper with the test infrastructure."}]
    assert view["screenshots"][0]["id"] == "S1"


def test_freelancer_sees_own_code_but_never_hidden_tests(world):
    http, h, tid = world["http"], world["h"], world["tid"]
    responses = all_get_responses(http, h["freelancer"], tid)
    view = responses[f"/tasks/{tid}/submissions/{SID}"]
    assert CANARY.encode() in view.content  # su propio código y sus logs, sí
    for path, resp in responses.items():
        assert HIDDEN.encode() not in resp.content, path
    body = view.json()
    kinds = [e["kind"] for e in body["criteria"][0]["judge_evidence"]]
    assert "test_code" not in kinds
    assert body["criteria"][0]["tests"][0]["summary"] == f"AssertionError: {CANARY}"
    assert "message" not in body["criteria"][0]["tests"][0]
    assert responses[f"/tasks/{tid}/submissions/{SID}/source.zip"].status_code == 200


def test_arbiter_sees_everything(world):
    view = world["http"].get(f"/tasks/{world['tid']}/submissions/{SID}", headers=world["h"]["arbiter"])
    assert HIDDEN.encode() in view.content and CANARY.encode() in view.content


def test_visitor_cannot_see_the_task_once_taken(world):
    responses = all_get_responses(world["http"], world["h"]["visitor"], world["tid"])
    for path, resp in responses.items():
        if path.startswith("/tasks/"):
            assert resp.status_code == 404, path
        assert CANARY.encode() not in resp.content


def test_arbiter_cannot_release_a_non_approved_verdict(world):
    http, tid = world["http"], world["tid"]
    resp = http.post(f"/tasks/{tid}/escrow/release", json={"submission_id": SID}, headers=world["h"]["arbiter"])
    assert resp.status_code == 409
    resp = http.post(f"/tasks/{tid}/escrow/release", json={"submission_id": SID}, headers=world["h"]["client"])
    assert resp.status_code == 403
    assert not [c for c in world["fake"].calls if c[0] == "release"]


def test_after_payment_client_downloads_the_verified_code(world):
    http, h, tid = world["http"], world["h"], world["tid"]
    assert http.post(f"/tasks/{tid}/escrow/client-release", headers=h["freelancer"]).status_code == 403
    released = http.post(f"/tasks/{tid}/escrow/client-release", headers=h["client"])
    assert released.status_code == 200 and released.json()["escrow"]["status"] == "released"
    resp = http.get(f"/tasks/{tid}/submissions/{SID}/source.zip", headers=h["client"])
    assert resp.status_code == 200
    report = http.get(f"/tasks/{tid}/submissions/{SID}", headers=h["arbiter"]).json()
    assert resp.headers["X-Workspace-Hash"] == report["verdict"]["workspace_hash"]
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        assert zf.namelist() == ["ventas.py"] and CANARY.encode() in zf.read("ventas.py")
    # El ZIP descargado, descomprimido, da exactamente el workspace_hash registrado.
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        zipfile.ZipFile(io.BytesIO(resp.content)).extractall(tmp)
        assert hash_workspace(Path(tmp)) == report["verdict"]["workspace_hash"]
    # Lo que no es código sigue filtrado para el cliente.
    view = http.get(f"/tasks/{tid}/submissions/{SID}", headers=h["client"])
    assert CANARY.encode() not in view.content


def test_approved_submission_is_released_automatically(world, monkeypatch):
    http, h, tid = world["http"], world["h"], world["tid"]
    world["approved"]["value"] = True
    resp = http.post(f"/tasks/{tid}/submissions", headers=h["freelancer"],
                     files={"zip": ("e.zip", make_zip({"ventas.py": CODE.encode()}), "application/zip")})
    assert resp.json()["payment"]["status"] == "released"
    release = [c for c in world["fake"].calls if c[0] == "release"]
    assert release and release[0][3] == "sha256:" + "d" * 64
    # Ya pagada, la tarea no acepta más entregas.
    again = http.post(f"/tasks/{tid}/submissions", headers=h["freelancer"],
                      files={"zip": ("e.zip", make_zip({"ventas.py": b"x = 1\n"}), "application/zip")})
    assert again.status_code == 409


# --- Con reportes REALES (corridas de la fase 3) ---------------------------------------------

from pathlib import Path  # noqa: E402

from backend.views import client_view, freelancer_view  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


def code_lines(path: Path) -> list[str]:
    """Líneas de código con suficiente "sabor a código" como para que su aparición sea una fuga."""
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if len(line) >= 18 and any(ch in line for ch in "=()<>{}[]:"):
            lines.append(line)
    return lines


@pytest.mark.parametrize(
    "report_file,source",
    [
        ("script_adv_lee_tests.json", "ventas/adv_lee_tests/ventas.py"),
        ("web_inyeccion_oculta.json", "landing/inyeccion_oculta/index.html"),
    ],
)
def test_real_reports_client_view_contains_no_submission_code(report_file, source):
    report = json.loads((FIXTURES_DIR / "reports" / report_file).read_text(encoding="utf-8"))
    full = json.dumps(report, ensure_ascii=False)
    # Fragmentos de la entrega: líneas de código, texto oculto de la página, fragmentos de señales.
    fragments = code_lines(FIXTURES_DIR / source)
    fragments += [h["text"] for h in report.get("hidden_text", []) if len(h["text"]) >= 12]
    fragments += [s["snippet"] for s in report.get("signals", []) if len(s.get("snippet", "")) >= 12]
    present = [f for f in fragments if json.dumps(f, ensure_ascii=False)[1:-1] in full]
    assert present, "el reporte completo debería citar la entrega (si no, el test no prueba nada)"
    view = json.dumps(client_view(report), ensure_ascii=False)
    assert [f for f in present if json.dumps(f, ensure_ascii=False)[1:-1] in view] == []


def test_real_script_report_freelancer_view_hides_hidden_tests():
    report = json.loads((FIXTURES_DIR / "reports" / "script_adv_lee_tests.json").read_text(encoding="utf-8"))
    # Las líneas de traceback de pytest con código de los tests empiezan con ">" en los mensajes.
    hidden_lines = {line.strip() for c in report["criteria"] for t in c.get("tests", [])
                    for line in t.get("message", "").splitlines() if line.strip().startswith(">") and len(line.strip()) > 12}
    hidden_lines |= {line.strip() for line in (report.get("test_run") or {}).get("stdout_tail", "").splitlines()
                     if line.strip().startswith(">") and len(line.strip()) > 12}
    assert hidden_lines, "el reporte real debería traer código de tests en los mensajes"
    view = json.dumps(freelancer_view(report), ensure_ascii=False)
    assert [line for line in hidden_lines if line in view] == []
