"""Capa 2 — análisis estático determinista (ast / ruff). Nunca ejecuta el código entregado.

Incluye:
- los chequeos del catálogo que piden los criterios `static`;
- señales sospechosas que se calculan siempre (inyección dirigida al evaluador, ejemplos
  públicos incrustados en el código, manipulación de la infraestructura de test). Las señales
  no deciden solas: se reportan y se pasan al juez.

Se analizan los archivos `.py` de la entrega salvo los tests propios del freelancer
(`tests/`, `test_*.py`, `*_test.py`, `conftest.py`), que nunca se ejecutan.
"""

from __future__ import annotations

import ast
import io
import json
import re
import subprocess
import sys
import tokenize
from dataclasses import dataclass, field
from pathlib import Path

from backend.contract.models import Criterion, TaskDraft
from backend.contract.validation import normalize_dist_name, parse_call_example, parse_expected


@dataclass
class SourceFile:
    rel: str
    text: str
    tree: ast.Module | None
    syntax_error: str | None = None


@dataclass
class StaticResult:
    criterion_id: str
    passed: bool
    evidence: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Signal:
    kind: str      # injection | cheating | tampering
    file: str
    line: int
    snippet: str
    detail: str

    @property
    def ref(self) -> str:
        return f"{self.file}:{self.line}"


def _is_own_test(rel: str) -> bool:
    parts = rel.split("/")
    name = parts[-1]
    return "tests" in parts[:-1] or name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"


