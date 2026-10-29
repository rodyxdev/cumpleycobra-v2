# Cumple&Cobra v2

Plataforma donde un cliente define criterios de aceptación, el freelancer entrega código
y un sistema de agentes con modelos de Meta (Muse Spark) verifica la entrega con evidencia
ejecutable antes de liberar un pago en escrow.

Estado: **fase 2** (contrato + verificación del perfil script/API).
Bitácoras: [fase 1](docs/fases/fase-1.md) · [fase 2](docs/fases/fase-2.md).

```
backend/
  config.py          configuración por variables de entorno (.env)
  canonical.py       JSON canónico (NFC, claves ordenadas, sin floats) → rules_hash / verdict_hash
  llm.py             cliente Muse Spark: json_schema estricto + Pydantic + reintentos + medición de uso
  contract/          borrador y revisión del contrato con Muse Spark, chequeos deterministas
  ingest/            entrega → Workspace (ruta + hash): GitHub+SHA, ZIP, (GitHub App: pendiente)
  sandbox/           Docker: BUILD con red → volumen /deps → TEST sin red; /out tmpfs
  verification/      suite oculta, higiene, estático, juez, regla final determinista
  tasks.py           almacenamiento de tareas (la suite oculta vive fuera de los workspaces)
  workflow.py        crear tarea = congelar contrato + generar y validar la suite
  main.py            API FastAPI
  demo.py            demo de punta a punta con las entregas de fixtures/ventas
  fixtures/ventas/   pedido, contrato confirmado y 5 entregas (correcta, incompleta, tramposas)
docs/fases/          bitácora por fase
```

## Puesta en marcha (Windows, PowerShell)

Requisitos: Python 3.12+, git, Docker Desktop corriendo.

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install -r backend\requirements.txt
copy .env.example .env   # y completa MODEL_API_KEY
.venv\Scripts\python -m pytest
.venv\Scripts\python -m backend.demo --defect-experiment
.venv\Scripts\python -m uvicorn backend.main:app --reload
```

## API

| Método | Ruta | Qué hace |
|---|---|---|
| POST | `/tasks/draft` | pedido en texto → borrador de contrato (Muse Spark) |
| POST | `/tasks/draft/review` | marca criterios vagos o no verificables, con sugerencia |
| POST | `/tasks` | confirma el contrato: `rules_hash` + suite oculta generada y validada |
| GET | `/tasks/{id}` | contrato + plan de pruebas (nunca el código de los tests) |
| POST | `/tasks/{id}/plan/approve` | el cliente aprueba el plan; la tarea acepta entregas |
| POST | `/tasks/{id}/submissions` | `repo`+`sha` o `zip` → veredicto por capas con `verdict_hash` |
| POST | `/submissions` | (fase 1) ingesta + un comando en el sandbox |
