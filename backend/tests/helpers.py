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


def headers_for(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def make_user(name: str, demo_wallet: str | None = None) -> dict:
    """Crea un usuario; si `demo_wallet` es client/freelancer, le asigna esa billetera de prueba."""
    from backend.auth import NewUser, create_user, update_address
    from backend.escrow import EscrowClient

    user, token = create_user(NewUser(name=name))
    if demo_wallet:
        update_address(user.user_id, EscrowClient.signer(demo_wallet).public_key)
    return headers_for(token)


class FakeEscrow:
    """Sustituto del contrato: registra llamadas y lleva el estado como lo haría la cadena."""

    contract_id = "CFAKE" + "A" * 51

    def __init__(self):
        self.calls: list[tuple] = []
        self.state: dict[str, dict] = {}

    def _tx(self, action):
        from backend.escrow import TxResult

        return TxResult(action, f"{len(self.calls):064x}", 1000 + len(self.calls))

    def deposit(self, signer, task_id, amount_units, deadline, rules_hash):
        self.calls.append(("deposit", task_id, amount_units, rules_hash))
        self.state[task_id] = {"status": "funded", "client": signer.public_key, "amount_units": amount_units}
        return self._tx("deposit")

    def accept(self, signer, task_id):
        self.calls.append(("accept", task_id, signer.public_key))
        self.state[task_id].update(status="accepted", freelancer=signer.public_key)
        return self._tx("accept")

    def release(self, task_id, workspace_hash, verdict_hash):
        self.calls.append(("release", task_id, workspace_hash, verdict_hash))
        self.state[task_id].update(status="released", workspace_hash=workspace_hash, verdict_hash=verdict_hash)
        return self._tx("release")

    def client_release(self, signer, task_id):
        self.calls.append(("client_release", task_id, signer.public_key))
        self.state[task_id].update(status="released")
        return self._tx("client_release")

    def refund(self, signer, task_id):
        from backend.escrow import EscrowError

        raise EscrowError("Error(Contract, #6) DeadlineNotReached")

    def get(self, task_id):
        return self.state.get(task_id)