def load_sources(workspace: Path) -> list[SourceFile]:
    sources = []
    for path in sorted(workspace.rglob("*.py")):
        rel = path.relative_to(workspace).as_posix()
        if ".git" in rel.split("/") or _is_own_test(rel):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            sources.append(SourceFile(rel, text, ast.parse(text, filename=rel)))
        except SyntaxError as exc:
            sources.append(SourceFile(rel, text, None, f"{rel}:{exc.lineno}: {exc.msg}"))
    return sources


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _alias_map(tree: ast.Module) -> dict[str, str]:
    """Nombre local → nombre completo (`import os as o` → o: os; `from os import system` → system: os.system)."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                aliases[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for a in node.names:
                aliases[a.asname or a.name] = f"{node.module}.{a.name}"
    return aliases


def _resolve(name: str, aliases: dict[str, str]) -> str:
    head, _, rest = name.partition(".")
    full = aliases.get(head, head)
    return f"{full}.{rest}" if rest else full


def imported_modules(src: SourceFile) -> list[tuple[str, int]]:
    """(módulo absoluto, línea) de cada import, incluido `__import__`/`importlib.import_module` con literal."""
    found = []
    for node in ast.walk(src.tree):
        if isinstance(node, ast.Import):
            found += [(a.name, node.lineno) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append((node.module, node.lineno))
        elif isinstance(node, ast.Call):
            fn = _dotted(node.func)
            if fn in ("__import__", "importlib.import_module") and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    found.append((arg.value, node.lineno))
    return found


def _line(src: SourceFile, lineno: int) -> str:
    lines = src.text.splitlines()
    return lines[lineno - 1].strip() if 0 < lineno <= len(lines) else ""


# --- chequeos del catálogo ---------------------------------------------------------------

def _forbid_imports(sources, args, draft) -> list[str]:
    hits = []
    for src in sources:
        for module, line in imported_modules(src):
            if any(module == a or module.startswith(a + ".") for a in args):
                hits.append(f"{src.rel}:{line}: importa {module}")
    return hits


def _forbid_calls(sources, args, draft) -> list[str]:
    targets = set(args)
    hits = []
    for src in sources:
        aliases = _alias_map(src.tree)
        for node in ast.walk(src.tree):
            name = None
            if isinstance(node, ast.Call):
                name = _dotted(node.func)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in targets:
                name = node.id  # referencia sin llamar: `f = eval`
            if not name:
                continue
            full = _resolve(name, aliases)
            if full in targets or name in targets or full.removeprefix("builtins.") in targets:
                hits.append(f"{src.rel}:{node.lineno}: usa {full}  ->  {_line(src, node.lineno)}")
    return sorted(set(hits))


def _local_modules(sources) -> set[str]:
    return {src.rel.split("/")[0].removesuffix(".py") for src in sources}


def _imports_allowed(sources, args, draft) -> list[str]:
    allowed = set(sys.stdlib_module_names) | _local_modules(sources)
    allowed |= {normalize_dist_name(d).replace("-", "_") for d in draft.allowed_dependencies}
    hits = []
    for src in sources:
        for module, line in imported_modules(src):
            top = module.split(".")[0]
            if top not in allowed:
                hits.append(f"{src.rel}:{line}: importa {module}, que no es stdlib ni una dependencia permitida")
    return hits


def _interface_functions(sources, draft, names):
    """(src, nodo) de cada función/clase de la interfaz (o de `names` si se indican)."""
    wanted = {s.name: s.module for s in draft.interface.symbols if s.kind in ("function", "class")}
    if names:
        wanted = {n: wanted.get(n) for n in names}
    found = []
    for src in sources:
        module = src.rel.removesuffix(".py").replace("/", ".").removesuffix(".__init__")
        for node in src.tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in wanted:
                if wanted[node.name] in (None, module):
                    found.append((src, node))
    missing = set(wanted) - {n.name for _, n in found}
    return found, missing


def _require_type_hints(sources, args, draft) -> list[str]:
    found, missing = _interface_functions(sources, draft, args)
    hits = [f"no se encontró {m}" for m in sorted(missing)]
    for src, node in found:
        funcs = [node] if not isinstance(node, ast.ClassDef) else [
            n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith("_")
        ]
        for fn in funcs:
            params = [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]
            params = [p for p in params if p.arg not in ("self", "cls")]
            params += [p for p in (fn.args.vararg, fn.args.kwarg) if p]
            sin_tipo = [p.arg for p in params if p.annotation is None]
            if sin_tipo:
                hits.append(f"{src.rel}:{fn.lineno}: {fn.name} sin anotación en {', '.join(sin_tipo)}")
            if fn.returns is None:
                hits.append(f"{src.rel}:{fn.lineno}: {fn.name} sin anotación de retorno")
    return hits


def _require_docstrings(sources, args, draft) -> list[str]:
    found, missing = _interface_functions(sources, draft, args)
    hits = [f"no se encontró {m}" for m in sorted(missing)]
    hits += [f"{src.rel}:{n.lineno}: {n.name} sin docstring" for src, n in found if not ast.get_docstring(n)]
    return hits


def _max_function_lines(sources, args, draft) -> list[str]:
    limit = int(args[0])
    hits = []
    for src in sources:
        for node in ast.walk(src.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                length = node.end_lineno - node.lineno + 1
                if length > limit:
                    hits.append(f"{src.rel}:{node.lineno}: {node.name} tiene {length} líneas (máx. {limit})")
    return hits


def _ruff_clean(sources, args, draft, workspace: Path) -> list[str]:
    if not sources:
        return []
    select = ",".join(args) or "F,E9"
    cmd = [sys.executable, "-m", "ruff", "check", "--isolated", "--no-cache", "--output-format", "json",
           "--select", select, *[src.rel for src in sources]]
    proc = subprocess.run(cmd, cwd=workspace, capture_output=True, text=True, timeout=60)
    if proc.returncode not in (0, 1):
        return [f"ruff falló: {proc.stderr.strip()[-300:]}"]
    findings = json.loads(proc.stdout or "[]")
    return [f"{Path(f['filename']).relative_to(workspace.resolve()).as_posix()}:{f['location']['row']}: {f['code']} {f['message']}"
            for f in findings]


_CHECKS = {
    "forbid_imports": _forbid_imports,
    "forbid_calls": _forbid_calls,
    "imports_allowed": _imports_allowed,
    "require_type_hints": _require_type_hints,
    "require_docstrings": _require_docstrings,
    "max_function_lines": _max_function_lines,
}


def run_static_check(criterion: Criterion, sources: list[SourceFile], draft: TaskDraft, workspace: Path) -> StaticResult:
    check = criterion.static_check
    broken = [s.syntax_error for s in sources if s.tree is None]
    if broken:
        return StaticResult(criterion.id, False, [f"error de sintaxis: {e}" for e in broken])
    if check.kind == "ruff_clean":
        hits = _ruff_clean(sources, check.args, draft, workspace)
    else:
        hits = _CHECKS[check.kind](sources, check.args, draft)
    return StaticResult(criterion.id, not hits, hits[:50])


# --- señales sospechosas -----------------------------------------------------------------

_INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(ia|ai|llm|gpt|chatgpt|claude|llama|muse|modelo|model|asistente|assistant|evaluador|evaluator"
        r"|juez|judge|grader|revisor|reviewer|verificador|verifier)\b[^\n]{0,80}"
        r"\b(apru[eé]b\w*|aprob\w*|approv\w*|pass|marc\w*|mark|calific\w*|grade|ignor\w*|acept\w*|accept\w*)",
        r"\b(ignore|disregard|forget)\b[^\n]{0,30}\b(previous|prior|above|all|earlier)\b[^\n]{0,20}\b(instructions?|rules?)",
        r"\b(ignora|olvida|omite)\b[^\n]{0,30}\b(instrucciones|reglas|criterios)",
        r"\b(system prompt|prompt del sistema|jailbreak)\b",
        r"\b(verdict|veredicto)\b[^\n]{0,40}\b(pass|aprobad|approved|aprueba)",
        r"\b(test_defect|todos los criterios (se )?cumplen|all criteria (are )?(met|satisfied))\b",
    )
]

_TAMPER_NAMES = {
    "sys._getframe": "inspecciona frames de la pila",
    "inspect.stack": "inspecciona la pila de llamadas",
    "inspect.currentframe": "inspecciona frames de la pila",
    "sys.settrace": "instala un tracer",
    "sys.setprofile": "instala un profiler",
    "os._exit": "termina el proceso saltándose pytest",
    "gc.get_objects": "recorre objetos del intérprete",
    "gc.get_referrers": "recorre objetos del intérprete",
    "atexit.register": "registra código al salir del intérprete",
}
_TAMPER_MODULES = {"pytest", "_pytest", "pluggy", "unittest.mock", "junitparser"}
_TAMPER_STRINGS = ("/hidden", "junit", "/out/", "conftest", "PYTEST_CURRENT_TEST")


def _comments_and_strings(src: SourceFile) -> list[tuple[int, str]]:
    found = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src.text).readline):
            if tok.type == tokenize.COMMENT:
                found.append((tok.start[0], tok.string))
    except (tokenize.TokenError, SyntaxError):
        pass
    if src.tree is not None:
        for node in ast.walk(src.tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                found.append((node.lineno, node.value))
    return found


def _injection_signals(src: SourceFile) -> list[Signal]:
    signals = []
    for lineno, text in _comments_and_strings(src):
        for pattern in _INJECTION_PATTERNS:
            m = pattern.search(text)
            if m:
                signals.append(Signal("injection", src.rel, lineno, m.group(0)[:160],
                                      "texto dirigido al evaluador automático"))
                break
    return signals


def _tamper_signals(src: SourceFile) -> list[Signal]:
    if src.tree is None:
        return []
    signals = []
    aliases = _alias_map(src.tree)
    for module, line in imported_modules(src):
        if any(module == m or module.startswith(m + ".") for m in _TAMPER_MODULES):
            signals.append(Signal("tampering", src.rel, line, _line(src, line), f"importa {module} desde el código entregado"))
    for node in ast.walk(src.tree):
        name = _dotted(node.func) if isinstance(node, ast.Call) else None
        if name:
            full = _resolve(name, aliases)
            if full in _TAMPER_NAMES:
                signals.append(Signal("tampering", src.rel, node.lineno, _line(src, node.lineno), _TAMPER_NAMES[full]))
        if isinstance(node, (ast.Name, ast.Attribute)) and _dotted(node) in ("__builtins__", "builtins.__dict__"):
            signals.append(Signal("tampering", src.rel, node.lineno, _line(src, node.lineno), "manipula builtins"))
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for needle in _TAMPER_STRINGS:
                if needle in node.value:
                    signals.append(Signal("tampering", src.rel, node.lineno, _line(src, node.lineno),
                                          f"referencia a la infraestructura de test ({needle})"))
                    break
    return signals


def _example_literals(draft: TaskDraft) -> tuple[set[str], list[object]]:
    """Strings de entrada (≥ 6 caracteres) y valores esperados de los ejemplos públicos."""
    inputs: set[str] = set()
    outputs: list[object] = []
    if draft.profile != "script":
        return inputs, outputs
    for ex in draft.examples:
        try:
            _, args, kwargs = parse_call_example(ex.input)
            kind, value = parse_expected(ex.expected_output)
        except (ValueError, SyntaxError):
            continue
        inputs |= {a for a in [*args, *kwargs.values()] if isinstance(a, str) and len(a) >= 6}
        if kind == "value" and value not in (None, True, False, 0, "", {}, []):
            outputs.append(value)
    return inputs, outputs


def _cheating_signals(src: SourceFile, draft: TaskDraft) -> list[Signal]:
    if src.tree is None:
        return []
    inputs, outputs = _example_literals(draft)
    signals = []
    for node in ast.walk(src.tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in inputs:
            signals.append(Signal("cheating", src.rel, node.lineno, _line(src, node.lineno),
                                  "contiene literalmente la entrada de un ejemplo público"))
        elif isinstance(node, (ast.Dict, ast.List, ast.Tuple, ast.Set)) and outputs:
            try:
                value = ast.literal_eval(node)
            except (ValueError, SyntaxError, TypeError):
                continue
            if value in outputs:
                signals.append(Signal("cheating", src.rel, node.lineno, _line(src, node.lineno),
                                      "contiene literalmente la salida esperada de un ejemplo público"))
    return signals


def collect_signals(sources: list[SourceFile], draft: TaskDraft) -> list[Signal]:
    signals = []
    for src in sources:
        signals += _injection_signals(src) + _tamper_signals(src) + _cheating_signals(src, draft)
    unique = {(s.kind, s.file, s.line, s.detail): s for s in signals}
    return sorted(unique.values(), key=lambda s: (s.file, s.line, s.kind))
