"""Verificación del perfil web por capas.

1. Higiene: lockfile obligatorio con package.json, dependencias npm dentro de las permitidas,
   paquetes resueltos solo desde registry.npmjs.org con integridad, sin .npmrc.
2. BUILD (npm ci --ignore-scripts con red; npm run build sin red) y servido en red interna.
3. Chequeos funcionales DETERMINISTAS con Playwright (lado confiable), capturas y video.
4. Señales: texto oculto vía DOM (contraste, tamaño 0, fuera de pantalla…), texto dirigido al
   evaluador en el texto visible, en el oculto o en el código fuente.
5. Juez visual (Muse Spark con capturas) SOLO para criterios `visual`, con IDs de captura
   verificados por código.
6. Regla final determinista (misma política que script/api).
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from backend.canonical import canonical_hash
from backend.contract.models import TaskDraft
from backend.ingest import Workspace
from backend.ingest.hashing import iter_entries
from backend.llm import LLMError, track_usage
from backend.tasks import TaskRecord, evidence_dir
from backend.verification.hygiene import HygieneReport
from backend.verification.static import Signal, scan_text_for_injection
from backend.verification.verdict import STAGES, CriterionDecision, Decision, verdict_payload
from backend.web.mockups import mockup_path
from backend.web.run import RUNNER, run_web_checks, web_spec
from backend.web.serve import prepare_site
from backend.web.visual_judge import JudgeImage, VisualOutput, has_screenshot_ref, judge_visual, valid_refs

SUITE_FORMAT_WEB = "web/v1"
_TEXT_SUFFIXES = {".html", ".htm", ".js", ".mjs", ".css", ".json", ".md", ".txt", ".svg", ".ts", ".tsx", ".jsx", ".vue"}
_BLOCKED = {".npmrc", ".yarnrc", ".yarnrc.yml", ".pnpmfile.cjs"}
_REGISTRY = "https://registry.npmjs.org/"


def web_suite_hash(draft: TaskDraft) -> str:
    """La "suite" web es el runner (código nuestro) más los chequeos del contrato."""
    runner = hashlib.sha256(RUNNER.read_bytes()).hexdigest()
    return canonical_hash({"format": SUITE_FORMAT_WEB, "runner_sha256": runner, "spec": web_spec(draft)})


def web_plan(draft: TaskDraft) -> dict[str, list[str]]:
    plan = {}
    for c in draft.criteria:
        if c.type == "test" and c.web_check:
            plan[c.id] = [f"Se comprueba en un navegador real ({c.web_check.kind}) en la página {c.web_check.page}: {c.text}"]
        elif c.type == "visual":
            plan[c.id] = [f"Se juzga con capturas a 1440 px y 390 px comparadas con los mockups: {c.text}"]
    return plan


def check_web_hygiene(workspace: Path, draft: TaskDraft) -> HygieneReport:
    report = HygieneReport()
    entries = iter_entries(workspace)
    names = {rel for rel, _, _ in entries}
    for rel, kind, _ in entries:
        if kind == "l":
            report.blockers.append(f"{rel}: enlace simbólico")
        if rel.rsplit("/", 1)[-1] in _BLOCKED:
            report.blockers.append(f"{rel}: configuración de registro/paquetes no permitida")
        if rel.startswith("node_modules/"):
            report.blockers.append("node_modules/ no se acepta: las dependencias se instalan en el BUILD")
            break
    if "package.json" not in names:
        if "index.html" not in names:
            report.blockers.append("falta index.html en la raíz (o un package.json con build)")
        return report
    if "package-lock.json" not in names:
        report.blockers.append("package.json sin package-lock.json: npm ci necesita el lockfile")
        return report
    allowed = {d.lower() for d in draft.allowed_dependencies}
    try:
        pkg = json.loads((workspace / "package.json").read_text(encoding="utf-8"))
        lock = json.loads((workspace / "package-lock.json").read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        report.blockers.append(f"package.json/package-lock.json inválidos: {exc}")
        return report
    for section in ("dependencies", "devDependencies", "optionalDependencies"):
        for name in (pkg.get(section) or {}):
            if name.lower() not in allowed:
                report.blockers.append(f"package.json: dependencia no permitida por el contrato: {name}")
    for path, meta in (lock.get("packages") or {}).items():
        if not path or meta.get("link"):
            continue
        resolved, integrity = meta.get("resolved", ""), meta.get("integrity", "")
        if not resolved.startswith(_REGISTRY) or not integrity:
            report.blockers.append(f"package-lock.json: {path} no viene de {_REGISTRY} con integridad ({resolved or 'sin resolved'})")
    scripts = pkg.get("scripts") or {}
    lifecycle = sorted(k for k in scripts if k in ("preinstall", "install", "postinstall", "prepare"))
    if lifecycle:
        report.warnings.append(f"scripts de instalación ignorados (--ignore-scripts): {', '.join(lifecycle)}")
    return report


def source_signals(workspace: Path) -> list[Signal]:
    signals = []
    for rel, kind, _ in iter_entries(workspace):
        if kind != "f" or Path(rel).suffix.lower() not in _TEXT_SUFFIXES or rel.startswith("node_modules/"):
            continue
        text = (workspace / rel).read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            hit = scan_text_for_injection(re.sub(r"<[^>]+>", " ", line))
            if hit:
                signals.append(Signal("injection", rel, lineno, hit, "texto dirigido al evaluador en el código fuente"))
    return signals


def dom_signals(results: dict) -> tuple[list[Signal], list[dict]]:
    """Señales de inyección (texto visible u oculto) y la lista completa de texto oculto."""
    signals, hidden_all = [], []
    for page, by_vp in results.get("pages", {}).items():
        for vp, info in by_vp.items():
            for h in info.get("hidden_text", []):
                hidden_all.append({"page": page, "viewport": vp, **h})
                hit = scan_text_for_injection(h["text"])
                if hit:
                    signals.append(Signal("injection", f"{page}@{vp}", 0, hit,
                                          "texto OCULTO dirigido al evaluador (" + ", ".join(h["reasons"]) + ")"))
            # innerText incluye texto invisible por color o tamaño: lo que ya se detectó como oculto
            # se reporta solo como oculto, no también como "visible".
            hidden_texts = {" ".join(h["text"].split()) for h in info.get("hidden_text", [])}
            for line in info.get("visible_text", "").splitlines():
                hit = scan_text_for_injection(line)
                if hit and " ".join(line.split()) not in hidden_texts:
                    signals.append(Signal("injection", f"{page}@{vp}", 0, hit, "texto visible dirigido al evaluador"))
    unique = {(s.file, s.snippet, s.detail): s for s in signals}
    return list(unique.values()), hidden_all


def judge_images(draft: TaskDraft, results: dict, evidence: Path) -> list[JudgeImage]:
    images = []
    for m in draft.web.mockups:
        path = mockup_path(m.sha256)
        if path:
            images.append(JudgeImage(m.id, "mockup", m.viewport, m.page, path, m.description))
    for shot in results.get("screenshots", []):
        if shot["label"] == "página completa":
            images.append(JudgeImage(shot["id"], "captura", shot["viewport"], shot["page"], evidence / shot["file"], shot["label"]))
    return images


def deterministic_facts(draft: TaskDraft, results: dict) -> dict:
    checks = results.get("checks", {})
    return {cid: {"pasa": r.get("passed"), "detalle": r.get("detail", "")[:300]} for cid, r in checks.items()}


def decide_web(draft, hygiene, site, results, signals, visual: VisualOutput | None, images) -> Decision:
    reasons: list[str] = []
    flags = {"injection": False, "cheating": False, "tampering": False}
    for s in signals:
        flags[s.kind] = True
    if visual and visual.injection_detected and valid_refs(visual.injection_evidence, images):
        flags["injection"] = True

    def all_status(status, stage):
        return [CriterionDecision(c.id, c.type, c.required, status, stage) for c in draft.criteria]

    if not hygiene.ok:
        return Decision("rejected", False, "hygiene", all_status("not_evaluated", "hygiene"), flags, list(hygiene.blockers))
    if site is not None and not site.ok:
        return Decision("rejected", False, "build", all_status("not_evaluated", "build"), flags, [site.error])
    if results is None:
        return Decision("manual_review", False, "tests", all_status("manual_review", "tests"), flags,
                        ["el runner de Playwright no produjo resultados (falla de infraestructura)"])

    decisions = []
    checks = results.get("checks", {})
    for c in draft.criteria:
        if c.type == "test":
            r = checks.get(c.id)
            if r is None:
                decisions.append(CriterionDecision(c.id, c.type, c.required, "manual_review", "tests",
                                                   reasons=["sin resultado del chequeo"]))
            else:
                decisions.append(CriterionDecision(c.id, c.type, c.required, "pass" if r["passed"] else "fail", "tests",
                                                   reasons=[r.get("detail", "")[:500]],
                                                   tests=[{k: v for k, v in r.items() if k != "requests"}]))
        elif c.type == "visual":
            vc = next((v for v in visual.criteria if v.criterion_id == c.id), None) if visual else None
            d = CriterionDecision(c.id, c.type, c.required, "manual_review", "judge")
            if vc:
                d.judge_verdict, d.judge_reasoning = vc.verdict, vc.reasoning
                ok_refs = valid_refs(vc.evidence, images)
                d.judge_evidence = [{**e.model_dump(), "verified": e in ok_refs} for e in vc.evidence]
                if has_screenshot_ref(vc.evidence, images):
                    d.status = vc.verdict
                else:
                    d.reasons.append("el juez no citó ninguna captura existente: revisión manual")
            else:
                d.reasons.append("el juez visual no emitió veredicto")
            decisions.append(d)
        else:
            decisions.append(CriterionDecision(c.id, c.type, c.required, "manual_review", "static",
                                               reasons=["tipo de criterio no soportado en el perfil web"]))

    required = [d for d in decisions if d.required]
    failed = [d for d in required if d.status == "fail"]
    if failed:
        status, stage = "rejected", min((d.stage for d in failed), key=STAGES.index)
        reasons.append("criterios requeridos no cumplidos: " + ", ".join(d.id for d in failed))
    elif any(d.status == "manual_review" for d in required) or any(flags.values()):
        status = "manual_review"
        stage = "judge" if any(d.status == "manual_review" for d in required) else "final"
    else:
        status, stage = "approved", "final"
    if any(flags.values()):
        reasons.append("señales de " + ", ".join(k for k, v in flags.items() if v) + ": no se aprueba automáticamente")
    return Decision(status, status == "approved", stage, decisions, flags, reasons)


def _file_hashes(directory: Path) -> dict[str, str]:
    return {p.name: "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(directory.iterdir()) if p.is_file()}


def verify_web_submission(task: TaskRecord, workspace: Workspace) -> dict:
    draft = task.draft
    submission_id = uuid.uuid4().hex
    evidence = evidence_dir(task.task_id, submission_id)
    timings: dict[str, float] = {}
    t0 = t = time.monotonic()

    def lap(name: str) -> None:
        nonlocal t
        now = time.monotonic()
        timings[name] = round(now - t, 3)
        t = now

    site = run = visual = None
    images: list[JudgeImage] = []
    judge_error = None
    hidden_all: list[dict] = []
    with track_usage() as usage:
        hygiene = check_web_hygiene(workspace.path, draft)
        signals = source_signals(workspace.path)
        lap("hygiene")
        if hygiene.ok:
            site = prepare_site(workspace.path)
            lap("build")
            try:
                if site.ok:
                    run = run_web_checks(site, draft, evidence)
                    lap("browser")
            finally:
                site.remove()
            if run and run.results:
                dom, hidden_all = dom_signals(run.results)
                signals += dom
                if any(c.type == "visual" for c in draft.criteria):
                    images = judge_images(draft, run.results, evidence)
                    try:
                        visual = judge_visual(draft, images, deterministic_facts(draft, run.results))
                    except LLMError as exc:
                        judge_error = str(exc)[:500]
                    lap("visual_judge")
        decision = decide_web(draft, hygiene, site, run.results if run else None, signals, visual, images)
    timings["total"] = round(time.monotonic() - t0, 3)

    payload = verdict_payload(task, workspace.content_hash, decision)
    payload["evidence"] = _file_hashes(evidence) if evidence.exists() else {}
    results = run.results if run and run.results else {}
    return {
        "submission_id": submission_id,
        "verdict_hash": canonical_hash(payload),
        "verdict": payload,
        "reasons": decision.reasons,
        "criteria": [asdict(d) for d in decision.criteria],
        "hygiene": asdict(hygiene),
        "signals": [{**asdict(s), "ref": s.ref} for s in signals],
        "hidden_text": hidden_all,
        "build": None if site is None else {
            "kind": site.kind, "subdir": site.subdir, "error": site.error, "install_scripts_ignored": site.scripts_ignored,
            "install_s": site.install.duration_s if site.install else None,
            "build_s": site.build.duration_s if site.build else None,
            "build_stdout_tail": site.build.stdout[-800:] if site.build else None,
        },
        "browser": None if run is None else {
            "exit_code": run.sandbox.exit_code, "duration_s": run.sandbox.duration_s,
            "stderr_tail": run.sandbox.stderr[-1500:], "console_errors": results.get("console", [])[:20],
            "runner_errors": results.get("errors", []), "server_state": run.server_state,
        },
        "screenshots": results.get("screenshots", []),
        "videos": results.get("videos", []),
        "visual_judge": {
            "summary": visual.summary if visual else None,
            "injection_detected": visual.injection_detected if visual else None,
            "injection_evidence": [e.model_dump() for e in visual.injection_evidence] if visual else [],
            "images_sent": [im.id for im in images],
            "error": judge_error,
        },
        "timings_s": timings,
        "llm_usage": [asdict(u) for u in usage],
    }


def contract_mockups_missing(draft: TaskDraft) -> list[str]:
    return [m.id for m in (draft.web.mockups if draft.web else []) if mockup_path(m.sha256) is None]


__all__ = ["SUITE_FORMAT_WEB", "contract_mockups_missing", "verify_web_submission", "web_plan", "web_suite_hash"]
