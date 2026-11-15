import asyncio
import shutil
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from aiac.agents.storage import JsonHistory
from aiac.config import ROOT, Settings
from aiac.llm import reasoning, tokens
from aiac.llm.client import ModelError
from aiac.tasks.week_02 import day_03 as day08
from aiac.web.app import app


@pytest.fixture
def directory(monkeypatch):
    path = ROOT / ".cache" / "tests" / uuid4().hex
    path.mkdir(parents=True)
    monkeypatch.setattr(day08, "STORE_ROOT", path)
    yield path
    shutil.rmtree(path)


def live():
    return Settings(
        _env_file=None,
        aiac_llm_mode="live",
        aiac_llm_model="gpt-6-luna",
        openai_api_key="not-a-real-key",
    )


def reply(text="Север, 42000 рублей, 20 декабря.", output=None):
    return reasoning.ReasoningResult(
        {
            "output": output or [],
            "usage": {
                "input_tokens": 100,
                "output_tokens": 20,
                "total_tokens": 120,
                "input_tokens_details": {"cached_tokens": 0},
            },
        },
        text,
        "gpt-6-luna",
        "completed",
        0.1,
    )


def test_counter_forwards_only_supported_fields(monkeypatch):
    count = AsyncMock(return_value=SimpleNamespace(input_tokens=123))
    client = AsyncMock()
    client.__aenter__.return_value.responses.input_tokens.count = count
    monkeypatch.setattr(tokens, "AsyncOpenAI", lambda **kwargs: client)
    payload = {
        "model": "gpt-6-luna",
        "input": [{"role": "user", "content": "abc"}],
        "tools": [],
        "instructions": "system",
        "store": False,
        "max_output_tokens": 4096,
        "include": ["reasoning.encrypted_content"],
        "truncation": "disabled",
    }
    assert asyncio.run(tokens.count(payload, live())) == 123
    sent = count.await_args.kwargs
    assert sent["input"] == payload["input"] and sent["truncation"] == "disabled"
    assert "store" not in sent and "max_output_tokens" not in sent
    with pytest.raises(ModelError):
        asyncio.run(tokens.count(payload, Settings(_env_file=None)))


def test_meter_counts_plain_conversation_without_tools(directory, monkeypatch):
    count = AsyncMock(side_effect=[10, 100, 12, 145, 175])
    monkeypatch.setattr(tokens, "count", count)
    complete = AsyncMock(side_effect=[reply("Запомнил"), reply("Павел, проект Север")])
    monkeypatch.setattr(reasoning, "complete", complete)
    first = asyncio.run(day08.execute(day08.ChatInput(prompt="Я Павел, проект Север"), live()))
    turn = first["turns"][0]
    assert turn["current_message_tokens"] == 10 and turn["history_before_turn_tokens"] == 0
    assert [m["full_input_tokens"] for m in turn["measurements"]] == [100]
    assert turn["counting_calls"] == 2 and turn["api_calls"] == 1
    assert turn["usage"]["total_tokens"] == 120
    assert turn["cost_usd"] == pytest.approx(0.00002)
    assert "tools" not in count.await_args_list[1].args[0]
    assert count.await_args_list[1].args[0]["instructions"]
    saved = JsonHistory(directory).load()
    second = asyncio.run(day08.execute(day08.ChatInput(prompt="Напомни проект"), live()))
    assert second["turns"][1]["history_before_turn_tokens"] == 145
    assert second["total_cost_usd"] == pytest.approx(0.00004)
    assert count.await_args_list[3].args[0]["input"] == saved.history
    for request in complete.await_args_list:
        payload = request.args[0]
        assert payload["truncation"] == "disabled"
        assert not {"tools", "tool_choice", "parallel_tool_calls"} & payload.keys()
        assert "current_time" not in payload["instructions"]


@pytest.mark.parametrize(
    ("full", "outcome", "generations"),
    [
        (100, "completed", 1),
        (10000, "completed", 1),
        (1200000, "model_context_overflow", 1),
        (2200000, "cost_guard", 0),
    ],
)
def test_manual_requests_use_fixed_budget(directory, monkeypatch, full, outcome, generations):
    monkeypatch.setattr(tokens, "count", AsyncMock(side_effect=[10, full]))
    failure = reasoning.ReasoningResult(
        {"http_status": 400, "error": {"code": "context_length_exceeded"}},
        "",
        "gpt-6-luna",
        "api_error",
        0.1,
        "Слишком большой контекст",
    )
    complete = AsyncMock(return_value=failure if full > 1050000 else reply())
    monkeypatch.setattr(reasoning, "complete", complete)
    state = asyncio.run(day08.execute(day08.ChatInput(prompt="Тестовый запрос"), live()))
    result = state["turns"][0]
    assert result["outcome"] == outcome
    assert result["api_calls"] == generations == complete.await_count
    assert result["counting_calls"] == 2
    assert result["history_before_turn_tokens"] == 0
    if generations and full > 1050000:
        assert result["cost_usd"] is None
        assert complete.await_args.args[0]["truncation"] == "disabled"


