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

WEB_CATALOG = """\
Perfil "web" (landing, sitio estático o proyecto Node con build):
- interface.symbols = [], examples = [] y web = {pages: rutas a capturar, mockups: los adjuntos del cliente
  con su id y sha256 tal como vienen en el pedido}.
- Criterios "test" = comprobables con un navegador; cada uno lleva web_check {kind, page, args}:
  text_present [texto] · title_contains [texto] · element_visible [rol ARIA, nombre] ·
  link_navigates [nombre del link, "#ancla" o "/ruta"] ·
  form_submits [endpoint, texto de confirmación, "Etiqueta=valor de prueba", ..., "boton=Nombre del botón"] ·
  no_horizontal_overflow [].
- Criterios "visual" = solo lo que exige mirar (parecido al mockup, legibilidad en móvil); los juzga un
  modelo con capturas. Todo lo que se pueda comprobar con el DOM debe ser "test", no "visual".
- allowed_dependencies = paquetes npm permitidos.
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

""" + STATIC_CATALOG + "\n" + WEB_CATALOG

REVIEW_SYSTEM = """\
Eres un auditor de contratos de aceptación de software. Revisas un borrador ANTES de que se
congele y señalas criterios que no se puedan verificar de forma objetiva.

Para cada problema emite un issue con:
- kind: vague (palabras sin umbral: "rápido", "bien", "intuitivo"), unverifiable (no se puede
  comprobar ejecutando código ni leyendo el fuente), ambiguous (admite dos lecturas que llevarían
  a tests distintos), wrong_type (el tipo test/static/visual no corresponde), missing_check,
  duplicate, implementation_detail, other.
- implementation_detail: el criterio (o su web_check) impone CÓMO se construye en vez de QUÉ debe
  observar el cliente: una etiqueta HTML o un rol concreto ("debe ser un <button>", element_visible
  con rol "button" para algo que es naturalmente un link), clases o ids de CSS, un framework o
  librería que el cliente no pidió, nombres de funciones internas o la estructura del código.
  Sugiere el resultado observable equivalente ("al hacer clic en «Pedir ahora» se llega al
  formulario", "el texto «Enviar» es visible y al pulsarlo se envía el formulario"). Excepciones
  legítimas: la interfaz pública que el cliente sí pidió (nombre de módulo/función en perfil
  script, rutas de una API) y restricciones explícitas del pedido ("solo biblioteca estándar").
- severity: blocker si impide escribir un test o chequeo objetivo, o si un criterio de
  implementation_detail reprobaría una entrega que cumple lo que el cliente quería; si no, warning.
- suggestion: reescritura concreta del criterio (con umbrales, formatos y casos borde explícitos).
Revisa también que los ejemplos sean coherentes con los criterios y con la interfaz.
No repitas problemas que ya figuran en "issues_deterministas". Si todo está bien, devuelve issues vacío.
ok = true solo si no hay bloqueantes.

""" + STATIC_CATALOG + "\n" + WEB_CATALOG


def draft_task(request: str, mockups: list[dict] | None = None) -> TaskDraft:
    attachments = ""
    if mockups:
        attachments = "\nMockups adjuntos (id, sha256, descripción):\n" + "\n".join(
            f"- {m['id']} {m['sha256']} {m.get('description', '')}" for m in mockups)
    draft = structured_completion(
        [
            {"role": "system", "content": DRAFT_SYSTEM},
            {"role": "user", "content": f"Pedido del cliente:\n<<<\n{request}\n>>>{attachments}"},
        ],
        TaskDraft,
        schema_name="task_draft",
    )
    if draft.web and mockups:
        # Los hashes vienen de los archivos subidos, no de lo que el modelo copió (en una prueba
        # real los devolvió sin el prefijo "sha256:"). Mockups que el cliente no subió se descartan.
        uploaded = {m["id"]: m["sha256"] for m in mockups}
        fixed = [m.model_copy(update={"sha256": uploaded[m.id]}) for m in draft.web.mockups if m.id in uploaded]
        draft = draft.model_copy(update={"web": draft.web.model_copy(update={"mockups": fixed})})
    return draft


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
