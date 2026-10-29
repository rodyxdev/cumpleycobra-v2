# Fase 1 — Núcleo de ejecución

Fecha: 2026-10-28 · Entorno: Windows 11, Python 3.14.5, Docker Desktop 4.88.1 (engine 29.7.2, backend WSL2), git 2.52.

Objetivo: recibir una entrega, ejecutarla de forma aislada y tener lista una llamada
estructurada a Muse Spark. Sin frontend, escrow ni verificación visual.

## Qué se hizo

| Pieza | Archivo(s) | Estado |
|---|---|---|
| Estructura del monorepo | `backend/`, `docs/fases/`, `.env.example`, `README.md`, `.gitignore`, `pytest.ini` | hecho |
| Configuración por env | `backend/config.py` | hecho |
| Cliente LLM con salida estructurada | `backend/llm.py` | hecho, probado contra la API real |
| Ingesta: interfaz común + hash | `backend/ingest/__init__.py`, `base.py`, `hashing.py` | hecho |
| Ingesta A: GitHub público + SHA | `backend/ingest/github_public.py` | hecho, probado contra github.com |
| Ingesta B: ZIP | `backend/ingest/zip_upload.py` | hecho |
| Ingesta C: GitHub App (repo privado) | `backend/ingest/github_app.py` | solo interfaz (`NotImplementedError` → HTTP 501) |
| Sandbox Docker | `backend/sandbox/runner.py` | hecho, probado con Docker real |
| `POST /submissions` | `backend/main.py` | hecho, probado con uvicorn + curl |
| Tests | `backend/tests/` (65 tests) | 65/65 en verde |

### Modelos disponibles (verificado con `GET https://api.meta.ai/v1/models`)

```
muse-spark-1.3   muse-spark-1.3-contributor   muse-spark-1.2   muse-spark-1.2-contributor
muse-spark-1.1   muse-wonder-1.0   muse-image-1.0   muse-voice-transcribe-1.0   sam-3.1
```

`MUSE_MODEL` por defecto: `muse-spark-1.3`. Probado en vivo con `chat.completions.create`:

- `response_format={"type":"json_schema", "json_schema":{..., "strict": true}}` → **funciona**;
  devuelve JSON que cumple el esquema, incluso con objetos anidados vía `$defs` y `enum`.
- `response_format={"type":"json_object"}` también responde, pero sin respetar un esquema
  (devolvió `"verdict": "PASS ✅"`). Por eso se usa `json_schema` estricto.
- Es un modelo con razonamiento: ~400 `reasoning_tokens` incluso en una pregunta trivial,
  con una latencia de 3–6 s por llamada.
- No se envían `temperature` ni otros parámetros: no se probaron, así que no se asumen.

## Decisiones y por qué

**LLM**
- SDK de OpenAI con `base_url` configurable y `max_retries=0`: los reintentos son nuestros
  para controlar el backoff (exponencial con *jitter* completo, tope de 30 s), respetar
  `Retry-After` y decidir qué se reintenta: 408/409/429/5xx, timeout y error de conexión.
  400/401 fallan de inmediato.
- El esquema sale del propio modelo Pydantic (`strict_json_schema`): se fuerzan
  `additionalProperties: false` y `required` con todos los campos en cada objeto, y se quitan los
  `default` (el modo estricto lo exige). La respuesta se valida después con el mismo modelo Pydantic,
  así que restricciones que JSON Schema no impone en modo estricto (como `ge/le`) se siguen verificando.
- Una salida inválida, truncada (`finish_reason=length`), rechazada o vacía lanza
  `StructuredOutputError` con el texto crudo (`.raw`) para poder auditarla. **No se reintenta**
  automáticamente: en un verificador conviene ver el fallo, no taparlo.

**Ingesta**
- Interfaz: `fetch_submission(source) -> Workspace(path, content_hash, source, size_bytes)`;
  `source` es una unión discriminada Pydantic (`kind`: `github_public` | `zip` | `github_app`) y
  cada tipo tiene un `Fetcher` registrado. Si algo falla, el directorio parcial se borra.