def test_count_failure_prevents_generation(directory, monkeypatch):
    monkeypatch.setattr(tokens, "count", AsyncMock(side_effect=ModelError("Подсчёт недоступен")))
    complete = AsyncMock()
    monkeypatch.setattr(reasoning, "complete", complete)
    data = asyncio.run(day08.execute(day08.ChatInput(prompt="Привет"), live()))["turns"][0]
    assert data["outcome"] == "count_api_error" and data["api_calls"] == 0
    assert data["counting_calls"] == 1
    complete.assert_not_called()


def test_manual_overflow_is_sent_to_model(directory, monkeypatch):
    monkeypatch.setattr(tokens, "count", AsyncMock(side_effect=[10, 1_100_000]))
    failure = reasoning.ReasoningResult(
        {"http_status": 400, "error": {"code": "context_length_exceeded"}},
        "",
        "gpt-6-luna",
        "api_error",
        0.1,
        "Слишком большой контекст",
    )
    complete = AsyncMock(return_value=failure)
    monkeypatch.setattr(reasoning, "complete", complete)
    data = asyncio.run(day08.execute(day08.ChatInput(prompt="Большой диалог"), live()))
    assert data["turns"][0]["outcome"] == "model_context_overflow"
    assert complete.await_count == 1


def test_unknown_profile_and_missing_usage_are_not_zero(directory, monkeypatch):
    assert tokens.profile("gpt-6-luna-snapshot") is None
    assert tokens.cost(None, "gpt-6-luna") is None
    assert tokens.cost({"input_tokens": 100, "output_tokens": 20}, "gpt-6-luna") is None
    result = day08.describe({"turns": [{"cost_usd": 0.1}, {"cost_usd": None}]}, live())
    assert result["total_cost_usd"] is None and result["known_cost_usd"] == 0.1


def test_prices_cache_long_context_and_no_reasoning_double_count():
    usage = {
        "input_tokens": 400_000,
        "output_tokens": 100,
        "input_tokens_details": {"cached_tokens": 100_000, "cache_write_tokens": 50_000},
        "output_tokens_details": {"reasoning_tokens": 80},
    }
    expected = (250000 * 0.1 + 100000 * 0.01 + 50000 * 0.125) * 2 / 1e6 + 100 * 0.5 * 1.5 / 1e6
    assert tokens.cost(usage, "gpt-6-luna") == pytest.approx(expected)
    assert tokens.upper_cost(400000, 100, "gpt-6-luna") >= expected
    assert (
        tokens.cost({**usage, "input_tokens_details": {"cached_tokens": 500_000}}, "gpt-6-luna")
        is None
    )


def test_web_routes_and_day7_isolation(directory, monkeypatch):
    from aiac.tasks.week_02 import day_02

    day7_path = ROOT / ".cache" / "tests" / uuid4().hex
    monkeypatch.setattr(day_02, "STORE_ROOT", day7_path)
    with TestClient(app) as client:
        assert "tokens-form" in client.get("/weeks/2/days/3").text
        assert client.get("/weeks/2/days/3/history").status_code == 200
        assert client.post("/weeks/2/days/3/run", json={"prompt": "test"}).status_code == 503
        assert client.post("/weeks/2/days/3/new", json={}).status_code == 200
        assert client.post("/weeks/2/days/3/probe", json={"kind": "short"}).status_code == 404
        assert (
            client.post(
                "/weeks/2/days/3/run", json={"prompt": "test", "max_cost_usd": 2}
            ).status_code
            == 422
        )
        page = client.get("/weeks/2/days/3").text
        assert "tokens-budget" not in page and "tokens-compare" not in page
        assert "$0.50" in page
    assert not day7_path.exists()


def test_day8_accepts_100x_message_limit_and_rejects_tools(directory, monkeypatch):
    from pydantic import ValidationError

    from aiac.tasks.week_02.day_01 import AgentInput

    prompt = "x" * 400_000
    assert len(day08.ChatInput(prompt=prompt).prompt) == 400_000
    with pytest.raises(ValidationError):
        day08.ChatInput(prompt=prompt + "x")
    with pytest.raises(ValidationError):
        day08.ChatInput(prompt="test", tools=["calculator"])
    with pytest.raises(ValidationError):
        AgentInput(prompt="x" * 4001)
    monkeypatch.setattr(tokens, "count", AsyncMock(side_effect=[100000, 100100]))
    complete = AsyncMock(return_value=reply("Принято"))
    monkeypatch.setattr(reasoning, "complete", complete)
    state = asyncio.run(day08.execute(day08.ChatInput(prompt=prompt), live()))
    assert state["status"] == "completed"
    assert complete.await_args.args[0]["input"][0]["content"] == prompt
    assert not state["tools"]
