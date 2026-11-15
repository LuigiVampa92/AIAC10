import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

from aiac.agents import simple, tools
from aiac.config import Settings
from aiac.llm import reasoning
from aiac.llm.client import ModelError
from aiac.web.app import app


def settings():
    return Settings(
        _env_file=None, aiac_llm_mode="live", aiac_llm_model="test-model", openai_api_key="fake"
    )


def response(output=None, text="", status="completed"):
    return reasoning.ReasoningResult(
        {"output": output or [], "usage": {"total_tokens": 10}}, text, "served-model", status, 0.1
    )


def function(name="calculator", arguments='{"expression":"2+2"}', call_id="c1"):
    return {"type": "function_call", "call_id": call_id, "name": name, "arguments": arguments}


def test_three_scenarios_and_real_function_outputs(monkeypatch):
    cases = [
        ([response(text="Объяснение")], [], "Объяснение"),
        ([response([function()]), response(text="4")], ["calculator"], "4"),
        (
            [
                response(
                    [
                        {"type": "reasoning", "id": "opaque", "encrypted_content": "opaque-data"},
                        function("fixed_exchange", '{"amount":25,"source":"USD","target":"RUB"}'),
                    ]
                ),
                response([function(arguments='{"expression":"2250 + 150"}', call_id="c2")]),
                response(text="2250 RUB по учебному курсу; плюс 150 — 2400 RUB"),
            ],
            ["fixed_exchange", "calculator"],
            "2250 RUB по учебному курсу; плюс 150 — 2400 RUB",
        ),
    ]
    for replies, expected_tools, answer in cases:
        mock = AsyncMock(side_effect=replies)
        monkeypatch.setattr(reasoning, "complete", mock)
        result = asyncio.run(simple.SimpleAgent(settings()).run("Запрос"))
        assert result.status == "completed" and result.text == answer
        assert [tool["name"] for step in result.steps for tool in step["tools"]] == expected_tools
        assert mock.await_count == len(expected_tools) + 1
        assert result.model == "served-model"
        for call in mock.await_args_list:
            payload = call.args[0]
            assert payload["tool_choice"] == "auto" and payload["parallel_tool_calls"] is False
            assert len(payload["tools"]) == 3
            assert payload["store"] is False
        if len(expected_tools) == 2:
            second = mock.await_args_list[1].args[0]["input"]
            assert second[1]["encrypted_content"] == "opaque-data"
            output = next(item for item in second if item.get("type") == "function_call_output")
            assert json.loads(output["output"])["result"]["amount"] == "2250.00"
            third = mock.await_args_list[2].args[0]["input"]
            assert json.loads(third[-1]["output"])["result"]["value"] == "2400"
            # Older snapshots must not change when subsequent steps extend history.
            assert len(second) < len(third)


def test_bad_tool_can_be_corrected_by_model(monkeypatch):
    mock = AsyncMock(
        side_effect=[
            response([function(arguments='{"expression":"1/0"}')]),
            response([function(arguments='{"expression":"2+2"}', call_id="c2")]),
            response(text="4"),
        ]
    )
    monkeypatch.setattr(reasoning, "complete", mock)
    result = asyncio.run(simple.SimpleAgent(settings()).run("Вычисли"))
    assert result.status == "completed"
    assert result.steps[0]["tools"][0]["output"]["ok"] is False
    assert result.steps[1]["tools"][0]["output"]["ok"] is True


def test_tool_loop_is_bounded(monkeypatch):
    mock = AsyncMock(side_effect=[response([function(call_id=f"c{i}")]) for i in range(6)])
    monkeypatch.setattr(reasoning, "complete", mock)
    execute = Mock(wraps=tools.execute_tool)
    monkeypatch.setattr(simple, "execute_tool", execute)
    result = asyncio.run(simple.SimpleAgent(settings()).run("Повторяй"))
    assert result.status == "limit" and mock.await_count == 6 and execute.call_count == 5


@pytest.mark.parametrize("status", ["incomplete", "refusal", "api_error", "empty"])
def test_provider_failure_does_not_execute_tools_or_retry(monkeypatch, status):
    mock = AsyncMock(return_value=response([function()], status=status))
    monkeypatch.setattr(reasoning, "complete", mock)
    execute = Mock()
    monkeypatch.setattr(simple, "execute_tool", execute)
    result = asyncio.run(simple.SimpleAgent(settings()).run("Запрос"))
    assert result.status == status and mock.await_count == 1
    execute.assert_not_called()
    assert result.text == ""


def test_parallel_and_duplicate_calls_are_rejected(monkeypatch):
    for replies in (
        [response([function(), function(call_id="c2")])],
        [response([function()]), response([function()])],
    ):
        mock = AsyncMock(side_effect=replies)
        monkeypatch.setattr(reasoning, "complete", mock)
        result = asyncio.run(simple.SimpleAgent(settings()).run("Запрос"))
        assert result.status == "protocol_error"


