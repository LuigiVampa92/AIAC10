import asyncio
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import BadRequestError, RateLimitError
from openai.types.chat import ChatCompletion
from pydantic import SecretStr

from aiac.config import Settings
from aiac.llm.chat import complete
from aiac.llm.client import ModelError
from aiac.tasks.week_01.day_02 import (
    DEFAULT_SCHEMA,
    DEFAULTS,
    VARIANTS,
    ExperimentInput,
    RunInput,
    check_schema,
    execute,
    payload_for,
    plan,
    schema_errors,
    strict_json,
)
from aiac.web.app import app


def live_settings() -> Settings:
    return Settings(
        _env_file=None,
        aiac_llm_mode="live",
        aiac_llm_provider="openai",
        aiac_llm_model="test-model",
        openai_api_key=SecretStr("fake-day-02-key"),
    )


def config() -> ExperimentInput:
    return ExperimentInput.model_validate(DEFAULTS)


def response(
    text: str = '{"name":"Анна","age":28,"city":"Казань"}',
    finish: str = "stop",
    refusal: str | None = None,
) -> ChatCompletion:
    return ChatCompletion.model_validate(
        {
            "id": "test-response",
            "object": "chat.completion",
            "created": 0,
            "model": "test-model-snapshot",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": finish,
                    "message": {"role": "assistant", "content": text, "refusal": refusal},
                }
            ],
            "usage": {"prompt_tokens": 20, "completion_tokens": 30, "total_tokens": 50},
        }
    )


def sdk(reply: ChatCompletion) -> AsyncMock:
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.chat.completions.create.return_value = reply
    return client


def run_input(variant: str = "schema") -> RunInput:
    return RunInput.model_validate(
        DEFAULTS
        | {
            "variant": variant,
            "repeat": 1,
            "model": "test-model",
            "provider": "openai",
        }
    )


def test_plan_and_controlled_payloads() -> None:
    settings = live_settings()
    experiment = config()
    assert plan(experiment, settings)["total"] == 15
    payloads = {key: payload_for(experiment, key, settings.aiac_llm_model) for key in VARIANTS}
    assert payloads["baseline"]["messages"][0]["content"] == experiment.prompt
    assert "response_format" not in payloads["format"]
    assert payloads["format"]["messages"] == payloads["limit"]["messages"]
    assert payloads["stop"]["messages"] == payloads["format"]["messages"]
    assert payloads["limit"]["max_completion_tokens"] == 32
    assert payloads["baseline"]["max_completion_tokens"] == 512
    assert payloads["stop"]["stop"] == ["}"]
    assert payloads["schema"]["response_format"]["json_schema"]["strict"] is True
    assert payloads["schema"]["response_format"]["json_schema"]["schema"] == DEFAULT_SCHEMA
    assert all("temperature" not in payload for payload in payloads.values())


@pytest.mark.parametrize("text", ["```json\n{}\n```", '{"a":1,"a":2}', "NaN", "1e999", "{"])
def test_raw_json_is_not_repaired(text: str) -> None:
    with pytest.raises(ValueError):
        strict_json(text)


def test_json_parsing_does_not_prove_schema_conformance() -> None:
    value = strict_json('{"name":"Анна","age":true,"extra":1}')
    errors = schema_errors(value, DEFAULT_SCHEMA)
    assert any("age" in error for error in errors)
    assert any("city" in error for error in errors)
    assert any("extra" in error for error in errors)
    assert not schema_errors({"name": "Анна", "age": 28.0, "city": "Казань"}, DEFAULT_SCHEMA)


def test_nested_arrays_enums_and_json_numeric_semantics() -> None:
    schema = {
        "type": "array",
        "items": {"type": "integer", "enum": [1, 2]},
    }
    check_schema(schema)
    assert schema_errors([True], schema)
    assert not schema_errors([1.0, 2], schema)
    assert schema_errors([3], schema)
    with pytest.raises(ValueError, match="повторяющиеся"):
        check_schema({"type": "number", "enum": [1, 1.0]})


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "properties": {}, "required": []},
        {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": [],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
            "$ref": "https://example.invalid/schema",
        },
        {"type": "array", "items": {"type": "string"}},
    ],
)
def test_unsupported_schema_rejected_before_api(schema: dict[str, object]) -> None:
    invalid = config().model_copy(update={"schema_text": json.dumps(schema)})
    with patch("aiac.llm.chat.AsyncOpenAI") as factory:
        with pytest.raises(ValueError, match="JSON Schema"):
            plan(invalid, live_settings())
    factory.assert_not_called()


