"""Demo de punta a punta de la fase 2, contra Muse Spark y Docker reales.

Uso:  python -m backend.demo [--skip-draft] [--rounds N] [--out resultados.json]

1. (opcional) pedido en texto libre → /tasks/draft → /tasks/draft/review
2. confirma el contrato de `fixtures/ventas/contrato.json` (el borrador ya corregido según la
   revisión, como lo haría el cliente) → /tasks (genera y valida la suite oculta)
3. aprueba el plan y verifica las 4 entregas de `fixtures/ventas/` como ZIP.
"""

from __future__ import annotations

import argparse
import io
import json
import statistics
import sys
import time
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from backend.main import app

FIXTURES = Path(__file__).parent / "fixtures" / "ventas"
EXPECTED = {
    "correcta": "approved",
    "incompleta": "rejected",
    "tramposa_hardcode": "rejected",
    "tramposa_inyeccion": "rejected",
    # Código correcto + texto dirigido al evaluador: nunca se aprueba automáticamente.
    "correcta_con_inyeccion": "manual_review",
    # Fase 3: intentos de atacar al verificador desde la entrega.
    "adv_conftest": "rejected",       # conftest.py que fuerza pass
    "adv_os_exit": "rejected",        # os._exit(0) para "terminar con éxito"
    "adv_lee_tests": "rejected",      # busca y lee los archivos de tests
}
SUBMISSIONS = list(EXPECTED)

# Experimento test_defect: se agrega a la suite un test cuyo valor esperado contradice el
# ejemplo público del contrato (300 → 310). La entrega correcta debe ir a revisión manual.
DEFECTIVE_TEST = '''

def test_ejemplo_publico_dos_productos_bis():
    """Verifica de nuevo el ejemplo público con dos productos distintos."""
    assert resumir_ventas("manzana,2,150\\npera,1,300") == {"manzana": 300, "pera": 310}
'''


