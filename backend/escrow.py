"""Cliente del contrato de escrow (Soroban, testnet) usado por el backend.

- `task_key`: el task_id (32 hex) se mapea a BytesN<32> como sha256 del id en ASCII.
- Los hashes "sha256:<hex>" del backend (rules, workspace, verdict) viajan como BytesN<32>.
- Los montos se expresan en unidades del token (USDC tiene 7 decimales en Stellar).
- Firmas: el árbitro firma con ARBITER_SECRET. Para la demo, el cliente y el freelancer de
  prueba firman con DEMO_*_SECRET (solo testnet). Una billetera real puede firmar el XDR que
  devuelve `prepare()` y enviarlo con `submit()`.
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from decimal import Decimal

from stellar_sdk import Keypair, SorobanServer, TransactionBuilder, TransactionEnvelope, scval
from stellar_sdk.exceptions import PrepareTransactionException
from stellar_sdk.soroban_rpc import GetTransactionStatus, SendTransactionStatus

USDC_DECIMALS = 7
EXPLORER = "https://stellar.expert/explorer/testnet"
STATUS_NAMES = {0: "funded", 1: "accepted", 2: "released", 3: "refunded"}


class EscrowError(RuntimeError):
    pass


def task_key(task_id: str) -> bytes:
    return hashlib.sha256(task_id.encode("ascii")).digest()


def hash_bytes(value: str) -> bytes:
    digest = value.removeprefix("sha256:")
    raw = bytes.fromhex(digest)
    if len(raw) != 32:
        raise ValueError(f"no es un sha256: {value}")
    return raw


def to_units(amount: str | Decimal | int) -> int:
    units = Decimal(str(amount)) * (10 ** USDC_DECIMALS)
    if units != units.to_integral_value() or units <= 0:
        raise ValueError("monto inválido (máx. 7 decimales y mayor que 0)")
    return int(units)


def tx_url(tx_hash: str) -> str:
    return f"{EXPLORER}/tx/{tx_hash}"


def contract_url(contract_id: str) -> str:
    return f"{EXPLORER}/contract/{contract_id}"


@dataclass(frozen=True)
class TxResult:
    action: str
    hash: str
    ledger: int | None

    @property
    def url(self) -> str:
        return tx_url(self.hash)

    def as_dict(self) -> dict:
        return {"action": self.action, "hash": self.hash, "ledger": self.ledger, "url": self.url}


def configured() -> bool:
    return bool(os.getenv("ESCROW_CONTRACT_ID") and os.getenv("ARBITER_SECRET"))


class EscrowClient:
    def __init__(self):
        if not configured():
            raise EscrowError("Falta configurar ESCROW_CONTRACT_ID / ARBITER_SECRET")
        self.server = SorobanServer(os.getenv("STELLAR_RPC_URL", "https://soroban-testnet.stellar.org"))
        self.passphrase = os.getenv("STELLAR_NETWORK_PASSPHRASE", "Test SDF Network ; September 2015")
        self.contract_id = os.environ["ESCROW_CONTRACT_ID"]

    # --- firmantes -------------------------------------------------------------------------

    @staticmethod
    def signer(name: str) -> Keypair:
        env = {"arbiter": "ARBITER_SECRET", "client": "DEMO_CLIENT_SECRET", "freelancer": "DEMO_FREELANCER_SECRET"}[name]
        secret = os.getenv(env)
        if not secret:
            raise EscrowError(f"Falta {env}")
        return Keypair.from_secret(secret)

    @classmethod
    def demo_addresses(cls) -> dict[str, str]:
        out = {}
        for name in ("arbiter", "client", "freelancer"):
            try:
                out[name] = cls.signer(name).public_key
            except EscrowError:
                pass
        return out

    # --- transacciones ---------------------------------------------------------------------

    def _build(self, source: str, fn: str, args: list):
        account = self.server.load_account(source)
        tx = (TransactionBuilder(account, self.passphrase, base_fee=100)
              .append_invoke_contract_function_op(self.contract_id, fn, args)
              .set_timeout(120).build())
        try:
            return self.server.prepare_transaction(tx)
        except PrepareTransactionException as exc:
            raise EscrowError(f"la simulación de {fn} falló: {_short(exc)}") from exc

    def prepare(self, source: str, fn: str, args: list) -> str:
        """XDR listo para que lo firme una billetera (p. ej. Freighter)."""
        return self._build(source, fn, args).to_xdr()

    def submit(self, signed_xdr: str, action: str) -> TxResult:
        tx = TransactionEnvelope.from_xdr(signed_xdr, self.passphrase)
        return self._send(tx, action)

    def _send(self, tx, action: str) -> TxResult:
        sent = self.server.send_transaction(tx)
        if sent.status == SendTransactionStatus.ERROR:
            raise EscrowError(f"{action}: la red rechazó la transacción ({sent.error_result_xdr})")
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            got = self.server.get_transaction(sent.hash)
            if got.status == GetTransactionStatus.SUCCESS:
                return TxResult(action, sent.hash, got.ledger)
            if got.status == GetTransactionStatus.FAILED:
                raise EscrowError(f"{action}: la transacción falló on-chain ({sent.hash})")
            time.sleep(1.5)
        raise EscrowError(f"{action}: sin confirmación en 90 s ({sent.hash})")

    def invoke(self, signer: Keypair, fn: str, args: list, action: str | None = None) -> TxResult:
        tx = self._build(signer.public_key, fn, args)
        tx.sign(signer)
        return self._send(tx, action or fn)

    # --- operaciones del contrato ----------------------------------------------------------

    @staticmethod
    def deposit_args(client: str, task_id: str, amount_units: int, deadline: int, rules_hash: str) -> list:
        return [scval.to_address(client), scval.to_bytes(task_key(task_id)), scval.to_int128(amount_units),
                scval.to_uint64(deadline), scval.to_bytes(hash_bytes(rules_hash))]

    def deposit(self, signer: Keypair, task_id: str, amount_units: int, deadline: int, rules_hash: str) -> TxResult:
        return self.invoke(signer, "deposit", self.deposit_args(signer.public_key, task_id, amount_units, deadline, rules_hash))

    def accept(self, signer: Keypair, task_id: str) -> TxResult:
        return self.invoke(signer, "accept", [scval.to_address(signer.public_key), scval.to_bytes(task_key(task_id))])

    def release(self, task_id: str, workspace_hash: str, verdict_hash: str) -> TxResult:
        args = [scval.to_bytes(task_key(task_id)), scval.to_bytes(hash_bytes(workspace_hash)),
                scval.to_bytes(hash_bytes(verdict_hash))]
        return self.invoke(self.signer("arbiter"), "release", args)

    def client_release(self, signer: Keypair, task_id: str) -> TxResult:
        return self.invoke(signer, "client_release", [scval.to_bytes(task_key(task_id))])

    def refund(self, signer: Keypair, task_id: str) -> TxResult:
        return self.invoke(signer, "refund", [scval.to_bytes(task_key(task_id))])

    def get(self, task_id: str) -> dict | None:
        """Estado on-chain del escrow (simulación, sin enviar nada)."""
        source = self.signer("arbiter").public_key
        account = self.server.load_account(source)
        tx = (TransactionBuilder(account, self.passphrase, base_fee=100)
              .append_invoke_contract_function_op(self.contract_id, "get_escrow", [scval.to_bytes(task_key(task_id))])
              .set_timeout(60).build())
        sim = self.server.simulate_transaction(tx)
        if sim.error:
            if "Error(Contract, #2)" in sim.error:  # NotFound
                return None
            raise EscrowError(f"get_escrow falló: {sim.error[:300]}")
        from stellar_sdk import xdr as stellar_xdr

        raw = scval.to_native(stellar_xdr.SCVal.from_xdr(sim.results[0].xdr))
        return {
            "client": raw["client"].address if hasattr(raw["client"], "address") else str(raw["client"]),
            "freelancer": (raw["freelancer"].address if raw.get("freelancer") is not None else None),
            "amount_units": int(raw["amount"]),
            "amount": str(Decimal(int(raw["amount"])) / (10 ** USDC_DECIMALS)),
            "deadline": int(raw["deadline"]),
            "rules_hash": "sha256:" + bytes(raw["rules_hash"]).hex(),
            "status": STATUS_NAMES.get(int(raw["status"]), str(raw["status"])),
            "workspace_hash": ("sha256:" + bytes(raw["workspace_hash"]).hex()) if raw.get("workspace_hash") else None,
            "verdict_hash": ("sha256:" + bytes(raw["verdict_hash"]).hex()) if raw.get("verdict_hash") else None,
        }


def _short(exc: Exception) -> str:
    text = str(exc)
    for marker in ("Error(Contract, #", "HostError"):
        if marker in text:
            i = text.index(marker)
            return text[i:i + 120]
    return text[:300]
