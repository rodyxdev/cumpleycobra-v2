"""Migración de suites de la fase 2 (importaban la entrega) al formato caja negra.

La transformación es determinista (AST, sin LLM): cada import de un módulo de la interfaz
se sustituye por proxies del canal.

    from ventas import resumir_ventas as rv   →   from cc_channel import remote
                                                  rv = remote("ventas", "resumir_ventas")
    import ventas                             →   from cc_channel import remote_module
                                                  ventas = remote_module("ventas")

Después se revalida: la suite debe fallar contra la entrega vacía y el stub y, si se da
una entrega de referencia, pasar contra ella.

Uso: python -m backend.blackbox.migrate [--reference backend/fixtures/ventas/correcta] [task_id ...]
"""

from __future__ import annotations

import argparse
import ast
import json
import shutil
import tempfile
from pathlib import Path

from backend.contract.models import TaskDraft
from backend.tasks import hidden_dir, load_task, now_iso, save_task
from backend.verification.junit import hidden_test_filename
from backend.verification.suite import (
    SUITE_FORMAT,
    build_stub,
    run_suite,
    static_problems,
    suite_hash,
    validation_problems,
    write_suite_dir,
)


class MigrationError(ValueError):
    pass


def migrate_test_code(code: str, interface_modules: set[str]) -> str:
    """Reescribe los imports de la entrega como proxies de `cc_channel`."""
    tree = ast.parse(code)
    lines = code.splitlines()
    replacements: list[tuple[int, int, list[str]]] = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module in interface_modules:
            new = ["from cc_channel import remote"]
            for alias in node.names:
                if alias.name == "*":
                    raise MigrationError(f"`from {node.module} import *` no se puede migrar")
                new.append(f'{alias.asname or alias.name} = remote("{node.module}", "{alias.name}")')
            replacements.append((node.lineno, node.end_lineno, new))
        elif isinstance(node, ast.Import) and any(a.name in interface_modules for a in node.names):
            new = []
            for alias in node.names:
                if alias.name in interface_modules:
                    new += ["from cc_channel import remote_module",
                            f'{alias.asname or alias.name} = remote_module("{alias.name}")']
                else:
                    new.append(f"import {alias.name}" + (f" as {alias.asname}" if alias.asname else ""))
            replacements.append((node.lineno, node.end_lineno, new))
    # Un import de la entrega dentro de una función también rompería el aislamiento.
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)) and node not in tree.body:
            mods = [node.module] if isinstance(node, ast.ImportFrom) else [a.name for a in node.names]
            if any(m in interface_modules for m in mods):
                raise MigrationError(f"import de la entrega anidado en la línea {node.lineno}")
    for start, end, new in sorted(replacements, reverse=True):
        lines[start - 1:end] = new
    migrated = "\n".join(lines) + "\n"
    compile(migrated, "<migrado>", "exec")
    return migrated


def migrate_task(task_id: str, reference: Path | None = None) -> dict:
    record = load_task(task_id)
    if record.suite_format == SUITE_FORMAT:
        return {"task_id": task_id, "skipped": "ya está en formato caja negra"}
    draft: TaskDraft = record.draft
    modules = {s.module for s in draft.interface.symbols}
    src = hidden_dir(task_id)
    files = {}
    for c in draft.criteria:
        if c.type == "test":
            name = hidden_test_filename(c.id)
            files[name] = migrate_test_code((src / name).read_text(encoding="utf-8"), modules)

    with tempfile.TemporaryDirectory(prefix="cc-migrate-") as tmp:
        tmp = Path(tmp)
        suite_dir = tmp / "suite"
        write_suite_dir(suite_dir, files)
        empty_ws, stub_ws = tmp / "empty", tmp / "stub"
        empty_ws.mkdir()
        stub_ws.mkdir()
        build_stub(draft.interface, stub_ws)
        empty_run, stub_run = run_suite(empty_ws, suite_dir, draft), run_suite(stub_ws, suite_dir, draft)
        problems = {name: static_problems(code) or validation_problems(name, empty_run, stub_run)
                    for name, code in files.items()}
        ref_summary = None
        if reference is not None:
            ref_run = run_suite(reference, suite_dir, draft)
            failing = [o.nodeid for o in ref_run.outcomes if o.outcome != "passed"]
            ref_summary = {"reference": str(reference), "passed": len(ref_run.outcomes) - len(failing),
                           "total": len(ref_run.outcomes), "failing": failing}
            for nodeid in failing:
                problems.setdefault(nodeid.split("::")[0], []).append(f"{nodeid} no pasa contra la referencia")
        bad = {k: v for k, v in problems.items() if v}
        result = {
            "task_id": task_id,
            "from": record.suite_format,
            "to": SUITE_FORMAT,
            "files": len(files),
            "tests_vs_empty": {"total": len(empty_run.outcomes), "passed": sum(o.outcome == "passed" for o in empty_run.outcomes)},
            "tests_vs_stub": {"total": len(stub_run.outcomes), "passed": sum(o.outcome == "passed" for o in stub_run.outcomes)},
            "reference": ref_summary,
            "problems": bad,
        }
        if bad:
            result["applied"] = False
            return result

        backup = src.with_name("hidden.inprocess-v1")
        if not backup.exists():
            shutil.copytree(src, backup)
        shutil.rmtree(src)
        write_suite_dir(src, files)
    new_hash = suite_hash(files)
    migration = {"at": now_iso(), "from": record.suite_format, "to": SUITE_FORMAT,
                 "old_suite_hash": record.suite_hash, "new_suite_hash": new_hash}
    save_task(record.model_copy(update={"suite_format": SUITE_FORMAT, "suite_hash": new_hash,
                                        "migrations": [*record.migrations, migration]}))
    result["applied"] = True
    result["new_suite_hash"] = new_hash
    return result


def main() -> None:
    from backend.config import get_settings

    parser = argparse.ArgumentParser()
    parser.add_argument("task_ids", nargs="*")
    parser.add_argument("--reference", type=Path)
    args = parser.parse_args()
    ids = args.task_ids or sorted(p.name for p in get_settings().task_store_dir.iterdir() if (p / "task.json").exists())
    for task_id in ids:
        print(json.dumps(migrate_task(task_id, args.reference), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
