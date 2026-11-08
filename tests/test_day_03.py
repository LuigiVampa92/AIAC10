import asyncio
import itertools
import json
from fractions import Fraction
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from aiac.config import Settings
from aiac.llm import reasoning
from aiac.tasks.week_01 import day_03 as day
from aiac.web.app import app


def test_reference_answers():
    original = 3 * 1290 + 2 * 875
    discounted = Fraction(original) * (Fraction(85, 100) if original >= 5000 else 1)
    discounted *= Fraction(88, 100)
    rounded = -(-discounted.numerator // discounted.denominator)
    assert day.get_task("price")["answer"] == str(rounded + (0 if rounded >= 4500 else 290))
    stack = []
    for char in "cabbaabbacddceffegghhijkkji":
        if stack and stack[-1] == char:
            stack.pop()
        else:
            stack.append(char)
    assert day.get_task("string")["answer"] == "".join(stack)
    edges = dict(
        [
            ("AB", 4),
            ("AD", 2),
            ("DB", 1),
            ("BC", 5),
            ("DE", 4),
            ("EC", 1),
            ("CF", 3),
            ("EF", 2),
            ("FG", 2),
            ("BG", 4),
            ("CG", 9),
            ("DC", 9),
        ]
    )
    lengths = []
    for size in range(1, 6):
        for middle in itertools.permutations("BCDEF", size):
            if "C" not in middle:
                continue
            path = ("A", *middle, "G")
            pairs = ["".join(pair) for pair in itertools.pairwise(path)]
            if all(pair in edges for pair in pairs):
                lengths.append(sum(edges[pair] for pair in pairs))
    assert day.get_task("graph")["answer"] == str(min(lengths))
    intervals = [
        (0, 3, 5),
        (1, 4, 8),
        (3, 5, 6),
        (4, 7, 10),
        (5, 8, 7),
        (7, 9, 6),
        (8, 10, 9),
        (9, 11, 8),
    ]
    feasible = [
        sum(item[2] for item in group)
        for size in range(len(intervals) + 1)
        for group in itertools.combinations(intervals, size)
        if all(a[1] <= b[0] for a, b in itertools.pairwise(group))
    ]
    assert day.get_task("schedule")["answer"] == str(max(feasible))
    culprits = [
        name
        for name in ("Анна", "Борис", "Вера")
        if sum([name == "Борис", name != "Борис", name != "Анна"]) == 1
    ]
    assert culprits == [day.get_task("logic")["answer"]]


def test_revised_task_set_is_used_in_every_method():
    assert len(day.TASKS) == 5
    assert day.plan(day.Selection())["task_set_version"] == "v2"
    for task in day.TASKS:
        for method in day.METHODS:
            request = day.payload(
                day.RunInput(
                    task=task["id"], method=method, effort="none", generated_prompt="Реши задачу"
                )
            )
            assert task["text"] in request["input"]
        assert day.evaluate(json.dumps({"answer": task["answer"]}), task["id"], "direct")["correct"]
    for task_id, old_answer in [
        ("price", "1110"),
        ("string", "ca"),
        ("graph", "7"),
        ("schedule", "3"),
    ]:
        assert not day.evaluate(json.dumps({"answer": old_answer}), task_id, "direct")["correct"]


def test_plan_matrix_and_payloads():
    plan = day.plan(day.Selection())
    assert (plan["solutions"], plan["calls"]) == (40, 50)
    assert len({tuple(r.values()) for r in plan["runs"]}) == 40
    for run in plan["runs"]:
        config = day.RunInput(**run, generated_prompt="Разбери условия задачи")
        payload = day.payload(config)
        assert payload["reasoning"] == {"effort": run["effort"]}
        assert payload["model"] == "gpt-6-luna"
        assert payload["store"] is False
        assert day.get_task(run["task"])["text"] in payload["input"]
        if run["method"] == "meta":
            assert "Разбери условия задачи" in payload["input"]
    assert day.plan(day.Selection(task="logic", method="meta", effort="none"))["calls"] == 2
    with pytest.raises(ValueError):
        day.payload(day.RunInput(task="logic", method="meta", effort="max"))
    with pytest.raises(ValueError):
        day.plan(day.Selection(task="unknown"))


def test_grading_and_expert_contract():
    assert day.evaluate('{"answer":" анна "}', "logic", "direct")["correct"]
    for text in ('{"answer":"Борис"}', '{"answer":1110}', "1110", "broken"):
        assert not day.evaluate(text, "logic", "direct")["correct"]
    data = {
        "answer": "Анна",
        "experts": [
            {"role": role, "solution": "Проверка высказываний", "answer": "Анна"}
            for role in ("аналитик", "инженер", "критик")
        ],
    }
    assert day.evaluate(json.dumps(data), "logic", "experts")["correct"]
    data["experts"].pop()
    assert not day.evaluate(json.dumps(data), "logic", "experts")["correct"]


@pytest.mark.parametrize("status", ["incomplete", "refusal", "empty", "api_error"])
def test_unsuccessful_answers_never_pass(monkeypatch, status):
    mock = AsyncMock(
        return_value=reasoning.ReasoningResult(
            {"usage": {"total_tokens": 30}}, '{"answer":"Анна"}', day.MODEL, status, 1.5
        )
    )
    monkeypatch.setattr(reasoning, "complete", mock)
    result = asyncio.run(
        day.execute(
            day.RunInput(task="logic", method="direct", effort="none"), Settings(_env_file=None)
        )
    )
    assert result["correct"] is False


def test_meta_stages_and_usage(monkeypatch):
    mock = AsyncMock(
        side_effect=[
            reasoning.ReasoningResult(
                {"usage": {"total_tokens": 10}}, "Проверь высказывания", day.MODEL, "completed", 1.0
            ),
            reasoning.ReasoningResult(
                {"usage": {"total_tokens": 20}}, '{"answer":"Анна"}', day.MODEL, "completed", 2.0
            ),
        ]
    )
    monkeypatch.setattr(reasoning, "complete", mock)
    settings = Settings(_env_file=None)
    first = asyncio.run(
        day.execute(
            day.RunInput(task="logic", method="meta", effort="max", stage="prepare"), settings
        )
    )
    assert first["usable_prompt"]
    second = asyncio.run(
        day.execute(
            day.RunInput(task="logic", method="meta", effort="max", generated_prompt=first["text"]),
            settings,
        )
    )
    assert second["correct"]
    assert first["seconds"] + second["seconds"] == 3
    assert first["usage"]["total_tokens"] + second["usage"]["total_tokens"] == 30


def test_web_and_demo_guard(monkeypatch):
    monkeypatch.setenv("AIAC_LLM_MODE", "demo")
    with TestClient(app) as client:
        page = client.get("/weeks/1/days/3")
        assert page.status_code == 200
        assert "reasoning-form" in page.text
        assert "gpt-6-luna" in page.text
        assert client.post("/weeks/1/days/3/plan", json={}).json()["calls"] == 50
        assert (
            client.post(
                "/weeks/1/days/3/runs", json={"task": "logic", "method": "direct", "effort": "none"}
            ).status_code
            == 503
        )
        assert client.post("/weeks/1/days/3/plan", json={"task": "bogus"}).status_code == 422


def test_responses_boundary(monkeypatch):
    create = AsyncMock(
        return_value=SimpleNamespace(
            model=day.MODEL,
            status="completed",
            output_text='{"answer":"Анна"}',
            model_dump=lambda **kwargs: {"output": [], "usage": {"total_tokens": 6}},
        )
    )
    client = AsyncMock()
    client.__aenter__.return_value.responses.create = create
    factory = __import__("unittest.mock", fromlist=["Mock"]).Mock(return_value=client)
    monkeypatch.setattr(reasoning, "AsyncOpenAI", factory)
    settings = Settings(_env_file=None, aiac_llm_mode="live", openai_api_key="fake")
    payload = day.payload(day.RunInput(task="logic", method="direct", effort="max"))
    result = asyncio.run(reasoning.complete(payload, settings))
    assert result.status == "completed"
    create.assert_awaited_once_with(**payload)
    assert factory.call_args.kwargs["max_retries"] == 0
    assert factory.call_args.kwargs["timeout"] == 120.0
