"""Vistas del reporte según el rol. Se construyen por LISTA BLANCA: se copian solo campos seguros.

- Cliente (antes y después del pago): veredicto por criterio, nombres y resultado de los tests,
  capturas, video y hashes. Nunca código de la entrega: ni citas del juez, ni fragmentos en señales,
  ni logs, ni mensajes de fallo (incluyen valores y excepciones producidos por la entrega), ni
  líneas de archivos citadas por la higiene o el análisis estático. Los motivos se regeneran a
  partir de datos estructurados, no se copian.
- Freelancer: todo su reporte salvo el contenido de los tests ocultos (código, valores esperados
  completos, citas del juez a los tests). De cada fallo ve el resumen de una línea.
- Árbitro: reporte completo.
"""

from __future__ import annotations

import copy

CLIENT_EVIDENCE_PREFIXES = ("S",)  # capturas S*.png
CLIENT_EVIDENCE_SUFFIXES = (".png", ".webm")


def _first_line(text: str, limit: int = 300) -> str:
    return (text or "").strip().splitlines()[0][:limit] if (text or "").strip() else ""


def test_summary(test: dict) -> str:
    return test.get("summary") or _first_line(test.get("message", ""))


def _criterion_summary(c: dict, profile: str) -> str:
    status, kind = c["status"], c["type"]
    if status == "not_evaluated":
        return f"Not evaluated (stopped at stage: {c['stage']})."
    if kind == "static":
        if status == "pass":
            return "Static check passed."
        return f"Static check failed at {len(c.get('static_evidence') or [])} location(s)."
    if kind == "visual":
        verdict = c.get("judge_verdict")
        if status == "manual_review":
            return "Visual criterion sent to manual review (no verifiable screenshot reference)."
        return f"Visual judge: {verdict}, based on the screenshots."
    tests = c.get("tests") or []
    if profile == "web":
        return "Checked deterministically in a real browser: " + ("passed." if status == "pass" else "failed.")
    passed = sum(1 for t in tests if t.get("outcome") == "passed")
    text = f"{passed}/{len(tests)} hidden tests passed."
    if status == "manual_review":
        if c.get("judge_verdict") == "test_defect":
            text += " The judge flagged a possible defect in a hidden test: manual review."
        else:
            text += " Manual review required."
    return text


def _client_reasons(report: dict) -> list[str]:
    v = report["verdict"]
    reasons = []
    if v["stage"] == "hygiene":
        reasons.append(f"The delivery failed hygiene checks ({len(report.get('hygiene', {}).get('blockers', []))} issue(s)).")
    elif v["stage"] == "build":
        reasons.append("The delivery's dependencies could not be installed or built.")
    failed = [c["id"] for c in report["criteria"] if c["required"] and c["status"] == "fail"]
    review = [c["id"] for c in report["criteria"] if c["required"] and c["status"] == "manual_review"]
    if failed:
        reasons.append("Required criteria not met: " + ", ".join(failed) + ".")
    if review:
        reasons.append("Criteria in manual review: " + ", ".join(review) + ".")
    flags = [k for k, on in v.get("flags", {}).items() if on]
    if flags:
        reasons.append("Signals detected (" + ", ".join(flags) + "): automatic approval is blocked.")
    return reasons


def client_view(report: dict) -> dict:
    profile = report.get("profile") or ("web" if "screenshots" in report else "script")
    criteria = []
    for c in report["criteria"]:
        item = {
            "id": c["id"], "type": c["type"], "required": c["required"], "status": c["status"], "stage": c["stage"],
            "summary": _criterion_summary(c, profile),
            "tests": [{"name": t.get("name") or t.get("nodeid", "").split("::")[-1], "outcome": t.get("outcome")}
                      for t in (c.get("tests") or []) if profile != "web"],
        }
        if profile == "web" and c["type"] == "test" and c.get("reasons"):
            # Hechos del navegador (texto visible, URL tras el clic, ancho de la página), no código.
            item["detail"] = c["reasons"][0][:300]
        if c["type"] == "visual" and c.get("judge_reasoning"):
            # Describe capturas que el cliente ya ve; el juez visual no recibe código.
            item["visual_reasoning"] = c["judge_reasoning"][:1200]
        criteria.append(item)
    signal_kinds = sorted({(s["kind"], _generic_signal(s["kind"])) for s in report.get("signals", [])})
    return {
        "submission_id": report["submission_id"],
        "received_at": report.get("received_at"),
        "profile": profile,
        "verdict_hash": report["verdict_hash"],
        "verdict": copy.deepcopy(report["verdict"]),
        "reasons": _client_reasons(report),
        "criteria": criteria,
        "signals": [{"kind": k, "detail": d} for k, d in signal_kinds],
        "screenshots": [{k: s[k] for k in ("id", "page", "viewport", "label", "file") if k in s}
                        for s in report.get("screenshots", [])],
        "videos": list(report.get("videos", [])),
        "timings_s": dict(report.get("timings_s", {})),
    }


