# Fase 4 — Frontend, escrow y entrega privada

Fecha: 2026-10-28/29 · Modelo: `muse-spark-1.3` · Stellar testnet · Soroban SDK 27 / Stellar CLI 28 ·
Next.js 16.4 · Python 3.14 (host).

## Resultado en una línea

Un pedido en texto recorrió **el flujo completo desde la UI en inglés**, con Muse Spark, Docker y
testnet reales:

- **borrador → revisión → contrato congelado → depósito de 25 USDC → aceptación → entrega →
  50/50 tests ocultos → pago liberado automáticamente**;
- los tres hashes (`rules_hash`, `workspace_hash`, `verdict_hash`) quedaron registrados on-chain y
  coinciden con los del backend;
- el cliente descargó un ZIP **idéntico byte a byte** a lo entregado.

## Flujo de punta a punta (hashes reales de testnet)

Contrato de escrow: [`CBOQPA5WO7TSQ7WQLCRRIEYCXPIPZZQERNI5GNL7NHXPKQLB3VAOCHLW`](https://stellar.expert/explorer/testnet/contract/CBOQPA5WO7TSQ7WQLCRRIEYCXPIPZZQERNI5GNL7NHXPKQLB3VAOCHLW)
· Token: USDC de prueba (SAC `CAZBUP6Z4A33I7HA57QN5EYNPLT5JISFJUELWHMAUI5GASI54TVGX3IR`, emitido por
`GDJBRQ7S…V56I6`; **no** es el USDC de Circle).

Tarea `8961a638f51e4c828475dbdee84c68e4` (pedido de ejemplo "Python function" de la UI, en inglés):

| Paso | Quién | Resultado |
|---|---|---|
| 1. Pedido → borrador | cliente (UI) | Muse Spark propuso 12 criterios: 9 test y 3 static |
| 2. Revisión | cliente (UI) | 2 ambigüedades reales (C3: comentarios con sangría; C4: split simple vs CSV con comillas). El cliente las aplicó y editó el texto |
| 3. Confirmar | cliente | `rules_hash = sha256:72b9b1b684a0a1b0a57b3807ce0f57a68ec1fd14698938593e62799c93ce595d`; suite oculta de 9 archivos aceptada al 1.er intento, `suite_hash = sha256:c4cdab8bdcdfb648ecead6acb245f3cd2365b0e1da81544aa9fe742c67870c4f` |
| 4. Aprobar plan y depositar 25 USDC (7 días) | cliente | [deposit `7b07689c0906e523d72a260859ba296007c2f0c4add29dbecdf6adbd3d90ab37`](https://stellar.expert/explorer/testnet/tx/7b07689c0906e523d72a260859ba296007c2f0c4add29dbecdf6adbd3d90ab37) (ledger 5445849) |
| 5. Aceptar | freelancer (UI) | [accept `ca7b32d2315c011163f6412811eb82ee307a5b003142a13468759b30f8ec1e2b`](https://stellar.expert/explorer/testnet/tx/ca7b32d2315c011163f6412811eb82ee307a5b003142a13468759b30f8ec1e2b) (ledger 5445859) |
| 6. Entregar ZIP (`backend/fixtures/sales_en/correct`) | freelancer (UI) | submission `9a23366e0d914d66922724aee0e5cc2e`: **approved**, 50/50 tests ocultos, 3/3 estáticos; en 33.4 s (tests en caja negra 15.0 s, juez 18.3 s) |
| 7. Pago automático | árbitro (backend) | [release `5ab9170412097677e4faff5fbbaed62b4dd5cd1c0ee3046ce9fc8af6e3ada0a2`](https://stellar.expert/explorer/testnet/tx/5ab9170412097677e4faff5fbbaed62b4dd5cd1c0ee3046ce9fc8af6e3ada0a2) (ledger 5445877) con `workspace_hash = sha256:0e5d3686d5837bdd54d28eb3e02db50432e613f9cc375ed94d599f0611bde480` y `verdict_hash = sha256:1a8418d8885c0894037dab1ec1de34fddb6f9da0580b97c610523f22f2689d68` |
| 8. Comprobación on-chain | — | `get_escrow`: status `released`, monto 25, freelancer `GBAB4WDL…X3DA`. **rules, workspace y verdict hash iguales** a los del backend |
| 9. Descarga del código | cliente | `source.zip` (673 bytes), con `X-Workspace-Hash` igual al hash on-chain y sha256 `9db0cf3d…6bba` **idéntico** al ZIP entregado |

Costo de este flujo (precio de agregadores, no verificado con Meta): crear la tarea (9 generaciones de
tests) ~USD 0.18 y ~60 s; verificar la entrega ~USD 0.04 y 33 s. Las 3 transacciones de la cadena
tardaron 5–10 s cada una.

## Qué se hizo

### 0. Pendientes de la fase 3
- **Scripts de npm en el BUILD**: `npm ci` con red y **con** scripts de instalación. Es seguro
  porque el contenedor solo tiene el código del freelancer y nunca monta tests ni secretos. Se
  mantienen nobody, el límite de CPU/memoria, un timeout propio (`NPM_INSTALL_TIMEOUT_S=600`) y el
  build sin red.
  - Hay una caché npm compartida en un volumen. Es segura porque npm verifica el `integrity` del
    lockfile al leerla.
  - Probé un proyecto **Vite 8 + React 19 real** (generado con `npm create vite`, fixture
    `landing/vite_react`): **aprobado** con 8/8 criterios, `vite build` en 0.8 s sin red.
  - La primera instalación tardó 434 s, probablemente por lentitud transitoria de la red de Docker
    Desktop: después, incluso con la caché vacía, tardó 2.5 s. El BUILD completo queda en 8–9 s.
- **Revisor**: nuevo tipo `implementation_detail`. En vivo marcó como bloqueantes "debe ser un
  `<button>`" (con `element_visible ["button", …]`) y "clase CSS `.contact-form` + React", y propuso
  reescrituras observables. Los criterios legítimos no los tocó.

### 1. Roles y confidencialidad
- Usuarios con token bearer (en disco solo su sha256) y árbitro con `ARBITER_TOKEN`. Roles **por
  tarea**: el cliente es quien la crea y el freelancer quien la acepta. Un usuario ajeno recibe
  404, para no revelar que la tarea existe.
- Las vistas (`backend/views.py`) se construyen **por lista blanca**:
  - **Cliente**: veredicto por criterio, nombres y resultado de los tests, capturas, video,
    hashes y pago. No recibe citas, razonamiento del juez de código, fragmentos de señales, logs,
    `results.json`, mensajes de fallo ni líneas de higiene o estáticas. Los motivos se regeneran
    en inglés desde datos estructurados.
  - **Freelancer**: su reporte completo, salvo el contenido de los tests ocultos.
  - **Árbitro**: todo.
- **Tests de confidencialidad** (`test_roles.py`):
  - Un "canario" en el código y en cada campo sensible de un reporte; se recorren **todas las
    rutas GET** de la app con el token del cliente y el canario no aparece nunca.
  - Lo mismo con **dos reportes reales** de la fase 3 (script adversarial y web con inyección).
  - **Fuga encontrada y corregida**: la salida de pytest del contenedor confiable
    (`test_run.stdout_tail`) llevaba tracebacks con código de los tests ocultos y le llegaba al
    freelancer.
- **Después del pago**: el cliente descarga un ZIP determinista de la entrega verificada. Antes de
  servirlo, el backend lo descomprime y comprueba que su hash es el `workspace_hash` registrado.
  Antes del pago, el cliente recibe 403.

### 2. Escrow Soroban (`soroban/contracts/escrow`)
- Escrito de cero:
  - `__constructor(arbiter, token)`;
  - `deposit(client, task_id, amount, deadline, rules_hash)` → Funded;
  - `accept(freelancer, task_id)` → Accepted;
  - `release(task_id, workspace_hash, verdict_hash)`, solo el árbitro → Released;
  - `client_release(task_id)`, solo el cliente → Released;
  - `refund(task_id)`, cualquiera después del plazo → Refunded;
  - `get_escrow` y `get_config`.

  Tiene eventos tipados (`#[contractevent]`) y errores explícitos.
- **13 tests** (`cargo test`), entre ellos firmas, plazos, doble pago, reembolso sin firma y
  tareas independientes.
- El contrato no puede saber si un veredicto aprobó: lo decide el árbitro y lo deja registrado con
  los dos hashes. El backend solo llama a `release` con un veredicto `approved` (hay test). Un
  `client_release` firmado por el freelancer fue **rechazado on-chain** en testnet.
- `task_id` on-chain = `sha256(task_id)`; los hashes `sha256:…` viajan como `BytesN<32>`.

### 3. Entrega privada con GitHub App (implementación C)
- JWT RS256 (`iss` = App ID, 9 min) → token de instalación **limitado a un repo y a
  `contents: read`** → mismo `fetch` por SHA del flujo público.
- El token va a git por variables `GIT_CONFIG_*` (cabecera `extraHeader`): **nunca** en la URL ni en
  argumentos, y se enmascara en los errores. Hay tests con la API de GitHub y git simulados.
- **No probado en vivo**: hace falta crear la App en GitHub (permiso *Contents: read-only*),
  instalarla en un repo privado y configurar `GITHUB_APP_ID` + `GITHUB_APP_PRIVATE_KEY_PATH`. El ZIP
  sigue como flujo principal; la UI ya acepta `installation_id`.

### 4. Frontend (`frontend/`, Next.js 16, UI en inglés)
- **Inicio**: cuentas demo con billeteras de testnet ya fondeadas; selector de sesión para pasar
  de cliente a freelancer en el mismo navegador.
- **New task**: pedido (con ejemplos) → mockups opcionales → *Generate draft* → criterios
  editables con los problemas de la revisión y botón *apply* → *Confirm contract*.
- **Task**: criterios con su plan de pruebas (nunca el código de los tests). Acciones por rol:
  aprobar el plan y depositar, aceptar, entregar (ZIP o repo + SHA + installation id opcional),
  liberación manual cuando hay revisión manual, y descarga del código tras el pago. Panel de pago
  con links a stellar.expert.
- **Report**: estado y etapa, criterios con tests, capturas y video (descargados con el token),
  pago y hashes.
- `cacheComponents` desactivado, porque la app renderiza en el cliente. TypeScript y ESLint limpios.

## Bugs encontrados en la fase (corregidos)
- **Fuga del código de los tests ocultos al freelancer** por la salida de pytest (la detectó el test
  del canario).
- `npm ci` de un proyecto real superaba el timeout del BUILD (180 s): ahora tiene su propio timeout
  y una caché.
- La revisión respondía en español a pedidos en inglés y sus sugerencias traían instrucciones
  ("Reescribir C3 como: «…»") en lugar del criterio listo para reemplazar. Se corrigió el prompt y
  se verificó en vivo: ahora responde en el idioma del borrador con el texto limpio.
- Los errores de la API llegaban en español a la UI en inglés: traducidos.
- En el navegador quedaron dos sesiones "Demo client" y el selector eligió la equivocada. El 404 fue
  correcto (no era parte de la tarea), pero confundía: ahora el selector muestra el id corto.
- Faltaban la fecha en la vista del cliente y el conteo de tests aparecía duplicado.
- En Windows, el `&` del nombre de la carpeta rompe los atajos de `node_modules/.bin` (cmd.exe lo
  trata como separador de comandos): las herramientas se invocan con `node <ruta>`.

## Salida de los tests

`pytest` (backend, con Docker y red): **300 passed in 103.85s**. `cargo test` (contrato): **13 passed**.
Frontend: `tsc --noEmit` y `eslint` sin errores.

Archivos de test nuevos en la fase:
- `test_roles.py`: canario contra todas las rutas, reportes reales, pago y descarga;
- `test_github_app.py`;
- `test_api_tasks.py`: reescrito con roles y un escrow falso.

## Pendiente
- **GitHub App en vivo** (ver arriba).
- **Firma con billetera real** (Freighter). Hoy el backend firma con billeteras demo de testnet;
  `escrow.py` ya tiene `prepare()`/`submit()` para XDR, pero no hay endpoints ni UI para eso.
- **USDC de Circle**: el contrato acepta cualquier SAC en el constructor. Pasar al USDC de Circle
  en testnet es un redespliegue con su dirección, más fondos del faucet de Circle (web).
- **Idioma español** en la UI (no hubo tiempo). Algunos textos internos del backend siguen en
  español: validaciones deterministas del contrato y errores de ingesta.
- Persistencia en JSON sin concurrencia; los endpoints de crear tarea (~60 s) y verificar (~35 s)
  son síncronos y hace falta una cola.
- `released_submission_id` en `client_release` es la última entrega verificada; con varias entregas
  en revisión manual, el cliente debería elegir cuál paga.

## Lo que no pude verificar
- La GitHub App con GitHub real.
- `refund` en testnet: necesita que pase el plazo (está probado en `cargo test` moviendo el reloj
  del ledger).
- El flujo web completo desde la UI con depósito: el perfil web se verificó con
  `backend.demo_web`, pero la UI de punta a punta se recorrió con el perfil script.
- El precio oficial de Muse Spark.

## 4.5 — Demo listo para grabar

Fecha: 2026-10-29. Sin funcionalidades nuevas: el perfil web se probó de punta a punta desde la UI con
depósito en testnet, se corrigió lo que falló y se agregó el script de preparación.

### Perfil web desde la UI, con depósito en testnet

Una sola tarea, que cuenta la historia natural de la demo: el freelancer entrega una versión rota, luego
una tramposa y al final la correcta, que es la que cobra. Se usó un solo depósito; tres tareas habrían
necesitado tres depósitos sin aportar nada.

Tarea `bc2dbbe4c3fe47179822233ac68795e7` (ejemplo "Landing page" de la UI + mockups M1/M2 subidos desde
la UI). Contrato con 12 chequeos de navegador y 2 visuales:
`rules_hash = sha256:1459d19e6899fb1b14dd463429921351f673dabec8f976c2ccffcb5f57cac7f0`,
`suite_hash = sha256:761c5d474582950e627ebc339a6e6334693ef8e9fd3aa777d7b095d90266ede6`.

| Paso | Resultado |
|---|---|
| Depósito de 25 USDC | [`f6e548566ff23d797d54525ec92ccb632cfe3c375a6287041ecc10245fd8acb7`](https://stellar.expert/explorer/testnet/tx/f6e548566ff23d797d54525ec92ccb632cfe3c375a6287041ecc10245fd8acb7) |
| Aceptación | [`2c13736ed5f954f5bce7bfd15e4b7e3dcd2093d80b95e0b9cfae9fe03c0ae112`](https://stellar.expert/explorer/testnet/tx/2c13736ed5f954f5bce7bfd15e4b7e3dcd2093d80b95e0b9cfae9fe03c0ae112) |
| Entrega 1: landing rota en móvil | **rejected** en 23 s. El cliente ve por qué: C7–C10 `mobile: within width=False`, C12 `scrollWidth=1101` en un viewport de 390 px, C14 (juez visual) "no apila las tarjetas, desborda", y la captura móvil mide 1101 px de ancho |
| Entrega 2: landing con texto oculto | **manual review** en 21 s: los 14 criterios pasan, pero hay texto oculto dirigido al evaluador (contraste 1:1, `font-size:0`, fuera de pantalla). El cliente ve la señal genérica, sin el texto |
| Entrega 3: landing correcta | **approved** en 20 s y pago automático: [release `e06b11f91a6acc2fc8642c0fca358071be26a17f150482d4d9aaa8f226caa97a`](https://stellar.expert/explorer/testnet/tx/e06b11f91a6acc2fc8642c0fca358071be26a17f150482d4d9aaa8f226caa97a) con `workspace_hash = sha256:71b7d73a6cd9b36e24fdda6a0bfa8273b1f37de5b163fd48321cfa244f733a98` y `verdict_hash = sha256:63d8c688642c069ae98614495e4464506036b92f0088fd9f40d191969d3ff686` |
| Comprobación on-chain | `get_escrow`: `released`, 25 USDC. **rules, workspace y verdict hash iguales** a los del backend; el `verdict_hash` incluye los sha256 de S1–S3, los 2 videos y `results.json` |
| Vista del cliente | Las 3 capturas se cargan (1440 px, 390 px y después de enviar el formulario) y los 2 videos se reproducen (~9.5 s). Descarga el ZIP de la entrega **pagada** (200, `X-Workspace-Hash` correcto); el de la entrega no pagada da **403** |

### Fallos de la UI encontrados y corregidos
- **Contrato en español para un pedido en inglés**: el modelo se guiaba por los textos citados
  ("Verduras orgánicas a tu puerta"). Ahora el borrador usa el idioma de la *prosa* del cliente y
  copia tal cual los textos citados. Verificado en vivo: el contrato salió en inglés.
- **Jueces y revisión en español** con contratos en inglés: el backend detecta el idioma del contrato
  (`contract_language`) y lo pasa explícito a los dos jueces. Verificado: el razonamiento visual y la
  revisión salieron en inglés.
- **"0/1 hidden tests passed" en criterios web que pasaban**: la UI contaba tests ocultos, que el perfil
  web no tiene. Ahora muestra "Browser check: …" con el resultado del navegador.
- **Detalles del runner de Playwright en español** ("dentro_del_ancho", "tras enviar el formulario"):
  pasados a inglés. La primera entrega de la tarea de arriba se verificó antes del cambio y su reporte
  todavía los muestra en español.
- **Señales del freelancer en español y repetidas** (9 líneas para 4 hallazgos): traducidas y agrupadas,
  con ubicaciones legibles ("/ (desktop)").
- **Motivos del freelancer en español**: ahora usa los mismos motivos en inglés que el cliente; los
  originales quedan en `reasons_detail`.
- **Detalle web duplicado** en el reporte: el resumen solo aparece cuando agrega información.
- **"apply" cambiaba el texto pero no el chequeo**: la revisión sugirió `link_navigates` donde había
  `element_visible`, y "apply" solo reescribía el texto. Sin agregar un editor de chequeos, la UI ahora
  advierte que "apply" cambia solo el texto, muestra qué chequeo se mantiene y sugiere quitar el criterio
  o regenerar el borrador. *Comprobado solo con TypeScript y ESLint: el servidor de desarrollo no
  recompiló la página durante la prueba en vivo.*
- **Sesiones vencidas tras un reset**: tokens que ya no existían dejaban un usuario fantasma y errores
  "Invalid token". Ahora un 401 elimina esa sesión y la UI invita a iniciar sesión (verificado en vivo
  después de `demo_reset`).

### `scripts/demo_reset`

```powershell
.\scripts\demo_reset.ps1        # o: .venv\Scripts\python scripts\demo_reset.py [--keep-data] [--skip-images]
```

1. Borra tareas, usuarios, mockups y temporales de `.data/`, y los contenedores, volúmenes y redes `cc-*`
   que hayan quedado (no toca la caché npm ni las imágenes).
2. Verifica Docker, la API de Meta (que `MUSE_MODEL` exista) y testnet: RPC sano y `get_config` del
   escrow con el mismo árbitro y token que el `.env`.
3. Fondea las billeteras demo: XLM por friendbot si bajan de 100, trustline de USDC y recarga del cliente
   hasta 1000 USDC de prueba. Para eso necesita `USDC_ISSUER_SECRET` en `.env`.
4. Construye de antemano las imágenes de runner, SUT y Playwright.
5. Deja en `demo/` (fuera de git) 5 ZIPs de entregas, los 2 mockups y `GUION.txt` con los pasos.

Corrido **dos veces seguidas**: 8/8 pasos OK (16 s y 9 s); la segunda vez no hubo nada que borrar ni
recargar. Termina con código 1 si falla algún chequeo.

### Antes de grabar
1. `.\scripts\demo_reset.ps1`
2. Levantar la API y el frontend (README) y abrir http://localhost:3000. Las sesiones viejas del
   navegador se limpian solas.
3. Seguir `demo/GUION.txt`. En el navegador, los ZIPs y mockups se eligen desde `demo/`.

### Tests y lint
- `pytest`: **304 passed in 101.25s** (4 tests nuevos en `test_demo_ready.py`: idioma del contrato, señales en inglés y agrupadas, fixtures de `demo_reset` con el mismo hash que las originales, borrado idempotente).
- `cargo test`: **13 passed**. `ruff` (backend y scripts), `tsc --noEmit` y `eslint` (frontend): sin errores.
