"""Modelos del contrato de una tarea.

Todo lo que entra en `rules_hash` es str/bool/list (sin floats), para que el JSON
canónico sea representable.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Profile = Literal["script", "api", "web"]
CriterionType = Literal["test", "static", "visual"]
SymbolKind = Literal["function", "class", "asgi_app"]
StaticCheckKind = Literal[
    "forbid_imports",       # args: módulos prohibidos (p. ej. "requests", "os.system" no: usar forbid_calls)
    "forbid_calls",         # args: llamadas prohibidas por nombre ("eval", "exec", "os.system", "subprocess.run")
    "imports_allowed",      # args: ignorados; solo stdlib + allowed_dependencies + módulos propios
    "require_type_hints",   # args: nombres de símbolo (vacío = símbolos públicos de la interfaz)
    "require_docstrings",   # args: nombres de símbolo (vacío = símbolos públicos de la interfaz)
    "max_function_lines",   # args: ["<n>"]
    "ruff_clean",           # args: códigos o prefijos de reglas de ruff ("F", "E9", "B006")
]

WebCheckKind = Literal[
    "text_present",            # args: [texto]               — visible en escritorio y en móvil
    "title_contains",          # args: [texto]               — <title> de la página
    "element_visible",         # args: [rol, nombre]         — p. ej. ["button", "Enviar"], en ambos anchos
    "link_navigates",          # args: [nombre, destino]     — destino "#ancla", "/ruta" o URL
    "form_submits",            # args: [endpoint, texto_de_confirmación, "Etiqueta=valor", ..., "boton=Nombre"]
    "no_horizontal_overflow",  # args: []                    — a 390 px no hay scroll horizontal
]

CRITERION_ID_RE = re.compile(r"^C[0-9]{1,3}$")
MOCKUP_ID_RE = re.compile(r"^M[0-9]{1,3}$")
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InterfaceSymbol(_Strict):
    module: str = Field(description="Módulo Python con puntos, relativo a la raíz de la entrega (p. ej. 'ventas')")
    name: str = Field(description="Nombre del símbolo exportado")
    kind: SymbolKind
    signature: str = Field(description="Firma con tipos, p. ej. 'resumir_ventas(texto: str) -> dict[str, int]'")
    description: str


class Interface(_Strict):
    language: Literal["python"] = "python"
    symbols: list[InterfaceSymbol]


class StaticCheck(_Strict):
    kind: StaticCheckKind
    args: list[str]


class WebCheck(_Strict):
    kind: WebCheckKind
    page: str = Field(description="Ruta de la página, p. ej. '/'")
    args: list[str]


class Criterion(_Strict):
    id: str = Field(description="C1, C2, ...")
    text: str = Field(description="Criterio atómico y verificable, en lenguaje del cliente")
    type: CriterionType
    required: bool
    static_check: StaticCheck | None = Field(description="Solo para type=static; null en otro caso")
    web_check: WebCheck | None = Field(default=None, description="Solo para type=test en perfil web; null en otro caso")


class Mockup(_Strict):
    id: str = Field(description="M1, M2, ...")
    sha256: str = Field(description="Hash de la imagen subida a /mockups")
    page: str
    viewport: Literal["desktop", "mobile"]
    description: str


class WebSpec(_Strict):
    pages: list[str] = Field(description="Rutas relevantes a capturar, p. ej. ['/']")
    mockups: list[Mockup]


class Example(_Strict):
    description: str
    input: str = Field(description="script: expresión de llamada con literales, p. ej. 'f(\"a\", 2)'. api: 'METHOD /ruta <json>'")
    expected_output: str = Field(description="script: literal Python del resultado o 'raises ValueError'. api: '<status> <json>'")


class TaskDraft(_Strict):
    title: str
    profile: Profile
    description: str
    interface: Interface
    criteria: list[Criterion]
    allowed_dependencies: list[str] = Field(description="Nombres PyPI (o npm en perfil web) permitidos; vacío = ninguno")
    examples: list[Example]
    web: WebSpec | None = Field(default=None, description="Solo perfil web: páginas y mockups; null en otro caso")


IssueKind = Literal[
    "vague", "unverifiable", "ambiguous", "wrong_type", "missing_check", "duplicate", "implementation_detail", "other",
]


class ReviewIssue(_Strict):
    criterion_id: str = Field(description="Id del criterio, o '*' si aplica a todo el borrador")
    kind: IssueKind
    severity: Literal["blocker", "warning"]
    explanation: str
    suggestion: str = Field(description="Reescritura concreta propuesta para el criterio")


class DraftReview(_Strict):
    ok: bool = Field(description="true si no hay bloqueantes")
    issues: list[ReviewIssue]
    summary: str


class MockupRef(BaseModel):
    id: str
    sha256: str
    description: str = ""


class TaskRequest(BaseModel):
    request: str = Field(min_length=10, max_length=8000)
    mockups: list[MockupRef] = []


class ReviewRequest(BaseModel):
    draft: TaskDraft


class ConfirmRequest(BaseModel):
    draft: TaskDraft
