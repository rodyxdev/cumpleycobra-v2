# Fase 4 — Frontend, escrow y entrega privada (EN CURSO)

Fecha: 2026-10-28/29. Esta bitácora está **incompleta**: la sesión se cortó por límite de uso antes de
terminar el flujo de punta a punta por la UI.

## Hecho y verificado

- **BUILD npm con scripts**: `npm ci` con red y con scripts de instalación (contenedor aislado,
  sin tests). Tiene su propio timeout (`NPM_INSTALL_TIMEOUT_S=600`) y una caché npm compartida
  verificada por `integrity`.
  - Un proyecto **Vite 8 + React 19 real** (`npm create vite`, fixture `landing/vite_react`) se
    **aprobó de punta a punta**: 8/8 criterios.
  - El primer `npm ci` tardó 434 s, probablemente por lentitud transitoria de la red. Después,
    el BUILD tarda 8–9 s.
- **Revisor**: el nuevo `implementation_detail` marcó en vivo como bloqueantes un "debe ser un
  `<button>`" y un "clase CSS + React", y propuso reescrituras observables.
- **Roles y confidencialidad**:
  - Usuarios con token bearer y roles por tarea. Las vistas se construyen por lista blanca
    (`backend/views.py`).
  - El test del canario recorre **todas** las rutas GET con el token del cliente; además hay
    tests con reportes reales.
  - Encontró una fuga real hacia el freelancer: la salida de pytest del lado confiable incluía el
    código de los tests ocultos. Está corregida.
  - Después del pago, el cliente descarga un ZIP determinista cuyo hash es igual al
    `workspace_hash`.
- **Escrow Soroban** (`soroban/contracts/escrow`):
  - Funciones: deposit, accept, release (solo árbitro), client_release, refund (cualquiera, tras
    el plazo). 13 tests de `cargo test` en verde.
  - Desplegado en testnet: **`CBOQPA5WO7TSQ7WQLCRRIEYCXPIPZZQERNI5GNL7NHXPKQLB3VAOCHLW`**,
    token USDC de prueba (SAC) `CAZBUP6Z4A33I7HA57QN5EYNPLT5JISFJUELWHMAUI5GASI54TVGX3IR`.
    Es un activo `USDC` emitido por nuestra cuenta de testnet, **no el USDC de Circle**.
  - Prueba del ciclo en testnet desde el backend:
    - deposit `23922048349c1826636089fdc9d10802ad3da9b3abe457e611c47caa2e8ef7a6`
    - accept `fed81c6d90ee42a3e676c4768587099ba6a86707ef8d3d309420de02588bfb28`
    - release `ff9828c768df6eb2d4cc6469d8a964fa1c21af9c15bf945f7be3460b9f9d64a1`
      (con workspace/verdict hash registrados)
  - Un `client_release` firmado por el freelancer fue rechazado on-chain
    (`59631218ac53082ed95da6ee90b939a9349e5941dc113d6f04fe148af71b71ca`).
- **GitHub App (implementación C)**:
  - JWT RS256, token de instalación limitado a un repo con `contents: read`, entregado a git por
    variables `GIT_CONFIG_*` (nunca en argumentos) y enmascarado en los errores.
  - Tiene tests con la API simulada. **No probado en vivo**: falta crear e instalar la App (ver
    abajo). El ZIP sigue como flujo principal.
- **Frontend Next.js 16** (`frontend/`, UI en inglés):
  - inicio con cuentas demo;
  - lista de tareas;
  - creación (pedido → borrador → revisión con "apply" → confirmar);
  - tarea (plan, aprobar, depositar, aceptar, entregar por GitHub o ZIP, liberación manual,
    pago con links a stellar.expert, descarga del ZIP);
  - reporte (criterios, tests, capturas y video con autenticación, hashes).

  TypeScript y ESLint limpios. Probado en el navegador hasta generar el borrador de un pedido real
  con Muse Spark.

## Pendiente

- **Flujo E2E completo por la UI con hashes reales de testnet**: falta revisar, confirmar,
  aprobar, depositar, aceptar, entregar (`backend/fixtures/sales_en/correct/sales.py`) y ver el
  veredicto y el pago.
- Correr la suite completa de pytest con los últimos cambios del frontend (el backend pasó 300
  tests antes de empezar el frontend).
- Crear la GitHub App, con permiso Contents: read-only, e instalarla en un repo privado. Luego
  configurar `GITHUB_APP_ID` y `GITHUB_APP_PRIVATE_KEY_PATH` en `.env`.
- Firma con billetera real (Freighter). Hoy firman las billeteras demo de testnet desde el backend.
- Opción de idioma español en la UI.

## Cómo correrlo

```powershell
.venv\Scripts\python -m uvicorn backend.main:app --port 8000
node frontend/node_modules/next/dist/bin/next dev frontend --port 3000
```

En Windows, el `&` en el nombre de la carpeta rompe los atajos de `node_modules/.bin`: hay que
invocar las herramientas con `node <ruta>`.