def _generic_signal(kind: str) -> str:
    return {
        "injection": "Text addressed to the automated evaluator was found in the delivery.",
        "cheating": "The delivery appears to special-case known inputs or read the tests.",
        "tampering": "The delivery appears to tamper with the test infrastructure.",
    }.get(kind, "Signal detected.")


_SIGNAL_EN = [
    ("texto OCULTO dirigido al evaluador", "hidden text aimed at the automated evaluator"),
    ("texto visible dirigido al evaluador", "visible text aimed at the automated evaluator"),
    ("texto dirigido al evaluador en el código fuente", "text aimed at the automated evaluator in the source code"),
    ("texto dirigido al evaluador en la salida de la entrega", "text aimed at the automated evaluator in the delivery's output"),
    ("texto dirigido al evaluador automático", "text aimed at the automated evaluator"),
    ("contiene literalmente la entrada de un ejemplo público", "contains a public example's input verbatim"),
    ("contiene literalmente la salida esperada de un ejemplo público", "contains a public example's expected output verbatim"),
    ("define un hook de pytest", "defines a pytest hook"),
    ("referencia a la infraestructura de test", "references the test infrastructure"),
    ("desde el código entregado", "imported from the delivered code"),
]
_REASON_EN = {"contraste": "contrast", "tamaño 0": "size 0", "fuera de pantalla": "off-screen", "importa ": ""}


def _signal_en(detail: str, kind: str) -> str:
    """Las señales se generan en español; la UI es en inglés. Desconocida → descripción genérica."""
    for es, en in _SIGNAL_EN:
        if es in detail:
            rest = detail.replace(es, "").strip()
            for a, b in _REASON_EN.items():
                rest = rest.replace(a, b)
            return f"{en} {rest}".strip() if es != "desde el código entregado" else f"{rest} {en}".strip()
    return _generic_signal(kind)


def _grouped_signals(signals: list[dict]) -> list[dict]:
    """Una línea por (tipo, detalle), con todas sus ubicaciones: la lista cruda repetía 9 veces lo mismo."""
    groups: dict[tuple[str, str], list[str]] = {}
    for s in signals:
        ref = (s.get("ref") or "").removesuffix(":0")
        if "@" in ref:  # señales del DOM: "/@desktop" → "/ (desktop)"
            page, viewport = ref.split("@", 1)
            ref = f"{page} ({viewport})"
        key = (s["kind"], _signal_en(s.get("detail", ""), s["kind"]))
        if ref not in groups.setdefault(key, []):
            groups[key].append(ref)
    return [{"kind": k, "detail": d, "ref": ", ".join(r for r in refs if r)} for (k, d), refs in groups.items()]


def freelancer_view(report: dict) -> dict:
    view = copy.deepcopy(report)
    view["signals"] = _grouped_signals(report.get("signals", []))
    # Motivos en inglés, regenerados igual que para el cliente; los internos quedan aparte.
    view["reasons_detail"] = view.get("reasons", [])
    view["reasons"] = _client_reasons(report)
    for c in view.get("criteria", []):
        if view.get("profile") != "web":
            c["tests"] = [{"nodeid": t.get("nodeid"), "name": t.get("name"), "outcome": t.get("outcome"),
                           "summary": test_summary(t)} for t in (c.get("tests") or [])]
        evidence = []
        for e in c.get("judge_evidence") or []:
            if e.get("kind") == "test_code":
                continue  # citaría el código de un test oculto
            if e.get("kind") == "test_output":
                e = {**e, "quote": "(hidden test output)"}
            evidence.append(e)
        c["judge_evidence"] = evidence
    judge = view.get("judge") or {}
    for key in ("injection_evidence", "cheating_evidence"):
        judge[key] = [e for e in judge.get(key, []) if e.get("kind") != "test_code"]
    # La salida de pytest del contenedor confiable trae tracebacks con el código de los tests
    # ocultos (lo encontró el test del canario): solo se conservan los metadatos.
    if view.get("test_run"):
        view["test_run"] = {k: v for k, v in view["test_run"].items() if k not in ("stdout_tail", "stderr_tail")}
    return view


def evidence_allowed(role: str, name: str) -> bool:
    if role in ("arbiter", "freelancer"):
        return True
    if role == "client":
        return name.startswith(CLIENT_EVIDENCE_PREFIXES) and name.endswith(".png") or name.endswith(".webm")
    return False


def view_for(role: str, report: dict) -> dict:
    if role == "arbiter":
        return report
    if role == "freelancer":
        return freelancer_view(report)
    if role == "client":
        return client_view(report)
    raise PermissionError(role)
