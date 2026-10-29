"""Suite de tests ocultos: generación con Muse Spark, validación y ejecución.

La suite se genera al crear la tarea, un archivo por criterio de tipo `test`
(`test_c1.py`, ...), y se guarda fuera del workspace del freelancer. Antes de aceptarla:
1. cada archivo debe compilar;
2. ejecutada contra una entrega VACÍA y contra un STUB (los símbolos de la interfaz
   existen pero no hacen nada), ningún test puede pasar; y contra el stub cada archivo
   debe colectar al menos un test sin errores de colección.
Un archivo que no cumple se regenera con el motivo como feedback.
"""

from __future__ import annotations

import ast
import contextvars
import json
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from backend.canonical import canonical_hash
from backend.config import get_settings
from backend.contract.models import Criterion, Interface, TaskDraft
from backend.llm import structured_completion
from backend.sandbox import Mount, SandboxLimits, SandboxResult, ensure_runner_image, run_in_sandbox
from backend.verification.junit import TestOutcome, criterion_for_file, parse_junit, hidden_test_filename

HIDDEN_MOUNT = "/hidden"

# Archivos de infraestructura que escribimos nosotros (no el modelo ni el freelancer).
CONFTEST = '''\
"""Infraestructura de Cumple&Cobra (no generada por el modelo)."""
import asyncio
import sys

import pytest

# Al final de sys.path: la biblioteca estándar y pytest siempre ganan a un módulo
# del freelancer con el mismo nombre.
for _path in ("/workspace", "/deps"):
    if _path not in sys.path:
        sys.path.append(_path)


@pytest.fixture
def call_api():
    """Hace una petición HTTP a la app ASGI de la entrega (perfil api) sin red."""
    import httpx

    def _call(app, method, url, **kwargs):
        async def go():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                return await client.request(method, url, **kwargs)
        return asyncio.run(go())

    return _call
'''

PYTEST_INI = "[pytest]\njunit_family = xunit1\n"


def pytest_command(timeout_per_test: int) -> str:
    # cd /tmp + `python -I`: el directorio actual y PYTHONPATH no entran en sys.path, así que
    # un `pytest.py` en el workspace no puede suplantar a pytest.
    return (
        f"cd /tmp && python -I -m pytest {HIDDEN_MOUNT} -c {HIDDEN_MOUNT}/pytest.ini "
        f"--rootdir={HIDDEN_MOUNT} -p no:cacheprovider -q -rN --timeout={timeout_per_test} "
        # Sin esto, un archivo que no colecta interrumpe toda la suite.
        "--continue-on-collection-errors "
        "--junitxml=/out/junit.xml"
    )


class GeneratedTestFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    criterion_id: str
    plan: list[str] = Field(description="Una frase por test, en el idioma del cliente: qué se verifica")
    code: str = Field(description="Contenido completo del archivo pytest")


GENERATOR_SYSTEM = """\
Eres un ingeniero de QA que escribe tests de aceptación OCULTOS. El freelancer nunca verá este
código; solo verá el plan. Escribes un único archivo pytest para UN criterio del contrato.

Reglas:
- Importa solo lo que declara la interfaz (p. ej. `from ventas import resumir_ventas`), pytest y
  la biblioteca estándar. Nada de hypothesis ni otras librerías.
- Perfil "api": no importes clientes HTTP; usa la fixture `call_api(app, method, url, json=...)`,
  que devuelve un httpx.Response, con `from <modulo> import <app>`.
- Entre 3 y 8 funciones `test_...`, cada una con un docstring de una línea que diga qué verifica.
- Prueba SOLO el criterio indicado; usa los otros criterios solo como contexto.
- Cada valor esperado debe deducirse con certeza del contrato o de sus ejemplos. Si haces
  cuentas, deja el cálculo en un comentario (p. ej. `# 2*150 + 1*300 = 600`). No pruebes nada que
  el contrato no especifique (por ejemplo, el texto exacto de un mensaje si solo se exige que
  contenga algo).
- Usa entradas DISTINTAS de los ejemplos públicos además de los propios ejemplos, para que una
  entrega que solo memorice los ejemplos falle.
- Las aserciones deben comprobar el valor exacto (`==`, `pytest.raises(...)`), nunca solo el tipo
  o que "no falle". Un stub que devuelve None debe fallar TODOS los tests.
- Deterministas: sin red, sin hora actual, sin aleatoriedad, sin escribir fuera de tmp_path.
- plan: una frase por test, en el mismo orden, entendible por el cliente, sin revelar valores
  concretos de entrada/salida que no estén en los ejemplos públicos.
"""


