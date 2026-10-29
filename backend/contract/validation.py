"""Chequeos deterministas del borrador y `rules_hash`."""

from __future__ import annotations

import ast
import re

from backend.canonical import canonical_hash
from backend.contract.models import CRITERION_ID_RE, MOCKUP_ID_RE, SHA256_RE, Criterion, ReviewIssue, TaskDraft

RULES_SCHEMA = "cumpleycobra/rules/v1"
_PYPI_NAME_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$")
_MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")


def normalize_dist_name(name: str) -> str:
    """Nombre de distribución normalizado (PEP 503)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_call_example(text: str) -> tuple[str, list, dict]:
    """`f("a", n=2)` → ("f", ["a"], {"n": 2}). Solo literales; si no, ValueError."""
    node = ast.parse(text.strip(), mode="eval").body
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
        raise ValueError("debe ser una llamada simple: nombre(args)")
    args = [ast.literal_eval(a) for a in node.args]
    kwargs = {k.arg: ast.literal_eval(k.value) for k in node.keywords if k.arg}
    return node.func.id, args, kwargs


def parse_expected(text: str) -> tuple[str, object]:
    """'raises ValueError' → ("raises", "ValueError"); si no, ("value", literal)."""
    text = text.strip()
    match = re.fullmatch(r"raises\s+([A-Za-z_][A-Za-z0-9_.]*)", text)
    if match:
        return "raises", match.group(1)
    return "value", ast.literal_eval(text)


def _issue(cid: str, kind: str, severity: str, explanation: str, suggestion: str = "") -> ReviewIssue:
    return ReviewIssue(criterion_id=cid, kind=kind, severity=severity, explanation=explanation, suggestion=suggestion)


def _criterion_issues(c: Criterion, draft: TaskDraft) -> list[ReviewIssue]:
    issues = []
    if not CRITERION_ID_RE.match(c.id):
        issues.append(_issue(c.id, "other", "blocker", "El id debe tener la forma C1, C2, …"))
    if c.type == "static" and c.static_check is None:
        issues.append(_issue(c.id, "missing_check", "blocker",
                             "Un criterio estático necesita un chequeo del catálogo (static_check).",
                             "Asignar un static_check o convertirlo en criterio de tipo test."))
    if c.type != "static" and c.static_check is not None:
        issues.append(_issue(c.id, "wrong_type", "blocker", "static_check solo aplica a criterios de tipo static."))
    if c.static_check and c.static_check.kind == "max_function_lines":
        if len(c.static_check.args) != 1 or not c.static_check.args[0].isdigit():
            issues.append(_issue(c.id, "other", "blocker", "max_function_lines requiere un único argumento numérico."))
    if c.web_check is not None and (draft.profile != "web" or c.type != "test"):
        issues.append(_issue(c.id, "wrong_type", "blocker", "web_check solo aplica a criterios test del perfil web."))
    if draft.profile == "web" and c.type == "test" and c.web_check is None:
        issues.append(_issue(c.id, "missing_check", "blocker",
                             "En el perfil web, cada criterio test necesita un web_check del catálogo.",
                             "Asignar text_present, link_navigates, form_submits, element_visible, ..."))
    if draft.profile == "web" and c.type == "static":
        issues.append(_issue(c.id, "wrong_type", "blocker",
                             "Los chequeos estáticos son para código Python; no aplican al perfil web."))
    if c.web_check is not None:
        issues.extend(_web_check_issues(c, draft))
    if c.type == "visual" and draft.profile != "web":
        issues.append(_issue(c.id, "wrong_type", "warning",
                             "Un criterio visual no se puede verificar en un perfil script/api; irá a revisión manual."))
    return issues


_WEB_ARGS = {"text_present": 1, "title_contains": 1, "element_visible": 2, "link_navigates": 2,
             "no_horizontal_overflow": 0}


def _web_check_issues(c: Criterion, draft: TaskDraft) -> list[ReviewIssue]:
    wc = c.web_check
    issues = []
    if not wc.page.startswith("/"):
        issues.append(_issue(c.id, "other", "blocker", f"La página {wc.page!r} debe ser una ruta que empiece con '/'."))
    if draft.web and wc.page not in draft.web.pages:
        issues.append(_issue(c.id, "other", "blocker", f"La página {wc.page} no está en web.pages."))
    if wc.kind in _WEB_ARGS and len(wc.args) != _WEB_ARGS[wc.kind]:
        issues.append(_issue(c.id, "other", "blocker", f"{wc.kind} requiere {_WEB_ARGS[wc.kind]} argumento(s)."))
    if wc.kind == "form_submits":
        fields = [a for a in wc.args[2:] if "=" in a and not a.startswith("boton=")]
        has_button = any(a.startswith("boton=") for a in wc.args)
        if len(wc.args) < 3 or not wc.args[0].startswith("/") or not fields or not has_button:
            issues.append(_issue(c.id, "other", "blocker",
                                 "form_submits requiere [endpoint, confirmación, 'Etiqueta=valor'..., 'boton=Nombre']."))
    return issues


def deterministic_issues(draft: TaskDraft) -> list[ReviewIssue]:
    issues: list[ReviewIssue] = []
    ids = [c.id for c in draft.criteria]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        issues.append(_issue(dup, "duplicate", "blocker", f"El id {dup} está repetido."))
    for c in draft.criteria:
        issues.extend(_criterion_issues(c, draft))

    if draft.profile in ("script", "api") and not any(c.type == "test" for c in draft.criteria):
        issues.append(_issue("*", "unverifiable", "blocker", "No hay ningún criterio de tipo test."))
    if draft.profile == "web":
        if draft.web is None or not draft.web.pages:
            issues.append(_issue("*", "other", "blocker", "El perfil web requiere web.pages con al menos una página."))
        else:
            for m in draft.web.mockups:
                if not MOCKUP_ID_RE.match(m.id) or not SHA256_RE.match(m.sha256):
                    issues.append(_issue("*", "other", "blocker", f"Mockup inválido: {m.id}"))
        return issues
    if draft.web is not None:
        issues.append(_issue("*", "other", "blocker", "web solo aplica al perfil web."))
    if not draft.interface.symbols:
        issues.append(_issue("*", "unverifiable", "blocker", "La interfaz no declara ningún símbolo."))
    for sym in draft.interface.symbols:
        if not _MODULE_RE.match(sym.module) or not sym.name.isidentifier():
            issues.append(_issue("*", "other", "blocker", f"Símbolo inválido: {sym.module}.{sym.name}"))
    if draft.profile == "api" and not any(s.kind == "asgi_app" for s in draft.interface.symbols):
        issues.append(_issue("*", "other", "blocker", "El perfil api requiere un símbolo de tipo asgi_app."))
    for dep in draft.allowed_dependencies:
        if not _PYPI_NAME_RE.match(dep):
            issues.append(_issue("*", "other", "blocker", f"Dependencia con nombre inválido: {dep!r}"))

    if draft.profile == "script":
        functions = {s.name for s in draft.interface.symbols}
        for i, ex in enumerate(draft.examples, 1):
            try:
                name, _, _ = parse_call_example(ex.input)
                parse_expected(ex.expected_output)
            except (ValueError, SyntaxError) as exc:
                issues.append(_issue("*", "ambiguous", "blocker", f"Ejemplo {i} no es una llamada/literal válidos: {exc}"))
                continue
            if name not in functions:
                issues.append(_issue("*", "ambiguous", "blocker", f"Ejemplo {i} llama a {name}, que no está en la interfaz."))
    return issues


def blocking_errors(draft: TaskDraft) -> list[str]:
    return [f"{i.criterion_id}: {i.explanation}" for i in deterministic_issues(draft) if i.severity == "blocker"]


def rules_payload(draft: TaskDraft) -> dict:
    contract = draft.model_dump(mode="json")
    # Campos de la fase 3: si están vacíos no entran al hash, así los contratos anteriores
    # conservan su rules_hash.
    if contract.get("web") is None:
        contract.pop("web", None)
    for c in contract["criteria"]:
        if c.get("web_check") is None:
            c.pop("web_check", None)
    return {"schema": RULES_SCHEMA, "contract": contract}


def rules_hash(draft: TaskDraft) -> str:
    return canonical_hash(rules_payload(draft))
