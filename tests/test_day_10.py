import asyncio
import json
import re
import shutil
from copy import deepcopy
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from aiac.agents.branches import Workspace, checkpoint, fork_pair
from aiac.agents.compression import messages
from aiac.agents.storage import HistoryError
from aiac.agents.strategies import FACTS_INSTRUCTIONS, StrategyConversation, parse_facts
from aiac.config import ROOT, Settings
from aiac.llm import reasoning, tokens
from aiac.tasks.week_02 import day_03 as day08
from aiac.tasks.week_02 import day_04 as day09
from aiac.tasks.week_02 import day_05 as day10
from aiac.web.app import app


@pytest.fixture
def directory(monkeypatch):
    path = ROOT / ".cache" / "tests" / uuid4().hex
    path.mkdir(parents=True)
    monkeypatch.setattr(day10, "STORE_ROOT", path)
    yield path
    shutil.rmtree(path)


def live():
    return Settings(
        _env_file=None, aiac_llm_mode="live", aiac_llm_model="gpt-6-luna", openai_api_key="fake"
    )


def reply(text="Запомнил.", status="completed"):
    return reasoning.ReasoningResult(
        {
            "output": [],
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
    expected = {f["id"]: f["expected"] for f in day09.scenario()["facts"]}

    async def count(payload, settings):
        return len(json.dumps(payload, ensure_ascii=False)) // 4 + 10

    async def complete(payload, settings, **kwargs):
        prompt = payload["input"][-1].get("content", "")
        if payload["instructions"] == FACTS_INSTRUCTIONS:
            data = json.loads(prompt)
            facts = deepcopy(data["previous_facts"])
            new = data["new_user_message"]
            if new.startswith("Ответь по прошлому"):
                return reply(json.dumps(facts, ensure_ascii=False))
            if "Марина" in new:
                facts.update(owner="Марина", project="Север")
            if "20 декабря" in new:
                facts["deadline"] = "2026-12-20"
            if "бюджет" in new.lower():
                found = re.findall(r"\b\d{5}\b", new)
                if found:
                    facts["budget"] = found[-1]
            for marker, key in [
                ("хранени", "storage"),
                ("FastAPI", "backend"),
                ("тёмная", "theme"),
                ("регистрац", "registration"),
            ]:
                if marker.lower() in new.lower():
                    facts[key] = expected[key]
            return reply(json.dumps(facts, ensure_ascii=False))
        if payload["instructions"] == day09.SUMMARY_INSTRUCTIONS:
            return reply(
                "Север. Марина. 20.12.2026. Бюджет 42000. "
                "Локально. FastAPI. Тёмная. Без регистрации."
            )
        if isinstance(prompt, str) and prompt.startswith("Ответь по прошлому"):
            remembered = any(
                isinstance(i.get("content"), str)
                and i["content"].startswith(("Память facts", "Сводка раннего"))
                for i in payload["input"]
            )
            return reply(
                json.dumps(
                    expected if remembered else {k: None for k in expected}, ensure_ascii=False
                )
            )
        return reply()

    api = AsyncMock(side_effect=complete)
    monkeypatch.setattr(tokens, "count", count)
    monkeypatch.setattr(reasoning, "complete", api)
    return api


def test_window_discards_old_working_messages_but_never_reads_audit(directory, model):
    day10.new(day10.Options(strategy="window", keep_last=2))
    for prompt in ["SECRET_EARLY", "second", "third"]:
        state = asyncio.run(day10.execute(day08.ChatInput(prompt=prompt), live()))
    assert len(state["current"]["history"]) == 2
    assert len(state["current"]["turns"]) == 3
    assert state["current"]["turns"][0]["prompt"] == "SECRET_EARLY"
    last = model.await_args.args[0]
    assert "SECRET_EARLY" not in json.dumps(last["input"])
    assert "tools" not in last
    assert model.await_count == 3


def test_facts_updated_before_each_answer_and_overwritten(directory, model):
    day10.new(day10.Options(strategy="facts", keep_last=2))
    for text in ["Руководитель Марина. Бюджет 35000", "Окончательный бюджет 42000"]:
        state = asyncio.run(day10.execute(day08.ChatInput(prompt=text), live()))
    branch = state["current"]
    assert branch["facts"]["budget"] == "42000" and branch["facts"]["owner"] == "Марина"
    assert len(branch["facts_log"]) == 2 and model.await_count == 4
    assert len(branch["history"]) == 2
    assert branch["turns"][-1]["resources"]["api_calls"] == 2
    facts_request = json.loads(model.await_args_list[2].args[0]["input"][-1]["content"])
    assert set(facts_request) == {"previous_facts", "recent_messages", "new_user_message"}
    assert facts_request["previous_facts"]["budget"] == "35000"
    assert "42000" in model.await_args_list[3].args[0]["input"][0]["content"]


def test_invalid_facts_keeps_previous_memory_and_skips_answer(directory, model, monkeypatch):
    day10.new(day10.Options(strategy="facts"))
    asyncio.run(day10.execute(day08.ChatInput(prompt="Бюджет 42000"), live()))
    old = day10.read()["current"]["facts"]
    api = AsyncMock(return_value=reply("not json"))
    monkeypatch.setattr(reasoning, "complete", api)
    state = asyncio.run(day10.execute(day08.ChatInput(prompt="Новый бюджет"), live()))
    assert state["current"]["status"] == "invalid_facts"
    assert state["current"]["facts"] == old and api.await_count == 1
    with pytest.raises(HistoryError):
        asyncio.run(day10.execute(day08.ChatInput(prompt="retry"), live()))


def test_checkpoint_deep_copy_covers_facts_summary_cursor_and_messages():
    branch = StrategyConversation(
        strategy="summary",
        facts={"budget": "42000"},
        summary="Old summary",
        summarized_messages=2,
        history=[
            {"role": "user" if i % 2 == 0 else "assistant", "content": str(i)} for i in range(4)
        ],
    )
    workspace = Workspace(branches={"main": branch})
    key = checkpoint(workspace, "Общее начало")
    a, b = fork_pair(workspace, key)
    workspace.branches[a].facts["budget"] = "50000"
    workspace.branches[a].summary = "New summary"
    workspace.branches[a].summarized_messages = 4
    workspace.branches[a].history[-1]["content"] = "changed"
    for stable in [
        workspace.branches[b],
        workspace.branches["main"],
        workspace.checkpoints[key].state,
    ]:
        assert stable.facts["budget"] == "42000" and stable.summary == "Old summary"
        assert stable.summarized_messages == 2 and stable.history[-1]["content"] == "3"


def test_two_branches_continue_independently_and_restore(directory, model):
    day10.new(day10.Options(strategy="facts"))
    asyncio.run(day10.execute(day08.ChatInput(prompt="Бюджет 42000"), live()))
    cp = day10.save_checkpoint(day10.CheckpointInput(name="Начало"))
    key = next(iter(cp["checkpoints"]))
    forked = day10.fork(day10.ForkInput(checkpoint_id=key))
    a = forked["active_branch"]
    b = next(k for k, v in forked["branches"].items() if v["checkpoint_id"] == key and k != a)
    asyncio.run(day10.execute(day08.ChatInput(prompt="Бюджет 50000"), live()))
    assert day10.switch(day10.BranchInput(branch_id=b))["current"]["facts"]["budget"] == "42000"
    asyncio.run(day10.execute(day08.ChatInput(prompt="Бюджет 30000"), live()))
    restored = day10.read()
    assert restored["branches"][a]["facts"]["budget"] == "50000"
    assert restored["branches"][b]["facts"]["budget"] == "30000"
    assert restored["checkpoints"][key]["state"]["facts"]["budget"] == "42000"
    assert restored["branches"]["main"]["facts"]["budget"] == "42000"


def test_strategy_switch_creates_new_branch_and_never_resurrects_old_audit(directory, model):
    day10.new(day10.Options(strategy="window", keep_last=2))
    for text in ["SECRET", "second"]:
        asyncio.run(day10.execute(day08.ChatInput(prompt=text), live()))
    old = day10.read()
    changed = day10.change_strategy(day10.Options(strategy="facts", keep_last=6))
    assert changed["active_branch"] != "main"
    assert changed["branches"]["main"] == old["current"]
    assert len(changed["active_context"]) == 3
    assert "SECRET" not in json.dumps(changed["active_context"])
    assert changed["current"]["facts"] == {} and changed["current"]["summary"] == ""


def test_same_scenario_three_strategies_full_maintenance_costs(directory, model):
    day10.comparison_start(day10.Options())
    assert model.await_count == 0
    for _ in range(50):
        state = asyncio.run(day10.comparison_step(live()))
        if state["phase"] == "finished":
            break
    assert state["phase"] == "finished"
    assert model.await_count == 30
    assert len(messages(day10.load_comparison(day10.comparison_store()))) == 24
    assert state["results"]["window"]["evaluation"]["remembered"] == 0
    assert state["results"]["facts"]["evaluation"]["remembered"] == 8
    assert state["results"]["summary"]["evaluation"]["remembered"] == 8
    assert state["results"]["window"]["maintenance_calls"] == 0
    assert state["results"]["facts"]["maintenance_calls"] == 13
    assert state["results"]["summary"]["maintenance_calls"] == 2
    assert state["resources"]["api_calls"] == 30
    assert state["resources"]["total_tokens"] == 30 * 240
    assert sum(op["strategy"] == "common" for op in state["operations"]) == 12
    assert (
        len(state["states"]["window"]["history"]) == len(state["states"]["facts"]["history"]) == 6
    )
    assert len(state["states"]["summary"]["history"]) == 24
    assert state["states"]["summary"]["summarized_messages"] == 18
    assert len(state["states"]["facts"]["facts_log"]) == 13
    assert all(state["complexity"][k]["nonblank_lines"] > 0 for k in ["window", "facts", "summary"])
    recalls = [
        c.args[0]
        for c in model.await_args_list
        if c.args[0]["input"][-1].get("content", "").startswith("Ответь по прошлому")
    ]
    assert len(recalls) == 3 and len({p["input"][-1]["content"] for p in recalls}) == 1
    assert "42000" not in json.dumps(recalls[0]["input"], ensure_ascii=False)
    assert "Марина" not in json.dumps(recalls[0]["input"], ensure_ascii=False)
    assert all("tools" not in c.args[0] for c in model.await_args_list)
    assert not day10.workspace_store().path("conversation.json").exists()
    count = model.await_count
    asyncio.run(day10.comparison_step(live()))
    assert model.await_count == count


def test_interrupted_comparison_is_not_retried(directory, model, monkeypatch):
    async def interrupted(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(reasoning, "complete", interrupted)
    first = day10.comparison_start(day10.Options())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(day10.comparison_step(live()))
    assert day10.comparison_read()["status"] == "running"
    with pytest.raises(HistoryError):
        asyncio.run(day10.comparison_step(live()))
    day10.comparison_start(day10.Options())
    assert day10.comparison_store().path("archive-" + first["id"] + ".json").exists()


def test_budget_and_branch_limits(directory, model, monkeypatch):
    monkeypatch.setattr(tokens, "upper_cost", lambda *args: 0.3)
    day10.comparison_start(day10.Options())
    asyncio.run(day10.comparison_step(live()))
    state = asyncio.run(day10.comparison_step(live()))
    assert model.await_count == 1 and state["status"] == "configuration_error"
    assert state["resources"]["reserved_usd"] == pytest.approx(0.3)
    workspace = Workspace()
    key = checkpoint(workspace, "test")
    for _ in range(3):
        fork_pair(workspace, key)
    assert len(workspace.branches) == 7
    with pytest.raises(HistoryError):
        fork_pair(workspace, key)


@pytest.mark.parametrize(
    "bad", ["[]", '{"x":1}', '{"x":null}', '{"x":{"nested":"value"}}', "```json\n{}\n```"]
)
def test_bad_facts_are_not_silently_accepted(bad):
    with pytest.raises(ValueError):
        parse_facts(bad)


def test_web_operations_offline_and_typed_requests(directory):
    with TestClient(app) as client:
        assert "strategy-form" in client.get("/weeks/2/days/5").text
        assert client.get("/weeks/2/days/5/history").status_code == 200
        assert client.get("/weeks/2/days/5/comparison").status_code == 200
        assert client.post("/weeks/2/days/5/new", json={"strategy": "facts"}).status_code == 200
        saved = client.post("/weeks/2/days/5/checkpoint", json={"name": "one"}).json()
        key = next(iter(saved["checkpoints"]))
        assert client.post("/weeks/2/days/5/fork", json={"checkpoint_id": key}).status_code == 200
        assert client.post("/weeks/2/days/5/run", json={"prompt": "Hi"}).status_code == 503
        assert (
            client.post("/weeks/2/days/5/strategy", json={"strategy": "invalid"}).status_code == 422
        )
        assert (
            client.post("/weeks/2/days/5/branch", json={"branch_id": "missing"}).status_code == 409
        )
        assert client.post("/weeks/2/days/5/new", json={"max_cost_usd": 2}).status_code == 422
        assert client.post("/weeks/2/days/5/comparison/start", json={}).status_code == 200
        assert client.post("/weeks/2/days/5/comparison/step", json={}).status_code == 503


def test_frequent_summary_is_counted_in_maximum_calls(directory, model):
    day10.comparison_start(day10.Options(keep_last=2, compress_every=2))
    for _ in range(50):
        state = asyncio.run(day10.comparison_step(live()))
        if state["phase"] == "finished":
            break
    assert state["phase"] == "finished" and model.await_count == 39
    assert state["results"]["summary"]["maintenance_calls"] == 11
