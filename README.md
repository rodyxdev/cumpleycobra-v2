# Cumple&Cobra v2

Plataforma donde un cliente define criterios de aceptación, el freelancer entrega código
y un sistema de agentes con modelos de Meta (Muse Spark) verifica la entrega con evidencia
ejecutable antes de liberar un pago en escrow.

Estado: **fase 4** (frontend, roles, escrow en Stellar, entrega privada).
Bitácoras: [fase 1](docs/fases/fase-1.md) · [fase 2](docs/fases/fase-2.md) · [fase 3](docs/fases/fase-3.md) · [fase 4](docs/fases/fase-4.md).

**Escrow en Stellar testnet:** [`CBOQPA5WO7TSQ7WQLCRRIEYCXPIPZZQERNI5GNL7NHXPKQLB3VAOCHLW`](https://stellar.expert/explorer/testnet/contract/CBOQPA5WO7TSQ7WQLCRRIEYCXPIPZZQERNI5GNL7NHXPKQLB3VAOCHLW)
(token: USDC de prueba, SAC `CAZBUP6Z4A33I7HA57QN5EYNPLT5JISFJUELWHMAUI5GASI54TVGX3IR`).

```
backend/
  config.py          configuración por variables de entorno (.env)
  canonical.py       JSON canónico (NFC, claves ordenadas, sin floats) → rules_hash / verdict_hash
  llm.py             cliente Muse Spark: json_schema estricto + Pydantic + reintentos + medición de uso
  contract/          borrador y revisión del contrato con Muse Spark, chequeos deterministas
  ingest/            entrega → Workspace (ruta + hash): GitHub+SHA, ZIP, repo privado vía GitHub App
  sandbox/           Docker: BUILD con red → volumen /deps → TEST sin red; /out tmpfs; redes internas
  blackbox/          caja negra: SUT no confiable (relay/adaptador o uvicorn) ↔ tests en contenedor confiable
  verification/      suite oculta, higiene, estático, juez, regla final determinista
  web/               perfil web: build npm, servido interno, Playwright, texto oculto, juez visual
  tasks.py           almacenamiento de tareas (suite oculta y evidencia fuera de los workspaces)
  workflow.py        crear tarea = congelar contrato + preparar la suite
  main.py            API FastAPI
  auth.py / views.py usuarios con token, roles por tarea y vistas del reporte por rol (lista blanca)
  escrow.py          cliente del contrato Soroban (deposit/accept/release/client_release/refund)
  demo.py            demo script/API (fixtures/ventas)
  demo_web.py        demo web (fixtures/landing)
frontend/            Next.js 16 (UI en inglés): pedido → contrato → depósito → entrega → reporte → pago
soroban/             contrato de escrow en Rust (soroban-sdk 27) y sus tests
docs/fases/          bitácora por fase
```

## Preparar la demo

`.\scripts\demo_reset.ps1` limpia los datos locales, verifica Docker, Meta y testnet, fondea las billeteras demo, construye las imágenes y deja en `demo/` los ZIPs, los mockups y el guion (`demo/GUION.txt`). Se puede correr varias veces seguidas. Si Docker Desktop no arranca por sockets huérfanos (`sailor-ingest.sock ... cannot be accessed`), corre `.\scripts\fix_docker.ps1`. Si Docker Desktop no arranca por sockets huérfanos (`sailor-ingest.sock ... cannot be accessed`), corre `.\scriptsix_docker.ps1`.

## Puesta en marcha (Windows, PowerShell)

Requisitos: Python 3.12+, git, Docker Desktop corriendo (las imágenes de tests, SUT y
Playwright se construyen solas la primera vez; la de Playwright pesa ~3.5 GB).

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install -r backend\requirements.txt
copy .env.example .env   # y completa MODEL_API_KEY
.venv\Scripts\python -m pytest
.venv\Scripts\python -m backend.demo --defect-experiment
.venv\Scripts\python -m backend.demo_web --rounds 1 --visual-repeats 2
.venv\Scripts\python -m uvicorn backend.main:app --port 8000
cd frontend; npm install; cd ..
node frontend/node_modules/next/dist/bin/next dev frontend --port 3000   # http://localhost:3000
cd soroban; cargo test; stellar contract build
```

En Windows, el `&` de la ruta rompe los atajos de `node_modules/.bin`: invoca las herramientas con `node <ruta>`.
El `.env` necesita además `ESCROW_CONTRACT_ID`, `ARBITER_SECRET`, `ARBITER_TOKEN` y (demo) `DEMO_CLIENT_SECRET` /
`DEMO_FREELANCER_SECRET` de cuentas de testnet; para repos privados, `GITHUB_APP_ID` y `GITHUB_APP_PRIVATE_KEY_PATH`.

## API

Todas las rutas (salvo `/health`, `/config` y `POST /users`) requieren `Authorization: Bearer <token>`.

| Método | Ruta | Qué hace |
|---|---|---|
| POST | `/users` | crea un usuario y devuelve su token (demo) |
| POST | `/me/demo-wallet` | asocia una billetera de prueba de testnet (cliente o freelancer) |
| POST | `/tasks/{id}/escrow/deposit` | el cliente deposita USDC en el escrow |
| POST | `/tasks/{id}/accept` | el freelancer acepta (on-chain) |
| POST | `/tasks/{id}/escrow/client-release` | el cliente libera a mano (revisión manual) |
| POST | `/tasks/{id}/escrow/refund` | reembolso tras el plazo |
| GET | `/tasks/{id}/escrow` | estado registrado y on-chain |
| GET | `/tasks/{id}/submissions[/{sid}]` | reportes filtrados por rol |
| GET | `/tasks/{id}/submissions/{sid}/source.zip` | código verificado (cliente: solo tras el pago) |
| POST | `/mockups` | sube un mockup (PNG/JPEG) y devuelve su sha256 |
| POST | `/tasks/draft` | pedido en texto (+ mockups) → borrador de contrato (Muse Spark) |
| POST | `/tasks/draft/review` | marca criterios vagos o no verificables, con sugerencia |
| POST | `/tasks` | confirma el contrato: `rules_hash` + suite oculta (script/api) o chequeos web |
| GET | `/tasks/{id}` | contrato + plan de pruebas (nunca el código de los tests) |
| POST | `/tasks/{id}/plan/approve` | el cliente aprueba el plan; la tarea acepta entregas |
| POST | `/tasks/{id}/submissions` | `repo`+`sha` o `zip` → veredicto por capas con `verdict_hash` |
| GET | `/tasks/{id}/submissions/{sid}/evidence/{archivo}` | capturas (S*.png), videos y results.json (web) |
| POST | `/submissions` | (fase 1) ingesta + un comando en el sandbox |

Migrar suites de la fase 2 al formato caja negra:
`python -m backend.blackbox.migrate --reference backend/fixtures/ventas/correcta [task_id ...]`
