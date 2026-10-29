# Cumple&Cobra v2

Plataforma donde un cliente define criterios de aceptación, el freelancer entrega código
y un sistema de agentes con modelos de Meta (Muse Spark) verifica la entrega con evidencia
ejecutable antes de liberar un pago en escrow.

Estado: **fase 3** (verificación caja negra + perfil web).
Bitácoras: [fase 1](docs/fases/fase-1.md) · [fase 2](docs/fases/fase-2.md) · [fase 3](docs/fases/fase-3.md).

```
backend/
  config.py          configuración por variables de entorno (.env)
  canonical.py       JSON canónico (NFC, claves ordenadas, sin floats) → rules_hash / verdict_hash
  llm.py             cliente Muse Spark: json_schema estricto + Pydantic + reintentos + medición de uso
  contract/          borrador y revisión del contrato con Muse Spark, chequeos deterministas
  ingest/            entrega → Workspace (ruta + hash): GitHub+SHA, ZIP, (GitHub App: pendiente)
  sandbox/           Docker: BUILD con red → volumen /deps → TEST sin red; /out tmpfs; redes internas
  blackbox/          caja negra: SUT no confiable (relay/adaptador o uvicorn) ↔ tests en contenedor confiable
  verification/      suite oculta, higiene, estático, juez, regla final determinista
  web/               perfil web: build npm, servido interno, Playwright, texto oculto, juez visual
  tasks.py           almacenamiento de tareas (suite oculta y evidencia fuera de los workspaces)
  workflow.py        crear tarea = congelar contrato + preparar la suite
  main.py            API FastAPI
  demo.py            demo script/API (fixtures/ventas)
  demo_web.py        demo web (fixtures/landing)
docs/fases/          bitácora por fase
```

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
.venv\Scripts\python -m uvicorn backend.main:app --reload
```

## API

| Método | Ruta | Qué hace |
|---|---|---|
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
