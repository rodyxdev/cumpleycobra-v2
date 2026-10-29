"""Borrador y revisión del contrato con Muse Spark."""

from __future__ import annotations

import json

from backend.contract.models import DraftReview, ReviewIssue, TaskDraft
from backend.contract.validation import deterministic_issues
from backend.llm import structured_completion

STATIC_CATALOG = """\
Catálogo de chequeos estáticos (static_check.kind y significado de args):
- forbid_imports: args = módulos que no se pueden importar.
- forbid_calls: args = llamadas prohibidas por nombre ("eval", "exec", "os.system").
- imports_allowed: args se ignoran; solo permite importar biblioteca estándar, allowed_dependencies
  y módulos propios de la entrega. Es el chequeo correcto para "solo biblioteca estándar".
- require_type_hints / require_docstrings: args = símbolos; [] = todos los de la interfaz.
- max_function_lines: args = ["<n>"].
- ruff_clean: args = códigos o prefijos de reglas de ruff ("F", "E9").
"""

DRAFT_SYSTEM = """\
Eres un líder técnico que convierte un pedido de software en un contrato de aceptación
verificable por máquina. Responde en el idioma del pedido.

Reglas del contrato:
- profile: "script" (módulo/funciones Python), "api" (app HTTP ASGI) o "web" (interfaz visual).
- interface.symbols: los símbolos que la entrega DEBE exponer. Respeta exactamente los nombres de
  módulo, función y firma que dé el cliente; si no los da, elige nombres cortos en snake_case.
  Para "api", incluye un símbolo kind="asgi_app" (p. ej. module "app", name "app").
- criteria: atómicos (una sola cosa por criterio), verificables y sin adjetivos vagos
  ("rápido", "limpio", "robusto"). Ids C1, C2, ... Tipos:
  * "test": comportamiento observable que se comprueba ejecutando código.
  * "static": propiedad del código fuente verificable con un chequeo del catálogo de abajo.
    Si una propiedad estática no encaja en el catálogo, conviértela en test o descártala.
  * "visual": solo para profile "web".
  static_check debe ser null salvo en criterios "static".
  required=true para lo que el cliente exige; false para lo deseable.
- allowed_dependencies: nombres de PyPI. Lista vacía si basta la biblioteca estándar.
- examples: 2 a 4 ejemplos públicos (el freelancer los verá). Para "script":
  input = UNA llamada con argumentos literales de Python, p. ej. resumir("a,1"),
  expected_output = literal Python del resultado exacto, o "raises NombreDeExcepcion".
  Para "api": input = "METHOD /ruta {json}", expected_output = "<status> {json}".
  Si el cliente da ejemplos, inclúyelos literalmente.
- No inventes requisitos que el cliente no pidió salvo validaciones de entrada obvias, y márcalas required=false.

""" + STATIC_CATALOG

REVIEW_SYSTEM = """\
Eres un auditor de contratos de aceptación de software. Revisas un borrador ANTES de que se
congele y señalas criterios que no se puedan verificar de forma objetiva.

Para cada problema emite un issue con:
- kind: vague (palabras sin umbral: "rápido", "bien", "intuitivo"), unverifiable (no se puede
  comprobar ejecutando código ni leyendo el fuente), ambiguous (admite dos lecturas que llevarían
  a tests distintos), wrong_type (el tipo test/static/visual no corresponde), missing_check,
  duplicate, other.
- severity: blocker si impide escribir un test o chequeo objetivo; warning si solo conviene mejorarlo.
- suggestion: reescritura concreta del criterio (con umbrales, formatos y casos borde explícitos).
Revisa también que los ejemplos sean coherentes con los criterios y con la interfaz.
No repitas problemas que ya figuran en "issues_deterministas". Si todo está bien, devuelve issues vacío.
ok = true solo si no hay bloqueantes.

""" + STATIC_CATALOG


def draft_task(request: str) -> TaskDraft:
    return structured_completion(
        [
            {"role": "system", "content": DRAFT_SYSTEM},
            {"role": "user", "content": f"Pedido del cliente:\n<<<\n{request}\n>>>"},
        ],
        TaskDraft,
        schema_name="task_draft",
    )


def review_draft(draft: TaskDraft) -> DraftReview:
    """Combina chequeos deterministas con la revisión del modelo."""
    det = deterministic_issues(draft)
    payload = {
        "borrador": draft.model_dump(mode="json"),
        "issues_deterministas": [i.model_dump() for i in det],
    }
    llm_review = structured_completion(
        [
            {"role": "system", "content": REVIEW_SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=1)},
        ],
        DraftReview,
        schema_name="draft_review",
    )
    issues: list[ReviewIssue] = [*det, *llm_review.issues]
    ok = not any(i.severity == "blocker" for i in issues)
    return DraftReview(ok=ok, issues=issues, summary=llm_review.summary)