def build_stub(interface: Interface, dest: Path) -> None:
    """Workspace con los símbolos de la interfaz que no implementan nada."""
    for sym in interface.symbols:
        parts = sym.module.split(".")
        pkg = dest
        for part in parts[:-1]:
            pkg = pkg / part
            pkg.mkdir(parents=True, exist_ok=True)
            (pkg / "__init__.py").touch()
        module_file = pkg / f"{parts[-1]}.py"
        with module_file.open("a", encoding="utf-8") as fh:
            if sym.kind == "function":
                fh.write(f"\ndef {sym.name}(*args, **kwargs):\n    return None\n")
            elif sym.kind == "class":
                fh.write(
                    f"\nclass {sym.name}:\n"
                    "    def __init__(self, *args, **kwargs):\n        pass\n\n"
                    "    def __getattr__(self, name):\n        return lambda *a, **k: None\n"
                )
            elif sym.kind == "asgi_app":
                fh.write(
                    f"\nasync def {sym.name}(scope, receive, send):\n"
                    "    if scope['type'] == 'lifespan':\n"
                    "        while True:\n"
                    "            message = await receive()\n"
                    "            if message['type'] == 'lifespan.startup':\n"
                    "                await send({'type': 'lifespan.startup.complete'})\n"
                    "            elif message['type'] == 'lifespan.shutdown':\n"
                    "                await send({'type': 'lifespan.shutdown.complete'})\n"
                    "                return\n"
                    "    await send({'type': 'http.response.start', 'status': 501, 'headers': []})\n"
                    "    await send({'type': 'http.response.body', 'body': b''})\n"
                )


def write_suite_dir(dest: Path, files: dict[str, str]) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "conftest.py").write_text(CONFTEST, encoding="utf-8", newline="\n")
    (dest / "pytest.ini").write_text(PYTEST_INI, encoding="utf-8", newline="\n")
    for name, code in files.items():
        (dest / name).write_text(code, encoding="utf-8", newline="\n")


def suite_hash(files: dict[str, str]) -> str:
    return canonical_hash({"conftest.py": CONFTEST, "pytest.ini": PYTEST_INI, **files})


@dataclass
class SuiteRun:
    sandbox: SandboxResult
    outcomes: list[TestOutcome]
    report_found: bool


def run_suite(workspace: Path, suite_dir: Path, *, deps_mount: Mount | None = None) -> SuiteRun:
    s = get_settings()
    image = ensure_runner_image()
    mounts = [Mount(str(suite_dir), HIDDEN_MOUNT)]
    if deps_mount:
        mounts.append(deps_mount)
    with tempfile.TemporaryDirectory(prefix="cc-out-") as tmp:
        out = Path(tmp)
        result = run_in_sandbox(
            workspace, pytest_command(s.hidden_test_timeout_s), out,
            image=image, limits=SandboxLimits.from_settings(), mounts=tuple(mounts),
        )
        report = out / "junit.xml"
        outcomes = parse_junit(report.read_bytes()) if report.exists() else []
        return SuiteRun(sandbox=result, outcomes=outcomes, report_found=report.exists())


