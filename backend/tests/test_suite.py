"""Suite oculta: stub, JUnit, validación y bucle de regeneración (con LLM y sandbox falsos)."""

import subprocess
import sys

import pytest

from backend.sandbox import SandboxResult
from backend.verification import suite as suite_mod
from backend.verification.junit import criterion_for_file, parse_junit, hidden_test_filename
from backend.verification.suite import (
    GeneratedTestFile,
    SuiteRun,
    build_stub,
    static_problems,
    validation_problems,
)

from .helpers import ventas_draft

JUNIT = b"""<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest">
 <testcase classname="" name="test_c2" time="0.0"><error message="collection failure">ImportError: x</error></testcase>
 <testcase classname="test_c1" name="test_ok" file="test_c1.py" line="3" time="0.0"/>
 <testcase classname="test_c1" name="test_bad" file="test_c1.py" line="7" time="0.0">
  <failure message="AssertionError: assert None == {}">def test_bad(): ...</failure></testcase>
 <testcase classname="test_c3" name="test_skip" file="test_c3.py" time="0.0"><skipped message="nope"/></testcase>
</testsuite></testsuites>"""


def test_parse_junit():
    outcomes = {o.nodeid: o for o in parse_junit(JUNIT)}
    assert outcomes["test_c2.py"].outcome == "error"  # error de colección
    assert outcomes["test_c1.py::test_ok"].outcome == "passed"
    assert outcomes["test_c1.py::test_bad"].outcome == "failed"
    assert "assert None == {}" in outcomes["test_c1.py::test_bad"].message
    assert outcomes["test_c3.py::test_skip"].outcome == "skipped"
    assert outcomes["test_c1.py::test_ok"].criterion_id == "C1"


def test_file_names_round_trip():
    assert hidden_test_filename("C12") == "test_c12.py"
    assert criterion_for_file("test_c12.py") == "C12"
    assert criterion_for_file("conftest.py") is None


def test_stub_exposes_interface_and_returns_none(tmp_path):
    from backend.contract.models import InterfaceSymbol

    draft = ventas_draft()
    extra = [
        InterfaceSymbol(module="pkg.mod", name="Clase", kind="class", signature="Clase()", description=""),
        InterfaceSymbol(module="app", name="app", kind="asgi_app", signature="app", description=""),
    ]
    build_stub(draft.interface.model_copy(update={"symbols": draft.interface.symbols + extra}), tmp_path)
    code = (
        "import asyncio, sys; sys.path.insert(0, sys.argv[1])\n"
        "from ventas import resumir_ventas; from pkg.mod import Clase; from app import app\n"
        "assert resumir_ventas('a,1,2') is None\n"
        "assert Clase(1).cualquier_cosa(2) is None\n"
        "sent = []\n"
        "async def recv(): return {'type': 'http.request'}\n"
        "async def send(m): sent.append(m)\n"
        "asyncio.run(app({'type': 'http'}, recv, send))\n"
        "assert sent[0]['status'] == 501\n"
        "print('ok')\n"
    )
    out = subprocess.run([sys.executable, "-I", "-c", code, str(tmp_path)], capture_output=True, text=True)
    assert out.stdout.strip() == "ok", out.stderr


def test_static_problems():
    assert static_problems("def test_a():\n    assert 1\n") == []
    assert "no compila" in static_problems("def test_a(:\n")[0]
    assert static_problems("def helper():\n    pass\n")


def run_with(outcomes_xml: bytes | None) -> SuiteRun:
    sb = SandboxResult(1, "", "boom", False, False, 1.0, False, False, "img", "pytest")
    return SuiteRun(sb, parse_junit(outcomes_xml) if outcomes_xml else [], outcomes_xml is not None)


def xml(*cases: str) -> bytes:
    return ("<testsuites><testsuite>" + "".join(cases) + "</testsuite></testsuites>").encode()


FAIL = '<testcase classname="test_c1" name="{n}" file="test_c1.py"><failure message="x"/></testcase>'
PASS = '<testcase classname="test_c1" name="{n}" file="test_c1.py"/>'
COLLECT_ERR = '<testcase classname="" name="test_c1"><error message="collection failure">ModuleNotFoundError: hypothesis</error></testcase>'


