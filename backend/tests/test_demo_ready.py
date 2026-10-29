"""Fase 4.5: idioma de los jueces, señales legibles para el freelancer y scripts/demo_reset."""

import importlib.util
import json
import sys
import zipfile
from pathlib import Path

from backend.contract.validation import contract_language
from backend.ingest import hash_workspace
from backend.views import client_view, freelancer_view

from .helpers import FIXTURES, ventas_draft

REPO = Path(__file__).resolve().parents[2]


def test_contract_language_ignores_quoted_ui_text():
    spanish = ventas_draft()
    assert contract_language(spanish) == "es"
    english = spanish.model_copy(update={
        "title": "Huerta Viva landing page",
        "description": "One-page landing with a contact form that shows 'Verduras orgánicas a tu puerta'.",
        "criteria": [c.model_copy(update={"text": "The page shows the title 'Verduras orgánicas a tu puerta' and the form"})
                     for c in spanish.criteria],
    })
    assert contract_language(english) == "en"


def _report(signals):
    return {
        "submission_id": "a" * 32, "profile": "web", "verdict_hash": "sha256:" + "d" * 64,
        "verdict": {"status": "manual_review", "approved": False, "stage": "final", "workspace_hash": "w",
                    "flags": {"injection": True, "cheating": False, "tampering": False}},
        "reasons": ["señales de injection: no se aprueba automáticamente"],
        "criteria": [], "signals": signals,
    }


def test_freelancer_signals_are_english_and_grouped():
    raw = [
        {"kind": "injection", "file": "index.html", "line": n, "ref": f"index.html:{n}", "snippet": "x",
         "detail": "texto dirigido al evaluador en el código fuente"} for n in (53, 54)
    ] + [
        {"kind": "injection", "file": f"/@{vp}", "line": 0, "ref": f"/@{vp}:0", "snippet": "x",
         "detail": "texto OCULTO dirigido al evaluador (contraste 1.00:1)"} for vp in ("desktop", "mobile")
    ] + [{"kind": "tampering", "file": "m.py", "line": 1, "ref": "m.py:1", "snippet": "x",
          "detail": "inspecciona frames de la pila"}]
    view = freelancer_view(_report(raw))
    assert view["signals"] == [
        {"kind": "injection", "detail": "text aimed at the automated evaluator in the source code",
         "ref": "index.html:53, index.html:54"},
        {"kind": "injection", "detail": "hidden text aimed at the automated evaluator (contrast 1.00:1)",
         "ref": "/ (desktop), / (mobile)"},
        {"kind": "tampering", "detail": "The delivery appears to tamper with the test infrastructure.", "ref": "m.py:1"},
    ]
    assert view["reasons"] == ["Signals detected (injection): automatic approval is blocked."]
    assert view["reasons_detail"] == ["señales de injection: no se aprueba automáticamente"]
    # El cliente sigue viendo solo la descripción genérica, sin ubicaciones.
    assert client_view(_report(raw))["signals"][0] == {
        "kind": "injection", "detail": "Text addressed to the automated evaluator was found in the delivery."}


def _load_demo_reset():
    spec = importlib.util.spec_from_file_location("demo_reset", REPO / "scripts" / "demo_reset.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["demo_reset"] = module
    spec.loader.exec_module(module)
    return module


def test_demo_reset_fixtures_reproduce_the_fixture_hashes(tmp_path, monkeypatch):
    demo_reset = _load_demo_reset()
    monkeypatch.setattr(demo_reset, "DEMO", tmp_path / "demo")
    demo_reset.prepare_fixtures()
    demo_reset.prepare_fixtures()  # dos veces seguidas: idempotente
    assert demo_reset.results[-1][1] and demo_reset.results[-2][1]
    deliveries = tmp_path / "demo" / "deliveries"
    assert sorted(p.name for p in deliveries.iterdir()) == sorted(demo_reset.DELIVERIES)
    for name, source in demo_reset.DELIVERIES.items():
        extracted = tmp_path / "x" / name
        zipfile.ZipFile(deliveries / name).extractall(extracted)
        assert hash_workspace(extracted) == hash_workspace(source), name
    assert (tmp_path / "demo" / "mockups" / "M1-desktop.png").read_bytes() == (
        FIXTURES.parent / "landing" / "mockup_desktop.png").read_bytes()
    assert "GUION" in (tmp_path / "demo" / "GUION.txt").read_text(encoding="utf-8")


def test_demo_reset_clears_local_data_and_is_idempotent(tmp_path):
    from backend.config import get_settings

    demo_reset = _load_demo_reset()
    work = get_settings().work_dir
    (work / "tasks" / "t1").mkdir(parents=True)
    (work / "users.json").write_text(json.dumps({}), encoding="utf-8")
    demo_reset.reset_data(False)
    assert not (work / "tasks").exists() and not (work / "users.json").exists()
    demo_reset.reset_data(False)
    assert demo_reset.results[-1] == ("Datos locales borrados (.data)", True, "ya estaba limpio")
