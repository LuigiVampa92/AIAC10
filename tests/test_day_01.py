"""Day 1 acceptance: form -> day module -> LLM boundary -> rendered response."""

import json
from html.parser import HTMLParser
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import APIConnectionError

from aiac.config import Settings
from aiac.web.app import app

URL = "/weeks/1/days/1"
PROMPT = "Say hello in one short sentence."


class ResultParser(HTMLParser):
    """Read rendered result text and debug JSON without external HTML dependencies."""

    def __init__(self) -> None:
        super().__init__()
        self.blocks: list[str] = []
        self.current: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "pre":
            self.current = []

    def handle_data(self, data: str) -> None:
        if self.current is not None:
            self.current.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "pre" and self.current is not None:
            self.blocks.append("".join(self.current))
            self.current = None


def result_blocks(html: str) -> list[str]:
    parser = ResultParser()
    parser.feed(html.partition('<div id="task-result"')[2])
    return parser.blocks


def test_day_01_real_mode_contract_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIAC_LLM_MODE", "live")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-day-01-key")
    monkeypatch.setenv("AIAC_LLM_MODEL", "test-model")
    answer = "Hello <friend>!"
    raw = {"status": "completed", "model": "test-model", "output": [{"text": answer}]}
    api = AsyncMock()
    api.__aenter__.return_value = api
    api.responses.create.return_value = SimpleNamespace(
        output_text=answer,
        model="test-model",
        model_dump_json=lambda **kwargs: json.dumps(raw, **kwargs),
    )
    with patch("aiac.llm.client.AsyncOpenAI", return_value=api):
        with TestClient(app) as client:
            assert client.get(URL).status_code == 200
            response = client.post(URL, data={"prompt": PROMPT})
    assert response.status_code == 200
    answer_text, request_json, response_json = result_blocks(response.text)
    assert answer_text == answer
    assert json.loads(request_json) == {
        "model": "test-model",
        "input": PROMPT,
        "max_output_tokens": 512,
    }
    assert json.loads(response_json) == raw
    api.responses.create.assert_awaited_once_with(**json.loads(request_json))
    assert "fake-day-01-key" not in response.text
    assert "<friend>" not in response.text
    assert "Демонстрационная заглушка" not in answer_text


def test_day_01_provider_failure_is_not_a_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIAC_LLM_MODE", "live")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-day-01-key")
    monkeypatch.setenv("AIAC_LLM_MODEL", "test-model")
    api = AsyncMock()
    api.__aenter__.return_value = api
    api.responses.create.side_effect = APIConnectionError(
        message="private-provider-error",
        request=httpx.Request("POST", "https://example.invalid"),
    )
    with patch("aiac.llm.client.AsyncOpenAI", return_value=api):
        with TestClient(app) as client:
            response = client.post(URL, data={"prompt": PROMPT})
    assert response.status_code == 503
    assert "Не удалось подключиться" in response.text
    assert "private-provider-error" not in response.text
    assert "fake-day-01-key" not in response.text
    assert "Демонстрационная заглушка" not in response.text.partition('<div id="task-result"')[2]
    assert len(result_blocks(response.text)) == 1  # Request only, no invented answer.
    api.responses.create.assert_awaited_once()


@pytest.mark.live_openai
def test_day_01_live_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    """One paid call, no exact-answer assertion, no fallback and no retries."""
    monkeypatch.setenv("AIAC_LLM_MODE", "live")
    settings = Settings()
    if not settings.openai_api_key or not settings.openai_api_key.get_secret_value().strip():
        pytest.fail("Live check requires OPENAI_API_KEY configured locally.", pytrace=False)
    if not settings.aiac_llm_model.strip():
        pytest.fail("Live check requires AIAC_LLM_MODEL configured locally.", pytrace=False)

    with TestClient(app) as client:
        page = client.get(URL)
        if page.status_code != 200 or 'action="' + URL + '"' not in page.text:
            pytest.fail("Day 1 page/form unavailable; API call not attempted.", pytrace=False)
        response = client.post(URL, data={"prompt": PROMPT})

    if response.status_code != 200:
        pytest.fail(
            f"Live check failed (HTTP {response.status_code}). "
            "Check API balance, model access and connection; no retry performed.",
            pytrace=False,
        )
    blocks = result_blocks(response.text)
    if len(blocks) != 3 or not blocks[0].strip():
        pytest.fail("Live check: missing model text or debug JSON.", pytrace=False)
    try:
        request_body = json.loads(blocks[1])
        response_body = json.loads(blocks[2])
    except ValueError:
        pytest.fail("Live check: invalid debug JSON.", pytrace=False)
    if request_body != {
        "model": settings.aiac_llm_model,
        "input": PROMPT,
        "max_output_tokens": 512,
    }:
        pytest.fail("Live check: request parameters do not match.", pytrace=False)
    if response_body.get("status") != "completed" or not response_body.get("model"):
        pytest.fail("Live check: response is not completed or model ID is missing.", pytrace=False)
    if "Демонстрационная заглушка" in blocks[0]:
        pytest.fail("Demo is not a successful live check.", pytrace=False)
    print(
        json.dumps(
            {
                "check": "week_01/day_01",
                "status": "passed",
                "model": response_body["model"],
                "max_output_tokens": request_body["max_output_tokens"],
                "usage": response_body.get("usage"),
            },
            ensure_ascii=False,
        )
    )
