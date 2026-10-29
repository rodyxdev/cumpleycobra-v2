"""Constructores compartidos por los tests de la fase 2."""

from pathlib import Path

from backend.contract.models import TaskDraft

CONTRACT_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "ventas" / "contrato.json"
FIXTURES = CONTRACT_PATH.parent


def ventas_draft(**changes) -> TaskDraft:
    draft = TaskDraft.model_validate_json(CONTRACT_PATH.read_text(encoding="utf-8"))
    return draft.model_copy(update=changes) if changes else draft


def write_files(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
    return root