def test_total_timeout_keeps_partial_trace(monkeypatch):
    monkeypatch.setattr(simple, "TOTAL_TIMEOUT", 0)

    async def slow(*args, **kwargs):
        await asyncio.sleep(1)

    monkeypatch.setattr(reasoning, "complete", slow)
    result = asyncio.run(simple.SimpleAgent(settings()).run("Запрос"))
    assert result.status == "timeout" and result.text == ""
    assert result.steps[-1]["status"] == "unknown"


def test_local_tools_and_rejected_inputs():
    assert (
        tools.execute_tool("calculator", '{"expression":"127+38+946"}')["result"]["value"] == "1111"
    )
    assert tools.execute_tool("current_time", '{"timezone":"UTC"}')["result"]["timezone"] == "UTC"
    assert (
        tools.execute_tool("fixed_exchange", '{"amount":25,"source":"USD","target":"RUB"}')[
            "result"
        ]["amount"]
        == "2250.00"
    )
    for name, arguments in [
        ("unknown", "{}"),
        ("calculator", '{"expression":"__import__(1)"}'),
        ("calculator", '{"expression":"2**1000000"}'),
        ("calculator", '{"expression":"1/0"}'),
        ("calculator", '{"expression":"1e999"}'),
        ("calculator", '{"expression":"x"}'),
        ("calculator", '{"expression":"2+2","extra":1}'),
        ("calculator", "[1,2]"),
        ("calculator", "x" * 2001),
        ("current_time", '{"timezone":"bad"}'),
        ("fixed_exchange", '{"amount":-1,"source":"USD","target":"RUB"}'),
        ("fixed_exchange", '{"amount":10,"source":"BTC","target":"RUB"}'),
    ]:
        assert tools.execute_tool(name, arguments)["ok"] is False
    for schema in tools.SCHEMAS:
        assert schema["strict"] and schema["parameters"]["additionalProperties"] is False
        assert set(schema["parameters"]["properties"]) == set(schema["parameters"]["required"])


def test_responses_adapter_accepts_tool_only_output(monkeypatch):
    create = AsyncMock(
        return_value=SimpleNamespace(
            model="test",
            status="completed",
            output_text="",
            model_dump=lambda **kwargs: {"output": [function()], "usage": {}},
        )
    )
    client = AsyncMock()
    client.__aenter__.return_value.responses.create = create
    monkeypatch.setattr(reasoning, "AsyncOpenAI", Mock(return_value=client))
    result = asyncio.run(reasoning.complete({"model": "test", "input": "test"}, settings()))
    assert result.status == "completed"


def test_demo_provider_and_blank_guards():
    with pytest.raises(ModelError):
        asyncio.run(simple.SimpleAgent(Settings(_env_file=None)).run("test"))
    with pytest.raises(ValueError):
        asyncio.run(simple.SimpleAgent(settings()).run("   "))
    with pytest.raises(ModelError):
        asyncio.run(
            simple.SimpleAgent(settings().model_copy(update={"aiac_llm_provider": "other"})).run(
                "x"
            )
        )


def test_web_routes_and_safe_rendering(monkeypatch):
    with TestClient(app) as client:
        page = client.get("/weeks/2/days/1")
        assert "agent-form" in page.text and "День 6. Первый агент" in page.text
        assert client.post("/weeks/2/days/1/run", json={"prompt": "test"}).status_code == 503
        assert client.post("/weeks/2/days/1/run", json={"prompt": " "}).status_code == 422
        monkeypatch.setenv("AIAC_LLM_MODE", "live")
        monkeypatch.setenv("AIAC_LLM_MODEL", "test")
        monkeypatch.setenv("OPENAI_API_KEY", "fake")
        monkeypatch.setattr(
            reasoning, "complete", AsyncMock(return_value=response(text="<script>x</script>"))
        )
        result = client.post("/weeks/2/days/1/run", json={"prompt": "Привет"})
        assert result.status_code == 200 and result.json()["text"] == "<script>x</script>"
        assert result.json()["status"] == "completed"


def test_disabled_tool_is_never_executed(monkeypatch):
    mock = AsyncMock(return_value=response([function()]))
    monkeypatch.setattr(reasoning, "complete", mock)
    execute = Mock()
    monkeypatch.setattr(simple, "execute_tool", execute)
    result = asyncio.run(simple.SimpleAgent(settings(), []).run("Запрос"))
    assert result.status == "protocol_error"
    assert mock.await_args.args[0]["tools"] == []
    execute.assert_not_called()


