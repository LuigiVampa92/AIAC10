import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from openai import APIConnectionError, RateLimitError
from pydantic import SecretStr

from aiac.config import Settings
from aiac.llm.client import ModelError, generate, provider_error_message


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "aiac_llm_mode": "demo",
        "openai_api_key": None,
        "aiac_llm_model": "",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_demo_never_constructs_client() -> None:
    with patch("aiac.llm.client.AsyncOpenAI") as factory:
        result = asyncio.run(generate("hello", settings()))
    factory.assert_not_called()
    assert result.mode == "demo"


def test_unknown_provider_never_uses_openai_key() -> None:
    with patch("aiac.llm.client.AsyncOpenAI") as factory:
        with pytest.raises(ModelError, match="пока не поддерживается"):
            asyncio.run(
                generate(
                    "hello",
                    settings(
                        aiac_llm_mode="live",
                        aiac_llm_provider="another-provider",
                        openai_api_key=SecretStr("fake-test-key"),
                        aiac_llm_model="other-model",
                    ),
                )
            )
    factory.assert_not_called()


def test_real_request_contract_without_network() -> None:
    raw_response = {"model": "test-model", "output": [], "usage": {"total_tokens": 12}}
    response = SimpleNamespace(
        output_text="mock response",
        model="test-model",
        model_dump_json=lambda **kwargs: json.dumps(raw_response, **kwargs),
    )
    api = AsyncMock()
    api.responses.create.return_value = response
    api.__aenter__.return_value = api
    with patch("aiac.llm.client.AsyncOpenAI", return_value=api) as factory:
        result = asyncio.run(
            generate(
                "hello",
                settings(
                    aiac_llm_mode="live",
                    openai_api_key=SecretStr("fake-test-key"),
                    aiac_llm_model="test-model",
                ),
            )
        )
    factory.assert_called_once_with(api_key="fake-test-key", timeout=30.0, max_retries=0)
    api.responses.create.assert_awaited_once_with(
        model="test-model", input="hello", max_output_tokens=512
    )
    assert result.text == "mock response"
    assert result.model == "test-model"
    assert json.loads(result.request_json) == api.responses.create.call_args.kwargs
    assert json.loads(result.response_json) == raw_response
    assert "fake-test-key" not in result.request_json + result.response_json


def test_provider_errors_do_not_leak_secrets() -> None:
    api = AsyncMock()
    api.__aenter__.return_value = api
    api.responses.create.side_effect = APIConnectionError(
        message="secret-should-not-leak", request=httpx.Request("POST", "https://example.invalid")
    )
    with patch("aiac.llm.client.AsyncOpenAI", return_value=api):
        with pytest.raises(ModelError) as error:
            asyncio.run(
                generate(
                    "hello",
                    settings(
                        aiac_llm_mode="live",
                        openai_api_key=SecretStr("fake-test-key"),
                        aiac_llm_model="test-model",
                    ),
                )
            )
    assert "secret-should-not-leak" not in str(error.value)
    assert json.loads(error.value.request_json)["input"] == "hello"
    assert "fake-test-key" not in error.value.request_json


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("credit_balance_exhausted", "Недостаточно средств"),
        ("insufficient_quota", "Недостаточно средств"),
        ("rate_limit_exceeded", "Достигнут лимит запросов"),
    ],
)
def test_quota_errors_are_distinguished_from_rate_limits(code: str, expected: str) -> None:
    error = RateLimitError(
        "secret-provider-message",
        response=httpx.Response(429, request=httpx.Request("POST", "https://example.invalid")),
        body={"code": code, "type": "insufficient_quota"},
    )
    message = provider_error_message(error)
    assert expected in message
    assert "secret-provider-message" not in message
