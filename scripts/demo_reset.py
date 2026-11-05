"""Deja el entorno listo para grabar la demo. Se puede correr varias veces seguidas.

Uso:  .venv\\Scripts\\python scripts\\demo_reset.py [--keep-data] [--skip-images]

1. Borra tareas, usuarios, mockups y temporales de `.data/`, y los contenedores, volúmenes y
   redes `cc-*` que hayan quedado de corridas anteriores (no toca la caché npm ni las imágenes).
2. Verifica Docker, la API de Meta (el modelo MUSE_MODEL existe) y Stellar testnet (RPC sano y
   el contrato de escrow responde con el árbitro y el token del .env).
3. Fondea las billeteras demo: XLM con friendbot si bajan de 100, trustline de USDC y recarga
   del cliente hasta 1000 USDC de prueba (lo emite nuestra cuenta de testnet).
4. Construye de antemano las imágenes Docker (runner, SUT, Playwright) para no esperar al grabar.
5. Deja a mano en `demo/` los ZIPs de las entregas, los mockups y un guion con los pasos.
Termina con código 1 si algún chequeo falla.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from backend.config import get_settings  # noqa: E402  (carga .env)

DEMO = REPO / "demo"
MIN_XLM = Decimal("100")
TARGET_USDC = Decimal("1000")
HORIZON = "https://horizon-testnet.stellar.org"
FIXTURES = REPO / "backend" / "fixtures"

results: list[tuple[str, bool, str]] = []


def step(name: str):
    def wrap(fn):
        def run(*args, **kwargs):
            try:
                detail = fn(*args, **kwargs) or ""
                results.append((name, True, detail))
                print(f"  ✓ {name}" + (f" — {detail}" if detail else ""))
            except Exception as exc:  # noqa: BLE001 - se informa y se sigue con los demás pasos
                results.append((name, False, str(exc)[:300]))
                print(f"  ✗ {name} — {str(exc)[:300]}")
        return run
    return wrap


def docker(*args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


# --- 1. Limpieza ---------------------------------------------------------------------------

@step("Datos locales borrados (.data)")
def reset_data(keep: bool) -> str:
    work = get_settings().work_dir
    if keep:
        return "se conservan (--keep-data)"
    removed = []
    for name in ("tasks", "users.json", "mockups", "uploads", "workspaces", "submissions"):
        path = work / name
        if path.is_dir():
            shutil.rmtree(path, onexc=lambda f, p, e: (os.chmod(p, 0o700), f(p)))
            removed.append(name)
        elif path.exists():
            path.unlink()
            removed.append(name)
    return ", ".join(removed) or "ya estaba limpio"


@step("Restos de Docker de corridas anteriores")
def reset_docker() -> str:
    counts = {}
    names = docker("ps", "-aq", "--filter", "name=cc-").stdout.split()
    if names:
        docker("rm", "-f", *names)
    counts["contenedores"] = len(names)
    vols = [v for v in docker("volume", "ls", "-q").stdout.split() if v.startswith(("cc-deps-", "cc-web-"))]
    if vols:
        docker("volume", "rm", "-f", *vols)
    counts["volúmenes"] = len(vols)
    nets = [n for n in docker("network", "ls", "--format", "{{.Name}}").stdout.split() if n.startswith("cc-net-")]
    for net in nets:
        docker("network", "rm", net)
    counts["redes"] = len(nets)
    return ", ".join(f"{v} {k}" for k, v in counts.items())


# --- 2. Chequeos ---------------------------------------------------------------------------

@step("Docker responde")
def check_docker() -> str:
    proc = docker("version", "--format", "{{.Server.Version}}", timeout=30)
    if proc.returncode != 0:
        raise RuntimeError("el daemon no responde: abre Docker Desktop")
    return f"engine {proc.stdout.strip()}"


@step("API de Meta (Muse Spark) responde")
def check_meta() -> str:
    from backend.llm import get_client

    model = get_settings().muse_model
    ids = {m.id for m in get_client(timeout_s=30).models.list().data}
    if model not in ids:
        raise RuntimeError(f"{model} no está entre los modelos disponibles: {sorted(ids)}")
    return f"{model} disponible"


@step("Stellar testnet y contrato de escrow")
def check_testnet() -> str:
    from stellar_sdk import TransactionBuilder, scval, xdr

    from backend.escrow import EscrowClient

    client = EscrowClient()
    health = client.server.get_health()
    arbiter = client.signer("arbiter").public_key
    account = client.server.load_account(arbiter)
    tx = (TransactionBuilder(account, client.passphrase, base_fee=100)
          .append_invoke_contract_function_op(client.contract_id, "get_config", []).set_timeout(60).build())
    sim = client.server.simulate_transaction(tx)
    if sim.error:
        raise RuntimeError(f"get_config falló: {sim.error[:200]}")
    config = scval.to_native(xdr.SCVal.from_xdr(sim.results[0].xdr))
    onchain_arbiter, onchain_token = str(config["arbiter"].address), str(config["token"].address)
    if onchain_arbiter != arbiter:
        raise RuntimeError(f"el árbitro on-chain ({onchain_arbiter}) no es ARBITER_SECRET")
    if onchain_token != os.getenv("USDC_CONTRACT_ID"):
        raise RuntimeError(f"el token on-chain ({onchain_token}) no es USDC_CONTRACT_ID")
    return f"RPC {health.status}, contrato {client.contract_id[:8]}…, árbitro y token coinciden"


# --- 3. Fondeo -----------------------------------------------------------------------------

def _balances(server, public_key: str) -> dict[str, Decimal] | None:
    from stellar_sdk.exceptions import NotFoundError

    try:
        account = server.accounts().account_id(public_key).call()
    except NotFoundError:
        return None
    out = {}
    for b in account["balances"]:
        key = "XLM" if b["asset_type"] == "native" else f"{b['asset_code']}:{b['asset_issuer']}"
        out[key] = Decimal(b["balance"])
    return out


def _friendbot(public_key: str) -> None:
    with urllib.request.urlopen(f"https://friendbot.stellar.org/?addr={public_key}", timeout=60) as resp:
        resp.read()


def _submit(server, signer, build) -> None:
    from stellar_sdk import Network, TransactionBuilder

    account = server.load_account(signer.public_key)
    builder = TransactionBuilder(account, Network.TESTNET_NETWORK_PASSPHRASE, base_fee=100)
    tx = build(builder).set_timeout(60).build()
    tx.sign(signer)
    server.submit_transaction(tx)


@step("Billeteras demo fondeadas")
def fund_wallets() -> str:
    from stellar_sdk import Asset, Keypair, Server

    server = Server(HORIZON)
    code, issuer = os.environ["USDC_ASSET"].split(":")
    usdc = Asset(code, issuer)
    usdc_key = f"{code}:{issuer}"
    report = []
    wallets = {name: Keypair.from_secret(os.environ[var]) for name, var in
               (("árbitro", "ARBITER_SECRET"), ("cliente", "DEMO_CLIENT_SECRET"), ("freelancer", "DEMO_FREELANCER_SECRET"))}
    for name, kp in wallets.items():
        balances = _balances(server, kp.public_key)
        if balances is None or balances.get("XLM", Decimal(0)) < MIN_XLM:
            _friendbot(kp.public_key)
            balances = _balances(server, kp.public_key)
        if name != "árbitro" and usdc_key not in balances:
            _submit(server, kp, lambda b: b.append_change_trust_op(asset=usdc))
            balances = _balances(server, kp.public_key)
        report.append((name, kp.public_key, balances))

    client_kp = wallets["cliente"]
    current = _balances(server, client_kp.public_key).get(usdc_key, Decimal(0))
    if current < TARGET_USDC:
        issuer_kp = Keypair.from_secret(os.environ["USDC_ISSUER_SECRET"])
        top_up = TARGET_USDC - current
        _submit(server, issuer_kp, lambda b: b.append_payment_op(client_kp.public_key, usdc, str(top_up)))
    lines = []
    for name, public, _ in report:
        b = _balances(server, public)
        lines.append(f"{name} {public[:6]}…: {b['XLM']:.0f} XLM" + (f", {b.get(usdc_key, 0):.0f} USDC" if name != "árbitro" else ""))
    return "; ".join(lines)


# --- 4. Imágenes ---------------------------------------------------------------------------

@step("Imágenes Docker listas")
def prepare_images() -> str:
    from backend.blackbox import sut_api_image
    from backend.sandbox import ensure_image, ensure_runner_image
    from backend.web.run import playwright_image
    from backend.web.serve import NODE_IMAGE

    tags = [ensure_runner_image(), sut_api_image(), playwright_image()]
    for image in (get_settings().sandbox_image, NODE_IMAGE):
        ensure_image(image)
        tags.append(image)
    return ", ".join(t.split("/")[-1] for t in tags)


# --- 5. Fixtures ---------------------------------------------------------------------------

DELIVERIES = {
    "1-script-sales-correct.zip": FIXTURES / "sales_en" / "correct",
    "2-landing-broken-on-mobile.zip": FIXTURES / "landing" / "rota_movil",
    "3-landing-hidden-text.zip": FIXTURES / "landing" / "inyeccion_oculta",
    "4-landing-correct.zip": FIXTURES / "landing" / "correcta",
    "5-landing-vite-react.zip": FIXTURES / "landing" / "vite_react",
}

GUIDE = r"""CUMPLE&COBRA — GUION DE LA DEMO
================================

