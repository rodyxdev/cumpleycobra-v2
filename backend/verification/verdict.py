"""Capa 5 — regla final determinista, reporte y `verdict_hash`; y el orquestador de las capas.

Regla por criterio:
- static: lo decide el chequeo determinista. El juez no opina.
- visual: revisión manual (no soportado en script/api).
- test, con algún test que no pasó:
    juez = test_defect con evidencia válida (línea del test + texto del contrato) → manual_review;
    cualquier otra cosa (incluido "pass") → fail. El LLM nunca aprueba sobre un fallo real.
- test, con todos sus tests pasando:
    juez = pass → pass; juez = fail con evidencia válida → fail;
    juez sin evidencia, test_defect, ausente o caído → manual_review.
Global: un criterio requerido en fail → rejected. Si no, cualquier requerido en manual_review o
una señal de inyección/trampa/manipulación → manual_review. Si no → approved.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Literal

from backend.canonical import canonical_hash
from backend.contract.models import Criterion, TaskDraft
from backend.ingest import Workspace
from backend.llm import LLMError, track_usage
from backend.sandbox import DepsVolume, SandboxResult, build_dependencies, ensure_runner_image
from backend.tasks import TaskRecord, hidden_dir
from backend.verification.hygiene import HygieneReport, check_hygiene
from backend.verification.judge import EvidenceChecker, JudgeOutput, judge_submission, submission_files
from backend.verification.junit import TestOutcome
from backend.verification.static import Signal, StaticResult, collect_signals, load_sources, run_static_check
from backend.verification.suite import outcomes_by_criterion, run_suite

VERDICT_SCHEMA = "cumpleycobra/verdict/v1"
Status = Literal["pass", "fail", "manual_review", "not_evaluated"]
STAGES = ["hygiene", "build", "static", "tests", "judge", "final"]


@dataclass
class CriterionDecision:
    id: str
    type: str
    required: bool
    status: Status
    stage: str
    reasons: list[str] = field(default_factory=list)
    tests: list[dict] = field(default_factory=list)
    judge_verdict: str | None = None
    judge_reasoning: str | None = None
    judge_evidence: list[dict] = field(default_factory=list)
    static_evidence: list[str] = field(default_factory=list)


@dataclass
class Decision:
    status: Literal["approved", "rejected", "manual_review"]
    approved: bool
    stage: str
    criteria: list[CriterionDecision]
    flags: dict[str, bool]
    reasons: list[str]


def _decide_test_criterion(
    c: Criterion, tests: list[TestOutcome], judge: JudgeOutput | None, checker: EvidenceChecker | None
) -> CriterionDecision:
    d = CriterionDecision(c.id, c.type, c.required, "manual_review", "tests",
                          tests=[{"nodeid": t.nodeid, "outcome": t.outcome, "message": t.message[:600]} for t in tests])
    if not tests:
        d.reasons.append("no hay resultados de tests para este criterio")
        return d
    jc = next((j for j in judge.criteria if j.criterion_id == c.id), None) if judge else None
    valid = checker.valid(jc.evidence) if (jc and checker) else []
    if jc:
        d.judge_verdict, d.judge_reasoning = jc.verdict, jc.reasoning
        d.judge_evidence = [{**e.model_dump(), "verified": e in valid} for e in jc.evidence]

    failing = [t for t in tests if t.outcome != "passed"]
    if failing:
        kinds = {e.kind for e in valid}
        if jc and jc.verdict == "test_defect" and {"test_code", "contract"} <= kinds:
            d.status, d.stage = "manual_review", "judge"
            d.reasons.append("el juez señala un test defectuoso con evidencia verificada: revisión manual")
        else:
            d.status, d.stage = "fail", "tests"
            d.reasons.append(f"{len(failing)} de {len(tests)} tests no pasaron")
            if jc and jc.verdict == "pass":
                d.reasons.append("el juez dijo pass pero hay tests que fallan: se ignora al juez")
            if jc and jc.verdict == "test_defect":
                d.reasons.append("el juez alegó test_defect sin evidencia verificable: se mantiene el fallo")
        return d

    d.stage = "judge"
    if jc is None:
        d.reasons.append("tests OK pero el juez no emitió veredicto para este criterio")
    elif jc.verdict == "pass":
        d.status = "pass"
    elif jc.verdict == "fail" and valid:
        d.status = "fail"
        d.reasons.append("los tests pasan, pero el juez detectó un problema con evidencia verificada")
    else:
        d.reasons.append(f"tests OK; el juez dijo {jc.verdict} sin evidencia verificable: revisión manual")
    return d


def decide(
    draft: TaskDraft,
    hygiene: HygieneReport,
    build: SandboxResult | None,
    static_results: list[StaticResult],
    signals: list[Signal],
    outcomes: list[TestOutcome] | None,
    test_run_error: str | None,
    judge: JudgeOutput | None,
    checker: EvidenceChecker | None,
) -> Decision:
    reasons: list[str] = []
    flags = {"injection": False, "cheating": False, "tampering": False}
    for s in signals:
        flags[s.kind] = True
    if judge and checker:
        flags["injection"] |= judge.injection_detected and bool(checker.valid(judge.injection_evidence))
        flags["cheating"] |= judge.cheating_detected and bool(checker.valid(judge.cheating_evidence))

    def all_not_evaluated(stage: str) -> list[CriterionDecision]:
        return [CriterionDecision(c.id, c.type, c.required, "not_evaluated", stage) for c in draft.criteria]

    if not hygiene.ok:
        reasons += hygiene.blockers
        return Decision("rejected", False, "hygiene", all_not_evaluated("hygiene"), flags, reasons)
    if build is not None and (build.exit_code != 0 or build.timed_out):
        reasons.append("no se pudieron instalar las dependencias declaradas (solo se aceptan wheels)")
        return Decision("rejected", False, "build", all_not_evaluated("build"), flags, reasons)

    static_by_id = {r.criterion_id: r for r in static_results}
    grouped = outcomes_by_criterion(outcomes or [])
    decisions = []
    for c in draft.criteria:
        if c.type == "static":
            r = static_by_id[c.id]
            decisions.append(CriterionDecision(c.id, c.type, c.required, "pass" if r.passed else "fail", "static",
                                               static_evidence=r.evidence))
        elif c.type == "visual":
            decisions.append(CriterionDecision(c.id, c.type, c.required, "manual_review", "judge",
                                               reasons=["criterio visual: no soportado en esta fase"]))
        elif test_run_error:
            decisions.append(CriterionDecision(c.id, c.type, c.required, "fail", "tests", reasons=[test_run_error]))
        else:
            decisions.append(_decide_test_criterion(c, grouped.get(c.id, []), judge, checker))

    required = [d for d in decisions if d.required]
    failed = [d for d in required if d.status == "fail"]
    if failed:
        stage = min((d.stage for d in failed), key=STAGES.index)
        reasons.append("criterios requeridos no cumplidos: " + ", ".join(d.id for d in failed))
        status = "rejected"
    elif any(d.status == "manual_review" for d in required) or any(flags.values()):
        stage = "judge" if any(d.status == "manual_review" for d in required) else "final"
        if any(flags.values()):
            reasons.append("señales de " + ", ".join(k for k, v in flags.items() if v) + ": no se aprueba automáticamente")
        status = "manual_review"
    else:
        stage, status = "final", "approved"
    if any(flags.values()) and status == "rejected":
        reasons.append("además se detectaron señales de " + ", ".join(k for k, v in flags.items() if v))
    return Decision(status, status == "approved", stage, decisions, flags, reasons)


def verdict_payload(task: TaskRecord, workspace_hash: str, decision: Decision) -> dict:
    return {
        "schema": VERDICT_SCHEMA,
        "task_id": task.task_id,
        "rules_hash": task.rules_hash,
        "suite_hash": task.suite_hash,
        "workspace_hash": workspace_hash,
        "approved": decision.approved,
        "status": decision.status,
        "stage": decision.stage,
        "criteria": [{"id": d.id, "status": d.status, "stage": d.stage} for d in decision.criteria],
        "flags": decision.flags,
    }


def verify_submission(task: TaskRecord, workspace: Workspace) -> dict:
    """Ejecuta las 5 capas sobre una entrega ya ingerida y devuelve el reporte completo."""
    draft = task.draft
    timings: dict[str, float] = {}
    t0 = time.monotonic()

    def lap(name: str, since: float) -> float:
        now = time.monotonic()
        timings[name] = round(now - since, 3)
        return now

    with track_usage() as usage:
        hygiene = check_hygiene(workspace.path, draft)
        t = lap("hygiene", t0)
        sources = load_sources(workspace.path)
        static_results = [run_static_check(c, sources, draft, workspace.path) for c in draft.criteria if c.type == "static"]
        signals = collect_signals(sources, draft)
        t = lap("static", t)

        build = outcomes = test_run = test_run_error = judge = checker = None
        judge_error = None
        if hygiene.ok:
            image = ensure_runner_image()
            volume = DepsVolume(image) if hygiene.requirements else None
            try:
                if volume:
                    build = build_dependencies(hygiene.requirements, volume, image)
                    t = lap("build", t)
                if build is None or build.exit_code == 0:
                    test_run = run_suite(workspace.path, hidden_dir(task.task_id),
                                         deps_mount=volume.mount() if volume else None)
                    outcomes = test_run.outcomes
                    if test_run.sandbox.timed_out:
                        test_run_error = "la suite superó el tiempo máximo (¿bucle infinito al importar?)"
                    elif not test_run.report_found:
                        test_run_error = "la suite no produjo reporte: " + test_run.sandbox.stderr[-300:]
                    t = lap("tests", t)
            finally:
                if volume:
                    volume.remove()

            if test_run is not None and any(c.type == "test" for c in draft.criteria):
                hidden = {p.name: p.read_text(encoding="utf-8") for p in sorted(hidden_dir(task.task_id).glob("test_*.py"))}
                files = submission_files(workspace.path)
                checker = EvidenceChecker(draft, files, hidden, outcomes or [])
                try:
                    judge = judge_submission(draft, outcomes_by_criterion(outcomes or []), hidden,
                                             static_results, signals, files)
                except LLMError as exc:
                    judge_error = str(exc)[:500]
                t = lap("judge", t)

        decision = decide(draft, hygiene, build, static_results, signals, outcomes, test_run_error, judge, checker)
    timings["total"] = round(time.monotonic() - t0, 3)

    payload = verdict_payload(task, workspace.content_hash, decision)
    return {
        "submission_id": uuid.uuid4().hex,
        "verdict_hash": canonical_hash(payload),
        "verdict": payload,
        "reasons": decision.reasons,
        "criteria": [asdict(d) for d in decision.criteria],
        "hygiene": asdict(hygiene),
        "signals": [{**asdict(s), "ref": s.ref} for s in signals],
        "judge": {
            "summary": judge.summary if judge else None,
            "injection_detected": judge.injection_detected if judge else None,
            "injection_evidence": [{**e.model_dump(), "verified": checker.is_valid(e)} for e in judge.injection_evidence] if judge else [],
            "cheating_detected": judge.cheating_detected if judge else None,
            "cheating_evidence": [{**e.model_dump(), "verified": checker.is_valid(e)} for e in judge.cheating_evidence] if judge else [],
            "error": judge_error,
        },
        "build": _sandbox_summary(build),
        "test_run": _sandbox_summary(test_run.sandbox) if test_run else None,
        "timings_s": timings,
        "llm_usage": [asdict(u) for u in usage],
    }


def _sandbox_summary(r: SandboxResult | None) -> dict | None:
    if r is None:
        return None
    return {"exit_code": r.exit_code, "duration_s": r.duration_s, "timed_out": r.timed_out,
            "oom_killed": r.oom_killed, "network": r.network,
            "stdout_tail": r.stdout[-1500:], "stderr_tail": r.stderr[-1500:]}
