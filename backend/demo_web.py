"""Demo del perfil web de punta a punta (Muse Spark, Docker y Playwright reales).

Uso: python -m backend.demo_web [--with-draft] [--rounds N] [--visual-repeats K] [--out resultados.json]

1. Sube los mockups (POST /mockups).
2. (opcional) pedido + mockups → /tasks/draft → /tasks/draft/review.
3. Confirma `fixtures/landing/contrato.json` y aprueba el plan.
4. Verifica las 5 entregas de la landing como ZIP.
5. Consistencia del juez visual: repite SOLO el juez K veces sobre las mismas capturas.
"""

from __future__ import annotations

import argparse
import io
import json
import statistics
import sys
import time
import zipfile
from collections import Counter
from pathlib import Path

from fastapi.testclient import TestClient

from backend.main import app

LANDING = Path(__file__).parent / "fixtures" / "landing"
EXPECTED = {
    "correcta": "approved",
    "correcta_npm": "approved",          # misma landing con package.json: npm ci (con scripts) + build sin red
    "vite_react": "approved",            # proyecto Vite 8 + React 19 real, generado con `npm create vite`
    "rota_movil": "rejected",
    "form_no_envia": "rejected",
    "inyeccion_oculta": "manual_review",  # correcta + texto oculto dirigido al evaluador
    "estructura_distinta": "rejected",    # pasa todo lo determinista, pero no se parece al mockup
}
IN_PRICE, OUT_PRICE = 1.25e-6, 4.25e-6