def _test_function_names(code: str) -> list[str]:
    tree = ast.parse(code)
    return [n.name for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")]


def static_problems(code: str) -> list[str]:
    try:
        compile(code, "<test>", "exec")
        names = _test_function_names(code)
    except SyntaxError as exc:
        return [f"no compila: {exc}"]
    return [] if names else ["no define ninguna función test_ a nivel de módulo"]


def validation_problems(file: str, empty_run: SuiteRun, stub_run: SuiteRun) -> list[str]:
    """Motivos por los que un archivo de tests no sirve (vacío si sirve)."""
    problems = []
    for label, run in (("vacía", empty_run), ("stub", stub_run)):
        if not run.report_found:
            problems.append(f"la ejecución contra la entrega {label} no produjo reporte: {run.sandbox.stderr[-500:]}")
            continue
        for o in run.outcomes:
            if o.file == file and o.outcome == "passed":
                problems.append(f"{o.name} PASA contra la entrega {label}: no comprueba el comportamiento")
    stub_cases = [o for o in stub_run.outcomes if o.file == file]
    if any(o.nodeid == file for o in stub_cases):  # error de colección
        msg = next(o.message for o in stub_cases if o.nodeid == file)
        problems.append(f"error al colectar el archivo contra el stub (¿import inválido?): {msg[:800]}")
    elif not stub_cases:
        problems.append("pytest no colectó ningún test de este archivo")
    return problems


def _generate(draft: TaskDraft, criterion: Criterion, previous: GeneratedTestFile | None, problems: list[str]) -> GeneratedTestFile:
    payload = {
        "contrato": draft.model_dump(mode="json"),
        "criterio_objetivo": criterion.model_dump(mode="json"),
        "nombre_de_archivo": hidden_test_filename(criterion.id),
    }
    messages = [
        {"role": "system", "content": GENERATOR_SYSTEM},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=1)},
    ]
    if previous is not None:
        messages += [
            {"role": "assistant", "content": previous.model_dump_json()},
            {"role": "user", "content": "La validación rechazó el archivo:\n- " + "\n- ".join(problems)
             + "\nReescríbelo completo corrigiendo esos problemas."},
        ]
    result = structured_completion(messages, GeneratedTestFile, schema_name="hidden_tests")
    return result.model_copy(update={"criterion_id": criterion.id})


@dataclass
class CriterionSuiteReport:
    criterion_id: str
    attempts: int
    accepted: bool
    problems: list[str] = field(default_factory=list)


@dataclass
class HiddenSuite:
    files: dict[str, str]
    plan: dict[str, list[str]]
    suite_hash: str
    reports: list[CriterionSuiteReport]

    @property
    def accepted(self) -> bool:
        return all(r.accepted for r in self.reports)


def _parallel(fn, items):
    with ThreadPoolExecutor(max_workers=max(1, min(6, len(items)))) as pool:
        futures = [pool.submit(contextvars.copy_context().run, fn, item) for item in items]
        return [f.result() for f in futures]


def generate_suite(draft: TaskDraft, dest: Path, max_regenerations: int | None = None) -> HiddenSuite:
    """Genera, valida y escribe en `dest` la suite oculta del contrato."""
    max_regen = get_settings().suite_max_regenerations if max_regenerations is None else max_regenerations
    criteria = [c for c in draft.criteria if c.type == "test"]
    generated: dict[str, GeneratedTestFile] = {}
    problems: dict[str, list[str]] = {c.id: [] for c in criteria}
    attempts: dict[str, int] = {c.id: 0 for c in criteria}
    pending = list(criteria)

    with tempfile.TemporaryDirectory(prefix="cc-suite-") as tmp:
        tmp = Path(tmp)
        empty_ws, stub_ws = tmp / "empty", tmp / "stub"
        empty_ws.mkdir()
        stub_ws.mkdir()
        build_stub(draft.interface, stub_ws)

        for _round in range(max_regen + 1):
            if not pending:
                break

            def make(c: Criterion) -> GeneratedTestFile:
                return _generate(draft, c, generated.get(c.id), problems[c.id])

            for c, gen in zip(pending, _parallel(make, pending), strict=True):
                generated[c.id] = gen
                attempts[c.id] += 1
                problems[c.id] = static_problems(gen.code)

            files = {hidden_test_filename(cid): g.code for cid, g in generated.items() if not static_problems(g.code)}
            suite_dir = tmp / f"round{_round}"
            write_suite_dir(suite_dir, files)
            empty_run = run_suite(empty_ws, suite_dir)
            stub_run = run_suite(stub_ws, suite_dir)
            for c in pending:
                if not problems[c.id]:
                    problems[c.id] = validation_problems(hidden_test_filename(c.id), empty_run, stub_run)
            pending = [c for c in pending if problems[c.id]]

    files = {hidden_test_filename(cid): g.code for cid, g in generated.items()}
    write_suite_dir(dest, files)
    return HiddenSuite(
        files=files,
        plan={cid: g.plan for cid, g in generated.items()},
        suite_hash=suite_hash(files),
        reports=[CriterionSuiteReport(c.id, attempts[c.id], not problems[c.id], problems[c.id]) for c in criteria],
    )


def outcomes_by_criterion(outcomes: list[TestOutcome]) -> dict[str, list[TestOutcome]]:
    grouped: dict[str, list[TestOutcome]] = {}
    for o in outcomes:
        cid = criterion_for_file(o.file)
        if cid:
            grouped.setdefault(cid, []).append(o)
    return grouped
