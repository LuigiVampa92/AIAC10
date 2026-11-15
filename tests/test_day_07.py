import asyncio
import json
import shutil
import subprocess
import sys
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from aiac.agents import simple
from aiac.agents.storage import Conversation, HistoryError, JsonHistory
from aiac.config import ROOT, Settings
from aiac.llm import reasoning
from aiac.llm.client import ModelError
from aiac.tasks.week_02 import day_02 as day07
from aiac.tasks.week_02.day_01 import AgentInput
from aiac.web.app import app


@pytest.fixture
def directory(monkeypatch):
    path = ROOT / ".cache" / "tests" / uuid4().hex
    path.mkdir(parents=True)
    monkeypatch.setattr(day07, "STORE_ROOT", path)
    yield path
    shutil.rmtree(path)


def live():
    return Settings(
        _env_file=None, aiac_llm_mode="live", aiac_llm_model="test", openai_api_key="fake"
    )


def reply(text="", output=None, status="completed"):
    return reasoning.ReasoningResult(
        {
            "output": output or [],
            "usage": {"input_tokens": 8, "output_tokens": 2, "total_tokens": 10},
        },
        text,
        "served",
        status,
        0.1,
    )


def call():
    return {
        "type": "function_call",
        "id": "fc_1",
        "call_id": "c1",
        "name": "fixed_exchange",
        "arguments": '{"amount":25,"source":"USD","target":"RUB"}',
        "status": "completed",
    }


def test_restores_all_context_and_actual_tool_results(directory, monkeypatch):
    opaque = {"type": "reasoning", "id": "rs1", "summary": [], "encrypted_content": "opaque"}
    final = {
        "type": "message",
        "id": "m1",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": "2250 RUB", "annotations": []}],
    }
    complete = AsyncMock(side_effect=[reply(output=[opaque, call()]), reply("2250 RUB", [final])])
    monkeypatch.setattr(reasoning, "complete", complete)
    first = asyncio.run(day07.execute(AgentInput(prompt="Я Павел. Конвертируй 25 USD."), live()))
    assert first["status"] == "completed"
    assert first["turns"][0]["tools"][0]["output"]["result"]["amount"] == "2250.00"
    assert first["turns"][0]["usage"]["total_tokens"] == 20
    disk = JsonHistory(directory).load()
    assert disk.history[1] == opaque
    assert disk.history[2]["status"] == "completed"
    assert disk.history[3]["call_id"] == "c1"
    assert disk.history[4] == final
    complete = AsyncMock(return_value=reply("Павел, 2250 RUB"))
    monkeypatch.setattr(reasoning, "complete", complete)
    second = asyncio.run(
        day07.execute(AgentInput(prompt="Как меня зовут и сколько получилось?"), live())
    )
    sent = complete.await_args.args[0]["input"]
    assert sent[:-1] == simple.replay_input(disk.history)
    assert second["id"] == first["id"] and len(second["turns"]) == 2
    assert "fake" not in (directory / "conversation.json").read_text()
    assert (directory / "conversation.json").stat().st_mode & 0o077 == 0


