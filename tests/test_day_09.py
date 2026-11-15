import asyncio
import json
import shutil
from copy import deepcopy
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from aiac.agents.compression import (
    SummaryConversation,
    active_context,
    compression_end,
    messages,
)
from aiac.agents.storage import HistoryError
from aiac.config import ROOT, Settings
from aiac.llm import reasoning, tokens
from aiac.tasks.week_02 import day_04 as day09
from aiac.web.app import app


@pytest.fixture
def directory(monkeypatch):
    path = ROOT / ".cache" / "tests" / uuid4().hex
    path.mkdir(parents=True)
    monkeypatch.setattr(day09, "STORE_ROOT", path)
    yield path
    shutil.rmtree(path)


def live():
    return Settings(
        _env_file=None,
        aiac_llm_mode="live",
        aiac_llm_model="gpt-6-luna",
        openai_api_key="fake",
    )


def reply(text="Запомнил.", status="completed"):
    return reasoning.ReasoningResult(
        {
            "output": [
                {"type": "reasoning", "id": "rs", "encrypted_content": "opaque"},
                {
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "id": "m",
                    "content": [{"type": "output_text", "text": text, "annotations": []}],
                },
            ],
            "usage": {
                "input_tokens": 200,
                "output_tokens": 40,
                "total_tokens": 240,
                "input_tokens_details": {"cached_tokens": 0},
            },
        },
        text,
        "gpt-6-luna",
        status,
        0.01,
    )


@pytest.fixture
def model(monkeypatch):
    async def count(payload, settings):
        return len(json.dumps(payload, ensure_ascii=False)) // 4 + 10

    expected = {f["id"]: f["expected"] for f in day09.scenario()["facts"]}

    async def complete(payload, settings, **kwargs):
        if payload["instructions"] == day09.SUMMARY_INSTRUCTIONS:
            return reply(
                "Север. Марина. Срок 20.12.2026. Бюджет 42000. "
                "Только локально. FastAPI. Тёмная тема. Без регистрации."
            )
        prompt = payload["input"][-1].get("content", "")
        if isinstance(prompt, str) and prompt.startswith("Ответь по прошлому"):
            return reply(json.dumps(expected, ensure_ascii=False))
        return reply()

    api = AsyncMock(side_effect=complete)
    monkeypatch.setattr(tokens, "count", count)
    monkeypatch.setattr(reasoning, "complete", api)
    return api


def test_compression_boundary_retains_exact_messages():
    history = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": str(i)} for i in range(16)
    ]
    state = SummaryConversation(history=deepcopy(history))
    assert compression_end(state) == 10
    state.history.pop()
    assert compression_end(state) == 0
    assert compression_end(state, force=True) == 9
    state.history = deepcopy(history)
    state.summary, state.summarized_messages = "Ранняя история", 10
    before = deepcopy(state.history)
    active = active_context(state)
    assert active[1:] == history[-6:]
    active[-1]["content"] = "changed"
    assert state.history == before


def test_incremental_summary_uses_only_previous_summary_and_new_prefix(directory, model):
    config = day09.MemorySettings(prompt="Начало", keep_last=2, compress_every=2)
    for i in range(3):
        state = asyncio.run(
            day09.execute(config.model_copy(update={"prompt": f"Факт {i}"}), live())
        )
    assert state["summarized_messages"] == 2
    assert len(state["compression_log"]) == 1
    source = json.loads(
        next(
            c.args[0]
            for c in model.await_args_list
            if c.args[0]["instructions"] == day09.SUMMARY_INSTRUCTIONS
        )["input"][-1]["content"]
    )
    assert source["previous_summary"] == ""
    assert (
        source["new_messages"]
        == [m for m in state["history"] if m.get("role") in ("user", "assistant")][:2]
    )
    old_summary = state["summary"]
    old_archive = deepcopy(state["history"])
    state = asyncio.run(
        day09.execute(config.model_copy(update={"prompt": "Следующий факт"}), live())
    )
    summary_requests = [
        c.args[0]
        for c in model.await_args_list
        if c.args[0]["instructions"] == day09.SUMMARY_INSTRUCTIONS
    ]
    source = json.loads(summary_requests[-1]["input"][-1]["content"])
    assert source["previous_summary"] == old_summary
    prior_state = day09.load(day09.store())
    assert source["new_messages"] == messages(prior_state)[2:4]
    assert state["history"][: len(old_archive)] == old_archive
    assert state["summarized_messages"] == 4
    assert state["turns"][-1]["resources"]["api_calls"] == 2
    assert state["turns"][-1]["uncompressed_input_tokens"] > 0
    assert day09.read()["summary"] == state["summary"]


def test_summary_failure_keeps_archive_and_does_not_commit_cursor(directory, model, monkeypatch):
    for text in ["one", "two"]:
        asyncio.run(
            day09.execute(day09.MemorySettings(prompt=text, keep_last=2, compress_every=2), live())
        )
    old = day09.read()["history"]
    monkeypatch.setattr(
        reasoning, "complete", AsyncMock(return_value=reply("часть", status="incomplete"))
    )
    state = asyncio.run(
        day09.execute(day09.MemorySettings(prompt="three", keep_last=2, compress_every=2), live())
    )
    assert state["status"] == "incomplete" and state["summary"] == ""
    assert state["history"] == old and state["summarized_messages"] == 0
    assert state["pending_prompt"] == "three"
    with pytest.raises(HistoryError, match="не завершена"):
        asyncio.run(day09.execute(day09.MemorySettings(prompt="retry"), live()))


