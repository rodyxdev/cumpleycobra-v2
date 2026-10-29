import unicodedata

import pytest

from backend.contract import drafting
from backend.contract.models import Criterion, DraftReview, InterfaceSymbol, ReviewIssue, StaticCheck
from backend.contract.validation import (
    blocking_errors,
    deterministic_issues,
    parse_call_example,
    parse_expected,
    rules_hash,
)

from .helpers import ventas_draft


def kinds(draft):
    return {(i.criterion_id, i.kind, i.severity) for i in deterministic_issues(draft)}


def test_fixture_contract_has_no_blockers():
    assert blocking_errors(ventas_draft()) == []


def test_rules_hash_is_stable_and_sensitive():
    a, b = ventas_draft(), ventas_draft()
    assert rules_hash(a) == rules_hash(b)
    changed = a.model_copy(update={"title": a.title + "!"})
    assert rules_hash(changed) != rules_hash(a)


def test_rules_hash_ignores_unicode_normalization_form():
    a = ventas_draft()
    nfd = a.model_copy(update={"description": unicodedata.normalize("NFD", a.description)})
    assert nfd.description != a.description or a.description.isascii()
    assert rules_hash(nfd) == rules_hash(a)


def test_duplicate_ids_and_bad_id_format():
    d = ventas_draft()
    crit = [*d.criteria, d.criteria[0], d.criteria[1].model_copy(update={"id": "X9"})]
    issues = kinds(d.model_copy(update={"criteria": crit}))
    assert ("C1", "duplicate", "blocker") in issues
    assert ("X9", "other", "blocker") in issues


def test_static_criterion_requires_check_and_others_must_not_have_one():
    d = ventas_draft()
    crit = [
        Criterion(id="C1", text="sin eval", type="static", required=True, static_check=None),
        Criterion(id="C2", text="suma", type="test", required=True, static_check=StaticCheck(kind="forbid_calls", args=["eval"])),
    ]
    issues = kinds(d.model_copy(update={"criteria": crit}))
    assert ("C1", "missing_check", "blocker") in issues
    assert ("C2", "wrong_type", "blocker") in issues


def test_needs_at_least_one_test_criterion():
    d = ventas_draft()
    only_static = [c for c in d.criteria if c.type == "static"]
    assert ("*", "unverifiable", "blocker") in kinds(d.model_copy(update={"criteria": only_static}))


def test_visual_criterion_outside_web_is_a_warning():
    d = ventas_draft()
    crit = [*d.criteria, Criterion(id="C99", text="se ve bien", type="visual", required=False, static_check=None)]
    assert ("C99", "wrong_type", "warning") in kinds(d.model_copy(update={"criteria": crit}))


def test_api_profile_requires_asgi_app():
    d = ventas_draft(profile="api")
    assert any("asgi_app" in e for e in blocking_errors(d))
    sym = InterfaceSymbol(module="app", name="app", kind="asgi_app", signature="app", description="ASGI")
    ok = d.model_copy(update={"interface": d.interface.model_copy(update={"symbols": [sym]}), "examples": []})
    assert not any("asgi_app" in e for e in blocking_errors(ok))


def test_examples_must_be_literal_calls_to_interface():
    d = ventas_draft()
    bad = [
        d.examples[0].model_copy(update={"input": "resumir_ventas(open('/etc/passwd').read())"}),
        d.examples[0].model_copy(update={"input": "otra_funcion('x')"}),
        d.examples[0].model_copy(update={"expected_output": "{'a': x}"}),
    ]
    errors = blocking_errors(d.model_copy(update={"examples": bad}))
    assert len(errors) == 3


def test_parse_call_and_expected():
    assert parse_call_example('f("a\\nb", n=2)') == ("f", ["a\nb"], {"n": 2})
    assert parse_expected("raises ValueError") == ("raises", "ValueError")
    assert parse_expected("{'a': 1}") == ("value", {"a": 1})
    with pytest.raises(ValueError):
        parse_call_example("__import__('os').system('x')")


def test_review_merges_deterministic_and_model_issues(monkeypatch):
    d = ventas_draft()
    crit = [*d.criteria, Criterion(id="C1", text="dup", type="test", required=True, static_check=None)]
    d = d.model_copy(update={"criteria": crit})
    llm_issue = ReviewIssue(criterion_id="C2", kind="vague", severity="warning", explanation="x", suggestion="y")
    seen = {}

    def fake(messages, model, **kw):
        seen["user"] = messages[1]["content"]
        return DraftReview(ok=True, issues=[llm_issue], summary="ok")

    monkeypatch.setattr(drafting, "structured_completion", fake)
    review = drafting.review_draft(d)
    assert review.ok is False  # el duplicado determinista es bloqueante aunque el modelo diga ok
    assert llm_issue in review.issues
    assert any(i.kind == "duplicate" for i in review.issues)
    assert "issues_deterministas" in seen["user"]


def test_draft_prompt_wraps_request(monkeypatch):
    captured = {}

    def fake(messages, model, **kw):
        captured["messages"] = messages
        return ventas_draft()

    monkeypatch.setattr(drafting, "structured_completion", fake)
    drafting.draft_task("Necesito una función que sume")
    assert "Necesito una función que sume" in captured["messages"][1]["content"]
    assert "imports_allowed" in captured["messages"][0]["content"]