- **Hash determinista**: sha256 de líneas `ruta_posix \0 tipo \0 sha256(contenido)` ordenadas
  por ruta. Hashear el *digest* de cada archivo (no concatenar bytes) evita colisiones del tipo
  `"ab"+"" == "a"+"b"`. Se ignoran `.git`, los directorios vacíos, las fechas y los permisos
  (el bit ejecutable no es fiable en Windows). Los symlinks se hashean por su destino, nunca se siguen.
- **GitHub**: solo SHA completo de 40 hex (un SHA corto o una rama no son "fijos"). Se hace
  `git init` + `fetch --depth 1 <sha>` + `checkout --detach`, sin bajar el historial. Se verifica que el
  objeto sea un *commit* (un SHA de tag anotado se rechaza con un mensaje claro; este caso apareció
  durante las pruebas) y que `HEAD == sha`. Después se borra `.git`.
  git se ejecuta endurecido: ignora la configuración global y la del sistema, desactiva `autocrlf`
  (si no, el mismo commit daría hashes distintos según la máquina), symlinks, transporte `file://`,
  LFS, hooks de plantilla y prompts de credenciales.
- **ZIP**: rechaza rutas absolutas, con unidad, `..`, `:` (ADS de NTFS), nombres reservados de Windows,
  symlinks, entradas cifradas o duplicadas (sin distinguir mayúsculas, como hace NTFS), y además
  comprueba que la ruta resuelta quede dentro del destino. Hay límites de tamaño subido, número de
  entradas y bytes descomprimidos; estos últimos se cuentan al escribir, sin fiarse de la cabecera.
  Si todo cuelga de una única carpeta raíz (como en "Download ZIP" de GitHub), se quita esa carpeta.
- **GitHub App**: el modelo `GitHubAppSource` y el `Fetcher` ya existen; el plan está documentado en
  el docstring del módulo (token de instalación pasado por `http.extraHeader`, nunca en la URL).

**Sandbox**
- `docker run` con `--network none`, `--cpus`, `--memory` = `--memory-swap` (sin swap), `--pids-limit`,
  `--user 65534:65534`, `--cap-drop ALL`, `no-new-privileges`, raíz `--read-only`, `/tmp` en tmpfs de
  64 MB, `/workspace` de solo lectura y `/out` escribible. `--pull never`: la imagen se prepara
  antes, fuera del tiempo medido.
- **Timeout**: al vencer, el contenedor se mata por nombre (`docker kill`). Matar el proceso `docker`
  del host no detiene el contenedor.
- stdout/stderr se leen en hilos que guardan solo los primeros N bytes y descartan el resto, para que
  una salida infinita no llene la RAM. Se informa `*_truncated`.
- Se informa `oom_killed` con `docker inspect` antes de borrar el contenedor.
- `duration_s` es tiempo de pared e incluye el arranque del contenedor (~0.5 s).

**API**
- `POST /submissions` en multipart: `repo` + `sha`, **o** un archivo `zip`, más un `command` opcional
  que se ejecuta con `sh -c` dentro del sandbox. Errores: 422 (entrada inválida), 501 (GitHub App),
  503 (Docker no disponible).
- El endpoint es síncrono (FastAPI lo corre en su *threadpool*). En la fase 1 no se persiste nada:
  el workspace, el ZIP y `/out` se borran al responder; solo se devuelven los nombres de los archivos de `/out`.

## Cómo correrlo

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install -r backend\requirements.txt
copy .env.example .env          # completar MODEL_API_KEY
docker pull python:3.12-slim    # imagen del sandbox (configurable con SANDBOX_IMAGE)
.venv\Scripts\python -m pytest -v
.venv\Scripts\python -m uvicorn backend.main:app --port 8000
```

```powershell
# Repo público fijado a un commit
curl.exe -F repo=pallets/itsdangerous -F sha=096c8d42545d3b68ea21a4f890fb2b2d8979c0bd --form-string "command=ls -la" http://127.0.0.1:8000/submissions
# ZIP
curl.exe -F zip=@entrega.zip --form-string "command=python -m pytest -q" http://127.0.0.1:8000/submissions
```

Usa `--form-string` para `command`: con `-F`, curl interpreta `;` como separador y corta el
comando (pasó durante las pruebas).

Los tests que necesitan Docker se saltan solos si Docker no está disponible. El de GitHub necesita red
y se desactiva con `SKIP_NETWORK_TESTS=1`.

## Salida real de los tests

```
platform win32 -- Python 3.14.5, pytest-9.2.0, pluggy-1.7.0
collected 65 items