def zip_dir(path: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(path.rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts:
                zf.write(f, f.relative_to(path).as_posix())
    return buf.getvalue()


def tokens(usage: list[dict]) -> dict:
    return {k: sum(u[k] for u in usage) for k in ("prompt_tokens", "completion_tokens", "reasoning_tokens")}


def check(resp, what: str) -> dict:
    if resp.status_code >= 400:
        print(f"ERROR en {what}: {resp.status_code} {resp.text[:2000]}")
        sys.exit(1)
    return resp.json()


def defect_experiment(client: TestClient, task_id: str) -> dict:
    """Clona la tarea con un test defectuoso agregado a test_c1.py y verifica la entrega correcta."""
    import shutil

    from backend.tasks import hidden_dir, load_task, new_task_id, save_task
    from backend.verification.suite import suite_hash

    original = load_task(task_id)
    clone_id = new_task_id()
    shutil.copytree(hidden_dir(task_id), hidden_dir(clone_id))
    target = hidden_dir(clone_id) / "test_c1.py"
    target.write_text(target.read_text(encoding="utf-8") + DEFECTIVE_TEST, encoding="utf-8", newline="\n")
    files = {p.name: p.read_text(encoding="utf-8") for p in sorted(hidden_dir(clone_id).glob("test_*.py"))}
    save_task(original.model_copy(update={"task_id": clone_id, "status": "open", "suite_hash": suite_hash(files)}))

    t = time.monotonic()
    report = check(client.post(f"/tasks/{clone_id}/submissions",
                               files={"zip": ("correcta.zip", zip_dir(FIXTURES / "correcta"), "application/zip")}),
                   "experimento test_defect")
    c1 = next(c for c in report["criteria"] if c["id"] == "C1")
    mark = "OK " if report["verdict"]["status"] == "manual_review" else "XX "
    print(f"{mark}[test_defect] correcta + test defectuoso → {report['verdict']['status']} "
          f"C1={c1['status']} juez={c1['judge_verdict']} {time.monotonic() - t:.1f}s  {c1['reasons']}")
    return {"expected": "manual_review", "elapsed_s": round(time.monotonic() - t, 2), "report": report}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-draft", action="store_true")
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--defect-experiment", action="store_true")
    parser.add_argument("--task-id", help="reutiliza una tarea existente (abierta) en vez de crear una")
    parser.add_argument("--only", nargs="*", help="solo estas entregas")
    args = parser.parse_args()
    client = TestClient(app)
    results: dict = {"draft": None, "task": None, "verifications": []}

    if not args.skip_draft:
        pedido = (FIXTURES / "pedido.txt").read_text(encoding="utf-8")
        t = time.monotonic()
        draft = check(client.post("/tasks/draft", json={"request": pedido}), "draft")
        t_draft = time.monotonic() - t
        t = time.monotonic()
        review = check(client.post("/tasks/draft/review", json={"draft": draft}), "review")
        t_review = time.monotonic() - t
        print(f"[draft] {len(draft['criteria'])} criterios en {t_draft:.1f}s; "
              f"[review] ok={review['ok']} issues={len(review['issues'])} en {t_review:.1f}s")
        for issue in review["issues"]:
            print(f"   - {issue['criterion_id']} {issue['severity']}/{issue['kind']}: {issue['explanation'][:160]}")
        results["draft"] = {"draft": draft, "review": review, "draft_s": round(t_draft, 1), "review_s": round(t_review, 1)}

    if args.task_id:
        task_id = args.task_id
        task = check(client.get(f"/tasks/{task_id}"), "cargar tarea") | {"suite_validation": []}
        t_task = 0.0
    else:
        contract = json.loads((FIXTURES / "contrato.json").read_text(encoding="utf-8"))
        t = time.monotonic()
        task = check(client.post("/tasks", json={"draft": contract}), "crear tarea")
        t_task = time.monotonic() - t
        task_id = task["task_id"]
    print(f"[tarea] {task_id} rules_hash={task['rules_hash'][:23]}… suite_hash={task['suite_hash'][:23]}… "
          f"en {t_task:.1f}s; validación: "
          + ", ".join(f"{s['criterion_id']}:{'ok' if s['accepted'] else 'RECHAZADO'}({s['attempts']})" for s in task["suite_validation"]))
    public = check(client.get(f"/tasks/{task_id}"), "vista pública")
    assert "def test_" not in json.dumps(public), "la vista pública no debe incluir código de tests"
    if public["status"] == "awaiting_plan_approval":
        check(client.post(f"/tasks/{task_id}/plan/approve"), "aprobar plan")
    results["task"] = {"task_id": task_id, "rules_hash": task["rules_hash"], "suite_hash": task["suite_hash"],
                       "creation_s": round(t_task, 1), "suite_validation": task["suite_validation"],
                       "test_plan": public["test_plan"]}

    for rnd in range(1, args.rounds + 1):
        for name in args.only or SUBMISSIONS:
            t = time.monotonic()
            report = check(client.post(f"/tasks/{task_id}/submissions",
                                       files={"zip": (f"{name}.zip", zip_dir(FIXTURES / name), "application/zip")}),
                           f"verificar {name}")
            elapsed = time.monotonic() - t
            v = report["verdict"]
            mark = "OK " if v["status"] == EXPECTED[name] else "XX "
            crit = " ".join(f"{c['id']}={c['status']}" for c in report["criteria"])
            flags = [k for k, on in v["flags"].items() if on]
            print(f"{mark}[r{rnd}] {name:<20} → {v['status']:<13} etapa={v['stage']:<6} {elapsed:5.1f}s  {crit}  flags={flags}")
            results["verifications"].append({
                "round": rnd, "submission": name, "expected": EXPECTED[name], "elapsed_s": round(elapsed, 2),
                "report": report,
            })

    if args.defect_experiment:
        results["defect_experiment"] = defect_experiment(client, task_id)

    vs = results["verifications"]
    judge_lat = [u["latency_s"] for r in vs for u in r["report"]["llm_usage"]]
    totals = [r["elapsed_s"] for r in vs]
    toks = [tokens(r["report"]["llm_usage"]) for r in vs]
    summary = {
        "verifications": len(vs),
        "matches_expected": sum(r["report"]["verdict"]["status"] == r["expected"] for r in vs),
        "avg_total_s": round(statistics.mean(totals), 1),
        "avg_judge_s": round(statistics.mean(judge_lat), 1) if judge_lat else None,
        "avg_tokens": {k: round(statistics.mean(t[k] for t in toks)) for k in toks[0]} if toks else None,
    }
    results["summary"] = summary
    print(json.dumps(summary, indent=1))
    if args.out:
        args.out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