def test_actual_process_restart(directory):
    # Two separate Python processes: no retained module state or browser storage.
    script = """
import asyncio, json, sys
from pathlib import Path
from aiac.config import Settings
from aiac.llm import reasoning
from aiac.tasks.week_02 import day_02 as day
from aiac.tasks.week_02.day_01 import AgentInput
day.STORE_ROOT = Path(sys.argv[1])
phase = sys.argv[2]
calls = 0
async def model(payload, settings, **kwargs):
    global calls
    calls += 1
    if phase == "write" and calls == 1:
        output = [{"type":"function_call","call_id":"c1","name":"calculator",
                   "arguments":'{"expression":"20+22"}'}]
        text = ""
    else:
        if phase == "read":
            history = payload["input"]
            assert history[0]["content"] == "Я Павел. Сложи 20+22."
            assert any(i.get("type") == "function_call" for i in history)
            assert any(i.get("type") == "function_call_output" and
                       json.loads(i["output"])["result"]["value"] == "42" for i in history)
            assert any(i.get("role") == "assistant" and i["content"] == "42" for i in history)
        output, text = [], "42" if phase == "write" else "Павел, 42"
    return reasoning.ReasoningResult({"output":output,"usage":{}},text,"served","completed",0.01)
reasoning.complete = model
settings = Settings(_env_file=None,aiac_llm_mode="live",aiac_llm_model="test",openai_api_key="fake")
prompt = "Я Павел. Сложи 20+22." if phase=="write" else "Напомни имя и сумму"
result = asyncio.run(day.execute(AgentInput(prompt=prompt),settings))
assert result["status"] == "completed"
print(len(result["turns"]))
"""
    for phase, expected in [("write", "1"), ("read", "2")]:
        result = subprocess.run(
            [sys.executable, "-c", script, str(directory), phase],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        assert result.stdout.strip() == expected


def test_interrupted_turn_is_saved_and_not_replayed(directory, monkeypatch):
    async def interrupted(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(reasoning, "complete", interrupted)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(day07.execute(AgentInput(prompt="Сложи 1+2"), live()))
    state = JsonHistory(directory).load()
    assert state.status == "running" and state.history[0]["content"] == "Сложи 1+2"
    complete = AsyncMock()
    monkeypatch.setattr(reasoning, "complete", complete)
    with pytest.raises(HistoryError, match="не завершён"):
        asyncio.run(day07.execute(AgentInput(prompt="продолжай"), live()))
    complete.assert_not_called()
    new = day07.new()
    assert not new["history"] and new["id"] != state.id
    assert JsonHistory(directory).path(f"archive-{state.id}.json").exists()


def test_lock_corruption_and_atomic_save(directory):
    store = JsonHistory(directory)
    with store.lock():
        with pytest.raises(HistoryError, match="занят"):
            with JsonHistory(directory).lock():
                pass
    state = Conversation()
    store.save(state)
    assert store.load() == state
    path = directory / "conversation.json"
    path.write_text("{broken")
    with pytest.raises(HistoryError):
        store.load()
    with pytest.raises(HistoryError):
        day07.new()
    assert path.read_text() == "{broken"


def test_model_change_demo_and_blank_do_not_change_history(directory, monkeypatch):
    monkeypatch.setattr(reasoning, "complete", AsyncMock(return_value=reply("Ответ")))
    asyncio.run(day07.execute(AgentInput(prompt="Привет"), live()))
    before = (directory / "conversation.json").read_bytes()
    with pytest.raises(HistoryError, match="Модель"):
        asyncio.run(
            day07.execute(
                AgentInput(prompt="x"), live().model_copy(update={"aiac_llm_model": "other"})
            )
        )
    with pytest.raises(ModelError):
        asyncio.run(day07.execute(AgentInput(prompt="x"), Settings(_env_file=None)))
    with pytest.raises(ValueError):
        asyncio.run(day07.execute(AgentInput(prompt=" "), live()))
    assert (directory / "conversation.json").read_bytes() == before


@pytest.mark.parametrize("status", ["api_error", "incomplete", "refusal"])
def test_failed_turn_is_not_success_or_retried(directory, monkeypatch, status):
    complete = AsyncMock(return_value=reply(status=status))
    monkeypatch.setattr(reasoning, "complete", complete)
    state = asyncio.run(day07.execute(AgentInput(prompt="x"), live()))
    assert state["status"] == status and complete.await_count == 1
    assert JsonHistory(directory).load().status == status


def test_web_history_and_new_are_offline(directory, monkeypatch):
    with TestClient(app) as client:
        assert "chat-form" in client.get("/weeks/2/days/2").text
        history = client.get("/weeks/2/days/2/history")
        assert history.status_code == 200 and history.headers["cache-control"] == "no-store"
        assert client.post("/weeks/2/days/2/run", json={"prompt": "Привет"}).status_code == 503
        assert not (directory / "conversation.json").exists()
        assert client.post("/weeks/2/days/2/new", json={}).status_code == 200
        monkeypatch.setenv("AIAC_LLM_MODE", "live")
        monkeypatch.setenv("AIAC_LLM_MODEL", "test")
        monkeypatch.setenv("OPENAI_API_KEY", "fake")
        monkeypatch.setattr(
            reasoning, "complete", AsyncMock(return_value=reply("<script>x</script>"))
        )
        response = client.post("/weeks/2/days/2/run", json={"prompt": "Привет"})
        assert response.status_code == 200
        assert response.json()["turns"][0]["text"] == "<script>x</script>"
        assert client.get("/weeks/2/days/2/history").json()["history"]
        assert client.post("/weeks/2/days/2/run", json={"prompt": " "}).status_code == 422


def test_failed_atomic_replace_keeps_previous_file(directory, monkeypatch):
    import aiac.agents.storage as storage

    store = JsonHistory(directory)
    old = Conversation()
    store.save(old)
    before = (directory / "conversation.json").read_bytes()

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(storage.os, "replace", fail)
    with pytest.raises(HistoryError, match="сохранить"):
        store.save(Conversation(status="running"))
    assert (directory / "conversation.json").read_bytes() == before
    assert list(directory.iterdir()) == [directory / "conversation.json"]


def test_interruption_after_tool_keeps_call_and_result(directory, monkeypatch):
    count = 0

    async def interrupted_after_tool(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 1:
            return reply(output=[call()])
        raise asyncio.CancelledError()

    monkeypatch.setattr(reasoning, "complete", interrupted_after_tool)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(day07.execute(AgentInput(prompt="Конвертируй 25 USD"), live()))
    saved = JsonHistory(directory).load()
    assert saved.status == "running"
    assert saved.history[1]["call_id"] == saved.history[2]["call_id"] == "c1"
    assert json.loads(saved.history[2]["output"])["result"]["amount"] == "2250.00"
