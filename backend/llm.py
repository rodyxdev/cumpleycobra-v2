"""Cliente LLM para Muse Spark (API de Meta, compatible con el SDK de OpenAI).

`structured_completion` pide una respuesta ajustada a un JSON Schema derivado de un
modelo Pydantic (`response_format=json_schema`, `strict=True`) y la valida con ese
mismo modelo. Los reintentos los hacemos nosotros (el SDK queda con `max_retries=0`)
para controlar el backoff y respetar `Retry-After`.
"""

from __future__ import annotations

import copy
import logging
import random
import time
from typing import Any, Callable, TypeVar

import openai
from openai import OpenAI
from pydantic import BaseModel, ValidationError

from backend.config import get_settings

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

RETRYABLE_STATUS = {408, 409, 429}
MAX_BACKOFF_S = 30.0


class LLMError(RuntimeError):
    """Fallo no recuperable (o reintentos agotados) al llamar al modelo."""


class StructuredOutputError(LLMError):
    """El modelo respondió, pero el contenido no cumple el esquema."""

    def __init__(self, message: str, raw: str | None):
        super().__init__(message)
        self.raw = raw


def get_client(timeout_s: float | None = None) -> OpenAI:
    s = get_settings()
    if not s.model_api_key:
        raise LLMError("Falta MODEL_API_KEY en el entorno")
    return OpenAI(
        base_url=s.model_base_url,
        api_key=s.model_api_key,
        timeout=timeout_s or s.llm_timeout_s,
        max_retries=0,
    )


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """JSON Schema de Pydantic adaptado a modo estricto.

    El modo estricto exige `additionalProperties: false` y que todas las propiedades
    estén en `required` en cada objeto (incluidos los de `$defs`).
    """
    schema = copy.deepcopy(model.model_json_schema())

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        if node.get("type") == "object" and "properties" in node:
            node["additionalProperties"] = False
            node["required"] = list(node["properties"].keys())
        node.pop("default", None)
        for key, value in node.items():
            if key in ("properties", "$defs") and isinstance(value, dict):
                # Mapas nombre -> subesquema: no tocar las claves, sólo los subesquemas.
                for sub in value.values():
                    walk(sub)
            else:
                walk(value)

    walk(schema)
    return schema


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, (openai.APITimeoutError, openai.APIConnectionError)):
        return True
    if isinstance(exc, openai.APIStatusError):
        return exc.status_code in RETRYABLE_STATUS or exc.status_code >= 500
    return False


def _retry_after_s(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    if response is None:
        return None
    raw = response.headers.get("retry-after")
    try:
        return float(raw) if raw is not None else None
    except ValueError:
        return None


def _backoff_s(attempt: int, base_s: float) -> float:
    # Exponencial con jitter completo: U(0, base * 2^(attempt-1)), acotado.
    return random.uniform(0, min(MAX_BACKOFF_S, base_s * 2 ** (attempt - 1)))


def structured_completion(
    messages: list[dict[str, Any]],
    output_model: type[T],
    *,
    model: str | None = None,
    schema_name: str | None = None,
    client: OpenAI | None = None,
    timeout_s: float | None = None,
    max_attempts: int | None = None,
    backoff_base_s: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Llama al modelo exigiendo salida JSON conforme a `output_model` y la valida."""
    s = get_settings()
    client = client or get_client(timeout_s)
    attempts = max_attempts or s.llm_max_attempts
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": schema_name or output_model.__name__,
            "schema": strict_json_schema(output_model),
            "strict": True,
        },
    }

    for attempt in range(1, attempts + 1):
        try:
            response = client.chat.completions.create(
                model=model or s.muse_model,
                messages=messages,
                response_format=response_format,
            )
            break
        except Exception as exc:  # noqa: BLE001 - se reclasifica abajo
            if not _is_retryable(exc) or attempt == attempts:
                raise LLMError(f"Llamada al modelo fallida tras {attempt} intento(s): {exc}") from exc
            delay = _retry_after_s(exc)
            delay = min(delay, MAX_BACKOFF_S) if delay is not None else _backoff_s(attempt, backoff_base_s)
            log.warning("LLM intento %d/%d falló (%s); reintento en %.1fs", attempt, attempts, type(exc).__name__, delay)
            sleep(delay)

    choice = response.choices[0]
    raw = choice.message.content
    refusal = getattr(choice.message, "refusal", None)
    if refusal:
        raise StructuredOutputError(f"El modelo rechazó la petición: {refusal}", raw)
    if choice.finish_reason == "length":
        raise StructuredOutputError("Respuesta truncada (finish_reason=length)", raw)
    if not raw:
        raise StructuredOutputError("Respuesta vacía", raw)
    try:
        return output_model.model_validate_json(raw)
    except ValidationError as exc:
        raise StructuredOutputError(f"La respuesta no cumple el esquema: {exc}", raw) from exc