def zip_dir(path: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(path.rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts and "node_modules" not in f.parts and "dist" not in f.parts:
                zf.write(f, f.relative_to(path).as_posix())
    return buf.getvalue()


def check(resp, what: str) -> dict:
    if resp.status_code >= 400:
        print(f"ERROR en {what}: {resp.status_code} {resp.text[:2000]}")
        sys.exit(1)
    return resp.json()


def cost(usage: list[dict]) -> float:
    return sum(u["prompt_tokens"] * IN_PRICE + u["completion_tokens"] * OUT_PRICE for u in usage)


def repeat_visual_judge(task_id: str, submission_id: str, k: int) -> list[dict]:
    """Corre el juez visual k veces más sobre la MISMA evidencia (sin volver a renderizar)."""
    from dataclasses import asdict

    from backend.llm import track_usage
    from backend.tasks import evidence_dir, load_task
    from backend.web.verify import deterministic_facts, judge_images
    from backend.web.visual_judge import has_screenshot_ref, judge_visual

    record = load_task(task_id)
    evidence = evidence_dir(task_id, submission_id)
    results = json.loads((evidence / "results.json").read_text(encoding="utf-8"))
    images = judge_images(record.draft, results, evidence)
    runs = []
    for _ in range(k):
        with track_usage() as usage:
            t = time.monotonic()
            out = judge_visual(record.draft, images, deterministic_facts(record.draft, results))
            elapsed = time.monotonic() - t
        runs.append({
            "verdicts": {c.criterion_id: c.verdict if has_screenshot_ref(c.evidence, images) else "sin_evidencia"
                         for c in out.criteria},
            "injection": out.injection_detected, "latency_s": round(elapsed, 1), "usage": [asdict(u) for u in usage],
        })
    return runs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--with-draft", action="store_true")
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--visual-repeats", type=int, default=0)
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    client = TestClient(app)
    results: dict = {"draft": None, "verifications": [], "visual_consistency": {}}

    mockups = []
    for mid, name, desc in (("M1", "mockup_desktop.png", "escritorio"), ("M2", "mockup_mobile.png", "móvil")):
        up = check(client.post("/mockups", files={"file": (name, (LANDING / name).read_bytes(), "image/png")}), "mockup")
        mockups.append({"id": mid, "sha256": up["sha256"], "description": desc})
    print("[mockups]", ", ".join(f"{m['id']}={m['sha256'][:19]}…" for m in mockups))

    if args.with_draft:
        pedido = (LANDING / "pedido.txt").read_text(encoding="utf-8")
        t = time.monotonic()
        draft = check(client.post("/tasks/draft", json={"request": pedido, "mockups": mockups}), "draft")
        t_draft = time.monotonic() - t
        t = time.monotonic()
        review = check(client.post("/tasks/draft/review", json={"draft": draft}), "review")
        t_review = time.monotonic() - t
        kinds = Counter((c["type"], (c.get("web_check") or {}).get("kind")) for c in draft["criteria"])
        print(f"[draft] perfil={draft['profile']} {len(draft['criteria'])} criterios {dict(kinds)} en {t_draft:.1f}s; "
              f"[review] ok={review['ok']} issues={len(review['issues'])} en {t_review:.1f}s")
        for issue in review["issues"]:
            print(f"   - {issue['criterion_id']} {issue['severity']}/{issue['kind']}: {issue['explanation'][:150]}")
        results["draft"] = {"draft": draft, "review": review, "draft_s": round(t_draft, 1), "review_s": round(t_review, 1)}

    contract = json.loads((LANDING / "contrato.json").read_text(encoding="utf-8"))
    task = check(client.post("/tasks", json={"draft": contract}), "crear tarea")
    task_id = task["task_id"]
    check(client.post(f"/tasks/{task_id}/plan/approve"), "aprobar plan")
    print(f"[tarea] {task_id} rules_hash={task['rules_hash'][:23]}… suite_hash={task['suite_hash'][:23]}…")
    results["task"] = {"task_id": task_id, "rules_hash": task["rules_hash"], "suite_hash": task["suite_hash"],
                       "test_plan": task["test_plan"]}

    for rnd in range(1, args.rounds + 1):
        for name in args.only or EXPECTED:
            t = time.monotonic()
            report = check(client.post(f"/tasks/{task_id}/submissions",
                                       files={"zip": (f"{name}.zip", zip_dir(LANDING / name), "application/zip")}),
                           f"verificar {name}")
            elapsed = time.monotonic() - t
            v = report["verdict"]
            mark = "OK " if v["status"] == EXPECTED[name] else "XX "
            crit = " ".join(f"{c['id']}={c['status']}" for c in report["criteria"])
            flags = [k for k, on in v["flags"].items() if on]
            print(f"{mark}[r{rnd}] {name:<17} → {v['status']:<13} etapa={v['stage']:<6} {elapsed:5.1f}s "
                  f"${cost(report['llm_usage']):.4f}  {crit}  flags={flags}")
            results["verifications"].append({"round": rnd, "submission": name, "expected": EXPECTED[name],
                                             "elapsed_s": round(elapsed, 2), "report": report})

    if args.visual_repeats:
        firsts = {}
        for v in results["verifications"]:
            firsts.setdefault(v["submission"], v)
        for name, v in firsts.items():
            if not v["report"]["screenshots"]:
                continue
            base = {c["id"]: c["judge_verdict"] for c in v["report"]["criteria"] if c["type"] == "visual"}
            runs = repeat_visual_judge(task_id, v["report"]["submission_id"], args.visual_repeats)
            series = {cid: [base[cid]] + [r["verdicts"].get(cid) for r in runs] for cid in base}
            results["visual_consistency"][name] = {"series": series, "runs": runs}
            print(f"[consistencia] {name:<17} " + "  ".join(f"{cid}: {'/'.join(map(str, s))}" for cid, s in series.items())
                  + f"  inyección: {[v['report']['visual_judge']['injection_detected']] + [r['injection'] for r in runs]}")

    vs = results["verifications"]
    summary = {
        "verifications": len(vs),
        "matches_expected": sum(v["report"]["verdict"]["status"] == v["expected"] for v in vs),
        "avg_total_s": round(statistics.mean(v["elapsed_s"] for v in vs), 1),
        "avg_browser_s": round(statistics.mean(v["report"]["timings_s"].get("browser", 0) for v in vs), 1),
        "avg_visual_judge_s": round(statistics.mean(v["report"]["timings_s"].get("visual_judge", 0) for v in vs), 1),
        "avg_cost_usd": round(statistics.mean(cost(v["report"]["llm_usage"]) for v in vs), 4),
        "avg_prompt_tokens": round(statistics.mean(sum(u["prompt_tokens"] for u in v["report"]["llm_usage"]) for v in vs)),
        "avg_completion_tokens": round(statistics.mean(sum(u["completion_tokens"] for u in v["report"]["llm_usage"]) for v in vs)),
    }
    results["summary"] = summary
    print(json.dumps(summary, indent=1))
    if args.out:
        args.out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
