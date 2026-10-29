"""Tests del cliente LLM con la API mockeada (no hacen llamadas reales)."""

from types import SimpleNamespace
from typing import Literal

import httpx
import openai
import pytest
from pydantic import BaseModel, Field

from backend.llm import LLMError, StructuredOutputError, strict_json_schema, structured_completion


class Check(BaseModel):
    criterion: str
    passed: bool


class Verdict(BaseModel):
    verdict: Literal["pass", "fail"]
    checks: list[Check]
    confidence: float = Field(default=0.5, ge=0, le=1)


VALID = '{"verdict":"pass","checks":[{"criterion":"tests","passed":true}],"confidence":0.9}'
MESSAGES = [{"role": "user", "content": "¿pasa?"}]


def completion(content, finish_reason="stop", refusal=None):
    message = SimpleNamespace(content=content, refusal=refusal)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish_reason)])


def api_error(status, headers=None):
    request = httpx.Request("POST", "https://api.meta.ai/v1/chat/completions")
    response = httpx.Response(status, headers=headers or {}, request=request)
    cls = {429: openai.RateLimitError, 400: openai.BadRequestError, 401: openai.AuthenticationError}.get(
        status, openai.InternalServerError
    )
    return cls(f"HTTP {status}", response=response, body=None)


class FakeClient:
    """Imita `client.chat.completions.create`, devolviendo/lanzando en orden."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def call(client, **kw):
    sleeps = []
    result = structured_completion(MESSAGES, Verdict, client=client, sleep=sleeps.append, **kw)
    return result, sleeps


def test_valid_json_is_parsed_into_model():
    client = FakeClient(completion(VALID))
    result, sleeps = call(client)
    assert result == Verdict(verdict="pass", checks=[Check(criterion="tests", passed=True)], confidence=0.9)
    assert sleeps == []


def test_request_uses_strict_json_schema_and_configured_model(monkeypatch):
    monkeypatch.setenv("MUSE_MODEL", "muse-spark-test")
    client = FakeClient(completion(VALID))
    call(client)
    sent = client.calls[0]
    assert sent["model"] == "muse-spark-test"
    rf = sent["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["name"] == "Verdict"


def test_strict_schema_forbids_extra_and_requires_all_fields():
    schema = strict_json_schema(Verdict)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"verdict", "checks", "confidence"}
    assert "default" not in schema["properties"]["confidence"]
    nested = schema["$defs"]["Check"]
    assert nested["additionalProperties"] is False
    assert set(nested["required"]) == {"criterion", "passed"}


def test_strict_schema_keeps_property_named_default():
    class Weird(BaseModel):
        default: str

    schema = strict_json_schema(Weird)
    assert "default" in schema["properties"]
    assert schema["required"] == ["default"]


@pytest.mark.parametrize(
    "content",
    [
        "no es json",
        '{"verdict":"maybe","checks":[]}',            # enum inválido
        '{"verdict":"pass","checks":[],"confidence":7}',  # fuera de rango
        '{"verdict":"pass"}',                         # falta campo requerido
    ],
)
def test_invalid_output_raises_with_raw_content(content):
    with pytest.raises(StructuredOutputError) as info:
        call(FakeClient(completion(content)))
    assert info.value.raw == content


def test_truncated_and_refused_and_empty_outputs_raise():
    with pytest.raises(StructuredOutputError, match="truncada"):
        call(FakeClient(completion('{"verdict":', finish_reason="length")))
    with pytest.raises(StructuredOutputError, match="rechazó"):
        call(FakeClient(completion(None, refusal="no puedo")))
    with pytest.raises(StructuredOutputError, match="vacía"):
        call(FakeClient(completion("")))


def test_retries_on_429_and_5xx_then_succeeds():
    client = FakeClient(api_error(429), api_error(503), completion(VALID))
    result, sleeps = call(client, max_attempts=4, backoff_base_s=1.0)
    assert result.verdict == "pass"
    assert len(client.calls) == 3
    assert len(sleeps) == 2
    assert 0 <= sleeps[0] <= 1.0 and 0 <= sleeps[1] <= 2.0  # backoff exponencial con jitter


def test_retry_after_header_is_respected():
    client = FakeClient(api_error(429, {"retry-after": "7"}), completion(VALID))
    _, sleeps = call(client)
    assert sleeps == [7.0]


def test_timeouts_and_connection_errors_are_retried():
    request = httpx.Request("POST", "https://api.meta.ai/v1/chat/completions")
    client = FakeClient(openai.APITimeoutError(request), openai.APIConnectionError(request=request), completion(VALID))
    result, sleeps = call(client)
    assert result.verdict == "pass" and len(sleeps) == 2


def test_gives_up_after_max_attempts():
    client = FakeClient(*(api_error(500) for _ in range(3)))
    with pytest.raises(LLMError, match="3 intento"):
        call(client, max_attempts=3)
    assert len(client.calls) == 3


@pytest.mark.parametrize("status", [400, 401])
def test_client_errors_are_not_retried(status):
    client = FakeClient(api_error(status), completion(VALID))
    with pytest.raises(LLMError):
        call(client)
    assert len(client.calls) == 1


def test_missing_api_key_fails_fast(monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "")
    with pytest.raises(LLMError, match="MODEL_API_KEY"):
        structured_completion(MESSAGES, Verdict)