def test_totals_and_control_validation(monkeypatch):
    from aiac.tasks.week_02 import day_01

    text = '{"timestamp": null, "amount_rub": 123}'
    assert day_01.check_json(text) == {"json": True}
    assert day_01.check_json('{"other": "any value"}') == {"json": True}
    for invalid in ("not json", '{"a": NaN}', '{"a": Infinity}', "```json\n{}\n```"):
        assert day_01.check_json(invalid) == {"json": False}
    replies = [
        response([function()]),
        response(text=text),
    ]
    for r in replies:
        r.response["usage"] = {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}
    monkeypatch.setattr(reasoning, "complete", AsyncMock(side_effect=replies))
    result = asyncio.run(
        day_01.execute(day_01.AgentInput(prompt=day_01.CONTROL_PROMPT), settings())
    )
    assert result["usage"]["total_tokens"] == 20
    assert result["usage"]["input_tokens"] == 14
    assert result["tool_calls"] == 1 and len(result["steps"]) == 2
    assert result["check"] == {"json": True}
    monkeypatch.setattr(reasoning, "complete", AsyncMock(return_value=response(text="ok")))
    result = asyncio.run(day_01.execute(day_01.AgentInput(prompt="other", tools=[]), settings()))
    assert result["usage"]["input_tokens"] is None and result["check"] == {"json": False}


@pytest.mark.parametrize("expression", ["1-2", "1*2", "1/2", "-1+2"])
def test_calculator_only_adds(expression):
    assert not tools.execute_tool("calculator", json.dumps({"expression": expression}))["ok"]


@pytest.mark.parametrize("item_status", [None, "completed"])
def test_replay_removes_status_without_losing_tool_or_reasoning_data(monkeypatch, item_status):
    opaque = {
        "type": "reasoning",
        "id": "rs_1",
        "summary": [],
        "encrypted_content": "opaque-data",
        "status": item_status,
    }
    call = {**function(), "id": "fc_1", "status": item_status}
    replies = [response([opaque, call]), response(text="4")]
    payloads = []

    async def strict_api(payload, settings, **kwargs):
        # Reproduce the actual API rejection on the second request.
        for item in payload["input"]:
            assert "status" not in item, "Unknown parameter: input[1].status"
        payloads.append(payload)
        return replies[len(payloads) - 1]

    monkeypatch.setattr(reasoning, "complete", strict_api)
    result = asyncio.run(simple.SimpleAgent(settings()).run("Сложи 2 и 2"))
    assert result.status == "completed" and len(payloads) == 2
    replay = payloads[1]["input"]
    assert replay[1] == {k: v for k, v in opaque.items() if k != "status"}
    assert replay[2] == {k: v for k, v in call.items() if k != "status"}
    assert replay[3]["call_id"] == call["call_id"]
    assert json.loads(replay[3]["output"])["result"]["value"] == "4"
    assert result.history[1]["status"] == item_status
    assert result.steps[0]["response"]["output"] == [opaque, call]
    assert payloads[0]["input"] == [{"role": "user", "content": "Сложи 2 и 2"}]


@pytest.mark.parametrize("clock_enabled", [True, False])
def test_time_uses_local_tool_or_model_answer(monkeypatch, clock_enabled):
    local_clock = Mock(return_value={"timezone": "UTC", "time": "2030-01-02T03:04:05+00:00"})
    monkeypatch.setattr(tools, "current_time", local_clock)
    text = (
        '{"timestamp":"2030-01-02T03:04:05Z"}'
        if clock_enabled
        else '{"timestamp":"2030-01-02T03:00:00Z"}'
    )
    replies = [response(text=text)]
    if clock_enabled:
        replies.insert(0, response([function("current_time", '{"timezone":"UTC"}')]))
    complete = AsyncMock(side_effect=replies)
    monkeypatch.setattr(reasoning, "complete", complete)
    agent = simple.SimpleAgent(settings(), ["current_time"] if clock_enabled else [])
    result = asyncio.run(agent.run("Сколько сейчас времени?"))
    assert result.status == "completed" and result.text == text
    assert local_clock.call_count == int(clock_enabled)
    assert complete.await_count == (2 if clock_enabled else 1)
    first = complete.await_args_list[0].args[0]
    assert [schema["name"] for schema in first["tools"]] == (
        ["current_time"] if clock_enabled else []
    )
    assert "YYYY-MM-DDTHH:mm:ssZ" in first["instructions"]
    if clock_enabled:
        assert "обязательно получи его" in first["instructions"]
        tool_output = complete.await_args_list[1].args[0]["input"][-1]
        assert (
            json.loads(tool_output["output"])["result"]["time"] == local_clock.return_value["time"]
        )
    else:
        assert "ответь самостоятельно" in first["instructions"]
        assert "приблизительную" in first["instructions"]