backend/tests/test_api.py::test_zip_submission_returns_hash_and_result PASSED
backend/tests/test_api.py::test_same_zip_twice_same_hash PASSED
backend/tests/test_api.py::test_zip_slip_is_rejected_with_422 PASSED
backend/tests/test_api.py::test_exactly_one_source_required[form0-False] PASSED
backend/tests/test_api.py::test_exactly_one_source_required[form1-True] PASSED
backend/tests/test_api.py::test_exactly_one_source_required[form2-False] PASSED
backend/tests/test_api.py::test_invalid_sha_rejected PASSED
backend/tests/test_api.py::test_end_to_end_zip_through_real_sandbox PASSED
backend/tests/test_hashing.py::test_same_content_same_hash_regardless_of_creation_order PASSED
backend/tests/test_hashing.py::test_hash_is_stable_across_calls_and_ignores_mtime PASSED
backend/tests/test_hashing.py::test_content_change_changes_hash PASSED
backend/tests/test_hashing.py::test_rename_changes_hash PASSED
backend/tests/test_hashing.py::test_moving_bytes_between_files_changes_hash PASSED
backend/tests/test_hashing.py::test_git_dir_and_empty_dirs_are_ignored PASSED
backend/tests/test_hashing.py::test_empty_workspace_has_fixed_hash PASSED
backend/tests/test_ingest_github.py::test_invalid_repo_or_sha_rejected_before_network[not-a-repo-0000…] PASSED
backend/tests/test_ingest_github.py::test_invalid_repo_or_sha_rejected_before_network[owner/../etc-0000…] PASSED
backend/tests/test_ingest_github.py::test_invalid_repo_or_sha_rejected_before_network[https://github.com/a/b-0000…] PASSED
backend/tests/test_ingest_github.py::test_invalid_repo_or_sha_rejected_before_network[owner/name;rm -rf-0000…] PASSED
backend/tests/test_ingest_github.py::test_invalid_repo_or_sha_rejected_before_network[owner/name-abc123] PASSED
backend/tests/test_ingest_github.py::test_invalid_repo_or_sha_rejected_before_network[owner/name-main] PASSED
backend/tests/test_ingest_github.py::test_invalid_repo_or_sha_rejected_before_network[owner/name-gggg…] PASSED
backend/tests/test_ingest_github.py::test_github_app_is_declared_but_not_implemented PASSED
backend/tests/test_ingest_github.py::test_public_repo_checkout_at_sha_is_deterministic PASSED
backend/tests/test_ingest_zip.py::test_valid_zip_is_extracted_and_hashed PASSED
backend/tests/test_ingest_zip.py::test_same_zip_content_same_hash PASSED
backend/tests/test_ingest_zip.py::test_single_top_level_dir_is_stripped PASSED
backend/tests/test_ingest_zip.py::test_zip_slip_and_dangerous_names_rejected[../evil.txt] PASSED
backend/tests/test_ingest_zip.py::test_zip_slip_and_dangerous_names_rejected[ok/../../evil.txt] PASSED
backend/tests/test_ingest_zip.py::test_zip_slip_and_dangerous_names_rejected[..\\evil.txt] PASSED
backend/tests/test_ingest_zip.py::test_zip_slip_and_dangerous_names_rejected[/etc/passwd] PASSED
backend/tests/test_ingest_zip.py::test_zip_slip_and_dangerous_names_rejected[\\windows\\evil.txt] PASSED
backend/tests/test_ingest_zip.py::test_zip_slip_and_dangerous_names_rejected[C:/evil.txt] PASSED
backend/tests/test_ingest_zip.py::test_zip_slip_and_dangerous_names_rejected[C:\\evil.txt] PASSED
backend/tests/test_ingest_zip.py::test_zip_slip_and_dangerous_names_rejected[file.txt:stream] PASSED
backend/tests/test_ingest_zip.py::test_zip_slip_and_dangerous_names_rejected[dir/CON.txt] PASSED
backend/tests/test_ingest_zip.py::test_zip_slip_and_dangerous_names_rejected[trailing. ] PASSED
backend/tests/test_ingest_zip.py::test_safe_member_parts_accepts_normal_paths PASSED
backend/tests/test_ingest_zip.py::test_symlink_entries_rejected PASSED
backend/tests/test_ingest_zip.py::test_duplicate_entries_rejected PASSED
backend/tests/test_ingest_zip.py::test_uncompressed_size_limit_stops_zip_bomb PASSED
backend/tests/test_ingest_zip.py::test_too_many_files_rejected PASSED
backend/tests/test_ingest_zip.py::test_upload_size_limit PASSED
backend/tests/test_ingest_zip.py::test_not_a_zip PASSED
backend/tests/test_llm.py::test_valid_json_is_parsed_into_model PASSED
backend/tests/test_llm.py::test_request_uses_strict_json_schema_and_configured_model PASSED
backend/tests/test_llm.py::test_strict_schema_forbids_extra_and_requires_all_fields PASSED
backend/tests/test_llm.py::test_strict_schema_keeps_property_named_default PASSED
backend/tests/test_llm.py::test_invalid_output_raises_with_raw_content[no es json] PASSED
backend/tests/test_llm.py::test_invalid_output_raises_with_raw_content[{"verdict":"maybe","checks":[]}] PASSED
backend/tests/test_llm.py::test_invalid_output_raises_with_raw_content[{"verdict":"pass","checks":[],"confidence":7}] PASSED
backend/tests/test_llm.py::test_invalid_output_raises_with_raw_content[{"verdict":"pass"}] PASSED
backend/tests/test_llm.py::test_truncated_and_refused_and_empty_outputs_raise PASSED
backend/tests/test_llm.py::test_retries_on_429_and_5xx_then_succeeds PASSED
backend/tests/test_llm.py::test_retry_after_header_is_respected PASSED
backend/tests/test_llm.py::test_timeouts_and_connection_errors_are_retried PASSED
backend/tests/test_llm.py::test_gives_up_after_max_attempts PASSED
backend/tests/test_llm.py::test_client_errors_are_not_retried[400] PASSED
backend/tests/test_llm.py::test_client_errors_are_not_retried[401] PASSED
backend/tests/test_llm.py::test_missing_api_key_fails_fast PASSED
backend/tests/test_sandbox.py::test_docker_args_enforce_isolation PASSED
backend/tests/test_sandbox.py::test_timeout_kills_container PASSED
backend/tests/test_sandbox.py::test_runs_command_and_captures_output PASSED
backend/tests/test_sandbox.py::test_isolation_inside_container PASSED
backend/tests/test_sandbox.py::test_output_is_truncated PASSED

============================= 65 passed in 18.26s =============================
```

(Los ids parametrizados con 40 ceros se abreviaron con `…`. Durante el desarrollo fallaron 2 tests,
en corridas distintas, y ambos eran errores de los tests, no del código: el ZIP de la "bomba" no se comprimía porque `ZipInfo` no
hereda la compresión, y `write_text` en Windows escribe `\r\n`.)

### Pruebas manuales con el servidor real (uvicorn + curl)

| Caso | Resultado |
|---|---|
| `pallets/itsdangerous@096c8d4…` + importar el paquete | `exit_code 0`, stdout `itsdangerous 3.12.16`, hash `sha256:afa1e105…3ab4c` (igual al de la ingesta directa) |
| ZIP con `proj/calc.py` y `proj/test_calc.py` + `python test_calc.py` | `exit_code 0`, stdout `1 passed` (se quitó la carpeta raíz `proj/`) |
| ZIP + `python -c "bytearray(2 GB)"` con 512 MB | `exit_code 137`, `oom_killed: true` |
| `repo=o/n`, `sha=main` | HTTP 422 |
| Llamada real a `structured_completion` con un esquema anidado | JSON válido, `verdict: fail` correcto (README faltante), 5.5 s |

Tras las pruebas no quedaron contenedores `cc-sbx-*` ni workspaces en `.data/`.

## Pendiente

- **Ingesta C** (GitHub App): obtener el token de instalación y reutilizar el flujo de `github_public`.
- **Disco en `/out` sin cuota**: el bind mount no tiene límite de tamaño, así que una entrega maliciosa
  podría llenar el disco del host. Opciones: un volumen con cuota, un tmpfs con tamaño fijo copiado al
  terminar, o vigilar el tamaño durante la ejecución.
- **GitHub sin límite durante el fetch**: el tamaño se comprueba después del checkout. Un repo enorme
  se descarga hasta que vence `GIT_TIMEOUT_S`. Conviene consultar antes el tamaño con la API de GitHub.
- **Aislamiento a nivel contenedor, no VM**: usa el seccomp por defecto de Docker, sin gVisor ni Kata.
  Si hace falta más aislamiento, `--runtime=runsc` es el siguiente paso.
- **API**: sin autenticación, sin *rate limit*, sin cola. Las ejecuciones largas ocupan hilos del
  *threadpool*. Para la fase de agentes hará falta un worker o cola y persistencia de resultados y evidencia.
- **Imagen del sandbox fija** (`python:3.12-slim`): elegir la imagen según el stack de la entrega
  queda para más adelante. Las dependencias tampoco se pueden instalar (no hay red); habrá que resolver
  una fase de "build con red" separada de la fase de "test sin red".
- Persistir la evidencia (stdout, `/out`, hash) para el veredicto y el escrow.

## Lo que no pude verificar

- **429/5xx reales de la API de Meta**: el retry/backoff solo se probó con mocks. Tampoco se verificó
  si la API devuelve `Retry-After`, ni un timeout real del cliente.
- **Parámetros del modelo** fuera de `model`, `messages` y `response_format` (temperature, max_tokens,
  reasoning effort…): no se probaron y no se usan.
- **Hash ZIP vs. git del mismo commit**: el código está pensado para que coincidan (se quita la carpeta
  raíz y no se convierten fines de línea), pero no se comparó un "Download ZIP" de GitHub con el
  checkout. Pueden diferir por `export-ignore` o `eol=crlf` en `.gitattributes`.
- **`/out` escribible por uid 65534 en Linux nativo**: funciona en Docker Desktop/WSL2, que expone los
  bind mounts con permisos abiertos. En un Linux nativo, el directorio del host puede necesitar
  `chown 65534` o `chmod`.
- **El efecto real de `--cpus`**: no se midió.
- **Python 3.12/3.13**: todo se probó solo con 3.14.5.

## Incidente de entorno (Docker Desktop)

Docker Desktop no arrancaba: `remove …\Docker\run\sailor-ingest.sock: The file cannot be accessed by
the system`. Varios sockets AF_UNIX del 20/10 habían quedado huérfanos y Windows no deja borrarlos, ni
siquiera con `del /f`. Matar los procesos de Docker los vuelve a dejar rotos. La solución fue renombrar
(no borrar) las carpetas que los contienen para que Docker las recree, y arrancarlo sin interrumpirlo.
Quedaron renombradas en `%LOCALAPPDATA%`:

- `Docker\run.stale-20261028`, `Docker\run.stale2-20261028`, `Docker\run.stale3-20261028`
- `docker-secrets-engine.stale-20261028`, `docker-secrets-engine.stale2-20261028`

Se pueden borrar (tras reiniciar Windows deberían dejar de estar bloqueadas). Si Docker se vuelve a
cerrar de forma abrupta, el problema puede repetirse.