def test_validation_accepts_tests_that_fail_on_empty_and_stub():
    empty = run_with(xml(COLLECT_ERR))
    stub = run_with(xml(FAIL.format(n="test_a"), FAIL.format(n="test_b")))
    assert validation_problems("test_c1.py", empty, stub) == []


def test_validation_rejects_a_test_that_passes_against_the_stub():
    empty = run_with(xml(COLLECT_ERR))
    stub = run_with(xml(FAIL.format(n="test_a"), PASS.format(n="test_b")))
    problems = validation_problems("test_c1.py", empty, stub)
    assert len(problems) == 1 and "test_b PASA contra la entrega stub" in problems[0]


def test_validation_rejects_a_test_that_passes_against_empty():
    empty = run_with(xml(PASS.format(n="test_x")))
    stub = run_with(xml(FAIL.format(n="test_x")))
    assert "entrega vacía" in validation_problems("test_c1.py", empty, stub)[0]


def test_validation_rejects_collection_errors_against_stub_and_missing_reports():
    assert "colectar" in validation_problems("test_c1.py", run_with(xml()), run_with(xml(COLLECT_ERR)))[0]
    assert "no colectó" in validation_problems("test_c1.py", run_with(xml()), run_with(xml()))[0]
    assert "no produjo reporte" in validation_problems("test_c1.py", run_with(None), run_with(xml(FAIL.format(n="t"))))[0]


def test_generate_suite_regenerates_only_defective_files(tmp_path, monkeypatch):
    draft = ventas_draft()
    test_ids = [c.id for c in draft.criteria if c.type == "test"]
    calls: list[tuple[str, bool]] = []

    def fake_generate(d, criterion, previous, problems):
        calls.append((criterion.id, previous is not None))
        good = previous is not None or criterion.id != "C2"
        name = "test_bien" if good else "test_trivial"
        return GeneratedTestFile(criterion_id=criterion.id, plan=["Verifica algo."],
                                 code=f"def {name}():\n    assert True\n")

    def fake_run(workspace, suite_dir, **kw):
        cases = []
        for f in sorted(suite_dir.glob("test_c*.py")):
            stem = f.stem
            trivial = "test_trivial" in f.read_text()
            is_stub = workspace.name == "stub"
            tmpl = PASS if (trivial and is_stub) else FAIL
            cases.append(tmpl.replace("test_c1", stem).format(n="test_x"))
        return run_with(xml(*cases))

    monkeypatch.setattr(suite_mod, "_generate", fake_generate)
    monkeypatch.setattr(suite_mod, "run_suite", fake_run)
    result = suite_mod.generate_suite(draft, tmp_path / "hidden", max_regenerations=2)

    assert result.accepted
    assert [c for c, regen in calls if regen] == ["C2"]          # solo se regenera C2
    assert len(calls) == len(test_ids) + 1
    report = {r.criterion_id: r for r in result.reports}
    assert report["C2"].attempts == 2 and report["C1"].attempts == 1
    assert (tmp_path / "hidden" / "conftest.py").exists()
    assert sorted(p.name for p in (tmp_path / "hidden").glob("test_*.py")) == [hidden_test_filename(c) for c in test_ids]
    assert result.suite_hash.startswith("sha256:")


def test_generate_suite_gives_up_after_max_regenerations(tmp_path, monkeypatch):
    def always_trivial(d, criterion, previous, problems):
        return GeneratedTestFile(criterion_id=criterion.id, plan=["x"], code="def test_trivial():\n    assert True\n")

    def fake_run(workspace, suite_dir, **kw):
        tmpl = PASS if workspace.name == "stub" else FAIL
        return run_with(xml(*[tmpl.replace("test_c1", f.stem).format(n="t") for f in suite_dir.glob("test_c*.py")]))

    monkeypatch.setattr(suite_mod, "_generate", always_trivial)
    monkeypatch.setattr(suite_mod, "run_suite", fake_run)
    result = suite_mod.generate_suite(ventas_draft(), tmp_path / "hidden", max_regenerations=1)
    assert not result.accepted
    assert all(r.attempts == 2 and r.problems for r in result.reports)


@pytest.mark.parametrize("cid", ["C1", "C10"])
def test_plan_is_keyed_by_criterion(cid):
    assert criterion_for_file(hidden_test_filename(cid)) == cid