@pytest.mark.parametrize(
    ("reply", "status", "parsed", "conforms"),
    [
        (response(), "completed", True, True),
        (response('{"name":"Анна"}'), "completed", True, False),
        (response('{"name":', "length"), "incomplete", False, False),
        (response("", refusal="refused"), "refusal", None, None),
        (response(""), "incomplete", None, None),
    ],
)
def test_full_experiment_response_validation(
    reply: ChatCompletion,
    status: str,
    parsed: bool | None,
    conforms: bool | None,
) -> None:
    api = sdk(reply)
    with patch("aiac.llm.chat.AsyncOpenAI", return_value=api) as factory:
        result = asyncio.run(execute(run_input(), live_settings()))
    factory.assert_called_once_with(api_key="fake-day-02-key", timeout=30.0, max_retries=0)
    assert result["status"] == status
    assert result["json_valid"] is parsed
    assert result["schema_valid"] is conforms
    assert result["model"] == "test-model-snapshot"
    assert result["usage"]["total_tokens"] == 50
    api.chat.completions.create.assert_awaited_once_with(**result["request"])
    assert "fake-day-02-key" not in json.dumps(result)


def test_stop_is_sent_to_api_without_local_truncation() -> None:
    api = sdk(response())
    with patch("aiac.llm.chat.AsyncOpenAI", return_value=api):
        result = asyncio.run(execute(run_input("stop"), live_settings()))
    assert api.chat.completions.create.call_args.kwargs["stop"] == ["}"]
    assert result["text"].endswith("}")  # The application does not simulate API stop.


@pytest.mark.parametrize("quota", [False, True])
def test_safe_errors_and_batch_policy(quota: bool) -> None:
    api = sdk(response())
    http_response = httpx.Response(
        429 if quota else 400, request=httpx.Request("POST", "https://example.invalid")
    )
    error = RateLimitError if quota else BadRequestError
    api.chat.completions.create.side_effect = error(
        "private-provider-error",
        response=http_response,
        body={"code": "credit_balance_exhausted" if quota else "unsupported_parameter"},
    )
    with patch("aiac.llm.chat.AsyncOpenAI", return_value=api):
        result = asyncio.run(execute(run_input("stop"), live_settings()))
    assert result["status"] == "api_error"
    assert result["stop_batch"] is quota
    assert result["json_valid"] is None
    assert result["response"] is None
    assert "private-provider-error" not in json.dumps(result)
    assert "fake-day-02-key" not in json.dumps(result)
    api.chat.completions.create.assert_awaited_once()


def test_changed_model_and_demo_cannot_start_api() -> None:
    with patch("aiac.llm.chat.AsyncOpenAI") as factory:
        with pytest.raises(ValueError, match="изменились"):
            asyncio.run(
                execute(run_input().model_copy(update={"model": "different"}), live_settings())
            )
        with pytest.raises(ModelError):
            asyncio.run(complete({}, Settings(_env_file=None)))
    factory.assert_not_called()


def test_routes_validate_before_calls_and_render_form(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIAC_LLM_MODE", "live")
    monkeypatch.setenv("AIAC_LLM_MODEL", "test-model")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-day-02-key")
    with TestClient(app) as client:
        page = client.get("/weeks/1/days/2")
        assert page.status_code == 200
        assert 'id="experiment-form"' in page.text
        assert "Решение и проверка" not in page.text
        assert client.post("/weeks/1/days/2/plan", json=DEFAULTS).json()["total"] == 15
        for overrides in [
            {"repeats": 6},
            {"token_limit": 9999},
            {"schema_text": "{"},
            {"prompt": " "},
            {"stop_sequence": ""},
        ]:
            assert client.post("/weeks/1/days/2/plan", json=DEFAULTS | overrides).status_code == 422
        api = sdk(response())
        with patch("aiac.llm.chat.AsyncOpenAI", return_value=api):
            result = client.post("/weeks/1/days/2/runs", json=run_input().model_dump())
        assert result.status_code == 200
        assert result.json()["schema_valid"] is True
        assert "fake-day-02-key" not in result.text
