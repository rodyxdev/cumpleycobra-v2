# Cumple&Cobra v2

Plataforma donde un cliente define criterios de aceptación, el freelancer entrega código
y un sistema de agentes con modelos de Meta (Muse Spark) verifica la entrega con evidencia
ejecutable antes de liberar un pago en escrow.

Estado: **fase 1** (núcleo de ejecución). Detalle en [docs/fases/fase-1.md](docs/fases/fase-1.md).

```
backend/
  config.py        configuración por variables de entorno (.env)
  llm.py           cliente Muse Spark: salida estructurada + validación Pydantic + reintentos
  ingest/          entrega → Workspace (ruta + hash determinista): GitHub+SHA, ZIP, (GitHub App: pendiente)
  sandbox/         ejecución aislada en Docker (sin red, límites, no root, workspace solo lectura)
  main.py          API FastAPI: POST /submissions
  tests/
docs/fases/        bitácora por fase
```

## Puesta en marcha (Windows, PowerShell)

Requisitos: Python 3.12+, git, Docker Desktop corriendo.

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install -r backend\requirements.txt
copy .env.example .env   # y completa MODEL_API_KEY
.venv\Scripts\python -m pytest
.venv\Scripts\python -m uvicorn backend.main:app --reload
```

Ejemplos:

```powershell
curl.exe -F repo=pallets/itsdangerous -F sha=096c8d42545d3b68ea21a4f890fb2b2d8979c0bd -F "command=ls" http://127.0.0.1:8000/submissions
curl.exe -F zip=@entrega.zip -F "command=python -m pytest -q" http://127.0.0.1:8000/submissions
```