def test_full_experiment_uses_shared_real_transcript_and_keeps_oracle_out(directory, model):
    started = day09.start_experiment(day09.SettingsInput())
    assert model.await_count == 0
    for _ in range(15):
        state = asyncio.run(day09.experiment_step(live()))
    exp = state["experiment"]
    assert exp["phase"] == "finished" and exp["index"] == 12
    assert model.await_count == 15
    assert state["message_count"] == 24
    assert exp["results"]["full_recall"]["evaluation"]["remembered"] == 8
    assert exp["results"]["compressed_recall"]["evaluation"]["remembered"] == 8
    full, compressed = exp["results"]["full_recall"], exp["results"]["compressed_recall"]
    assert full["sent_input_tokens"] == compressed["uncompressed_input_tokens"]
    assert compressed["sent_input_tokens"] < full["sent_input_tokens"]
    assert exp["full_test_resources"]["api_calls"] == 1
    assert exp["compressed_test_resources"]["api_calls"] == 2
    assert exp["resources"]["api_calls"] == 15
    assert exp["resources"]["total_tokens"] == 15 * 240
    assert state["id"] == started["id"] and len(state["compression_log"]) == 1
    assert state["active_context"][1:] == messages(day09.load(day09.store(experiment=True)))[-6:]
    assert not day09.store().path("conversation.json").exists()
    assert "42000" not in day09.recall_prompt() and "Марина" not in day09.recall_prompt()
    count = model.await_count
    asyncio.run(day09.experiment_step(live()))
    assert model.await_count == count
    # No recall question/answer was inserted into the source of summary.
    prompt = json.loads(model.await_args_list[13].args[0]["input"][-1]["content"])
    assert set(prompt) == {"previous_summary", "new_messages"}
    assert len(prompt["new_messages"]) == 18
    assert all(
        not str(m.get("content", "")).startswith("Ответь по прошлому")
        for m in prompt["new_messages"]
    )


def test_experiment_restart_resumes_only_explicit_steps(directory, model):
    day09.start_experiment(day09.SettingsInput())
    asyncio.run(day09.experiment_step(live()))
    restored = day09.experiment_read()
    assert restored["experiment"]["index"] == 1
    assert model.await_count == 1
    asyncio.run(day09.experiment_step(live()))
    assert model.await_count == 2 and day09.experiment_read()["experiment"]["index"] == 2


def test_interrupted_operation_is_durable_and_never_retried(directory, model, monkeypatch):
    async def interrupted(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(reasoning, "complete", interrupted)
    day09.start_experiment(day09.SettingsInput())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(day09.experiment_step(live()))
    assert day09.experiment_read()["status"] == "running"
    assert day09.experiment_read()["last_operation"]["history"]
    with pytest.raises(HistoryError):
        asyncio.run(day09.experiment_step(live()))
    old = day09.experiment_read()["id"]
    day09.start_experiment(day09.SettingsInput())
    assert day09.store(experiment=True).path(f"archive-{old}.json").exists()


@pytest.mark.parametrize("bad", [0, 1, 3, 22])
def test_settings_reject_invalid_message_boundaries(bad):
    with pytest.raises(ValidationError):
        day09.SettingsInput(keep_last=bad)


def test_grading_is_conservative_and_not_format_equals_memory():
    assert day09.evaluate("not json")["remembered"] is None
    assert day09.evaluate('{"budget":NaN}')["remembered"] is None
    assert day09.evaluate("{}")["remembered"] == 0
    assert day09.evaluate('{"budget":42000.0,"owner":"Марина"}')["remembered"] == 2
    assert day09.evaluate('{"budget":-42000,"owner":["Марина"]}')["remembered"] == 0
    data = day09.resources(
        [{"usage": {"total_tokens": 10}, "cost_usd": 0.1}, {"usage": {}, "cost_usd": None}]
    )
    assert data["total_tokens"] is None and data["cost_usd"] is None
    assert data["known_cost_usd"] == 0.1


def test_settings_change_blank_and_model_guard_do_not_mutate_archive(directory, model):
    asyncio.run(day09.execute(day09.MemorySettings(prompt="Привет"), live()))
    before = day09.store().path("conversation.json").read_bytes()
    with pytest.raises(HistoryError):
        asyncio.run(day09.execute(day09.MemorySettings(prompt="x", keep_last=10), live()))
    with pytest.raises(ValueError):
        asyncio.run(day09.execute(day09.MemorySettings(prompt=" "), live()))
    assert day09.store().path("conversation.json").read_bytes() == before


def test_web_isolation_and_offline_load(directory):
    with TestClient(app) as client:
        page = client.get("/weeks/2/days/4")
        assert page.status_code == 200 and "memory-form" in page.text
        assert client.get("/weeks/2/days/4/history").status_code == 200
        assert client.get("/weeks/2/days/4/comparison").status_code == 200
        assert client.post("/weeks/2/days/4/run", json={"prompt": "Hi"}).status_code == 503
        assert not day09.store().path("conversation.json").exists()
        assert client.post("/weeks/2/days/4/new", json={}).status_code == 200
        assert client.post("/weeks/2/days/4/comparison/start", json={}).status_code == 200
        assert client.post("/weeks/2/days/4/comparison/step", json={}).status_code == 503


def test_shared_experiment_budget_stops_before_next_paid_call(directory, model, monkeypatch):
    monkeypatch.setattr(tokens, "upper_cost", lambda *args: 0.3)
    day09.start_experiment(day09.SettingsInput())
    asyncio.run(day09.experiment_step(live()))
    state = asyncio.run(day09.experiment_step(live()))
    assert model.await_count == 1
    assert state["status"] == "configuration_error"
    assert state["experiment"]["resources"]["reserved_usd"] == pytest.approx(0.3)
    assert state["experiment"]["resources"]["api_calls"] == 1
    with pytest.raises(HistoryError):
        asyncio.run(day09.experiment_step(live()))