Frontend: http://localhost:3000   ·   API: http://127.0.0.1:8000
Contrato de escrow (testnet): {contract}

Antes de grabar
- Docker Desktop abierto. Si muere con "sailor-ingest.sock ... cannot be accessed": .\scripts\fix_docker.ps1
- Levanta la API y el frontend (dos terminales, desde la carpeta del repo):
    .venv\Scripts\python -m uvicorn backend.main:app --port 8000
    node frontend/node_modules/next/dist/bin/next dev frontend --port 3000
- Abre http://localhost:3000. Las sesiones viejas del navegador se limpian solas.
- Los archivos para los selectores están en demo\deliveries y demo\mockups.

Tiempos de espera (para cortar en la edición)
- Generate draft ≈ 20 s · Review criteria ≈ 30 s · Confirm (script, genera tests ocultos) ≈ 60 s
- Deposit / Accept ≈ 10 s cada uno (testnet) · Verificación script ≈ 35 s · web ≈ 20-25 s

A. Perfil script (≈3 min)
 1. "Continue as demo freelancer", vuelve al inicio, "Continue as demo client".
 2. New task → Samples: "Python function" → Generate draft → Review criteria → apply → Confirm (~1 min).
 3. Approve test plan → Deposit 25 USDC, 7 days.
 4. Cambia al freelancer (selector arriba a la derecha) → Tasks → Accept for 25 USDC.
 5. Deliver → ZIP upload → deliveries/1-script-sales-correct.zip → Submit (~35 s) → approved + pago.
 6. Cambia al cliente → abre el reporte (sin código) → Download verified code.

