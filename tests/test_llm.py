import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from openai import APIConnectionError
from pydantic import SecretStr

from aiac.config import Settings
from aiac.llm.client import ModelError, generate


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "aiac_llm_mode": "demo",
        "openai_api_key": None,
        "aiac_openai_model": "",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_demo_never_constructs_client() -> None:
    with patch("aiac.llm.client.AsyncOpenAI") as factory:
        result = asyncio.run(generate("hello", settings()))
    factory.assert_not_called()
    assert result.mode == "demo"


def test_real_request_contract_without_network() -> None:
    response = SimpleNamespace(output_text="mock response", model="test-model")
    api = AsyncMock()
    api.responses.create.return_value = response
    api.__aenter__.return_value = api
    with patch("aiac.llm.client.AsyncOpenAI", return_value=api) as factory:
        result = asyncio.run(
            generate(
                "hello",
                settings(
                    aiac_llm_mode="openai",
                    openai_api_key=SecretStr("fake-test-key"),
                    aiac_openai_model="test-model",
                ),
            )
        )
    factory.assert_called_once_with(api_key="fake-test-key", timeout=30.0, max_retries=0)
    api.responses.create.assert_awaited_once_with(
        model="test-model", input="hello", max_output_tokens=512
    )
    assert result.text == "mock response"
    assert result.model == "test-model"


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
                        aiac_llm_mode="openai",
                        openai_api_key=SecretStr("fake-test-key"),
                        aiac_openai_model="test-model",
                    ),
                )
            )
    assert "secret-should-not-leak" not in str(error.value)