B. Perfil web (≈4 min), una sola tarea
 1. Como cliente: New task → Samples: "Landing page" → Mockups: mockups/M1-desktop.png y M2-mobile.png
    → Generate draft → Review criteria → Confirm.
    Antes de confirmar, revisa la etiqueta gris de cada criterio: si alguno usa
    element_visible(button, Pedir ahora) quítalo con "remove" ("Pedir ahora" es un link en la entrega).
    Con el pedido de ejemplo actual el modelo usa link_navigates(Pedir ahora, #contacto), que está bien.
 2. Approve test plan → Deposit 25 USDC.
 3. Freelancer: Accept → entrega deliveries/2-landing-broken-on-mobile.zip → rejected (scroll horizontal a 390 px).
 4. Entrega deliveries/3-landing-hidden-text.zip → manual review (texto oculto dirigido al evaluador).
 5. Entrega deliveries/4-landing-correct.zip → approved + pago automático.
 6. Cliente: reporte con capturas 1440/390 px y video de la sesión; links a stellar.expert.
"""


@step("Fixtures de la demo en demo/")
def prepare_fixtures() -> str:
    from backend.ingest.hashing import deterministic_zip

    deliveries = DEMO / "deliveries"
    mockups = DEMO / "mockups"
    if DEMO.exists():
        shutil.rmtree(DEMO)
    deliveries.mkdir(parents=True)
    mockups.mkdir()
    for name, source in DELIVERIES.items():
        (deliveries / name).write_bytes(deterministic_zip(source))
    shutil.copy2(FIXTURES / "landing" / "mockup_desktop.png", mockups / "M1-desktop.png")
    shutil.copy2(FIXTURES / "landing" / "mockup_mobile.png", mockups / "M2-mobile.png")
    (DEMO / "GUION.txt").write_text(GUIDE.format(contract=os.getenv("ESCROW_CONTRACT_ID")), encoding="utf-8")
    return f"{len(DELIVERIES)} ZIPs, 2 mockups y GUION.txt"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep-data", action="store_true")
    parser.add_argument("--skip-images", action="store_true")
    args = parser.parse_args()
    started = time.monotonic()
    print("Cumple&Cobra — demo_reset")
    reset_data(args.keep_data)
    reset_docker()
    check_docker()
    check_meta()
    check_testnet()
    fund_wallets()
    if not args.skip_images:
        prepare_images()
    prepare_fixtures()
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} pasos OK en {time.monotonic() - started:.0f} s")
    if failed:
        print("FALLARON: " + ", ".join(name for name, _, _ in failed))
        return 1
    print(f"Listo para grabar. Guion: {DEMO / 'GUION.txt'}")
    print(json.dumps({"frontend": "http://localhost:3000", "api": "http://127.0.0.1:8000"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
