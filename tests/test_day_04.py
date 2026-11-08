import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from aiac.config import Settings
from aiac.llm import reasoning
from aiac.tasks.week_01 import day_04 as day
from aiac.web.app import app


@pytest.mark.parametrize(("repeats", "calls"), [(1, 6), (5, 30), (10, 60)])
def test_matrix(repeats, calls):
    plan = day.plan(day.Selection(repeats=repeats))
    assert plan["calls"] == calls
    assert len({tuple(row.values()) for row in plan["runs"]}) == calls
    assert {r["temperature"] for r in plan["runs"]} == {0, 0.7, 1.2}
    assert (
        day.plan(day.Selection(task="price", temperature="0", repeats=repeats))["calls"] == repeats
    )
    for task in day.TASKS:
        requests = [
            day.payload(day.RunInput(task=task, temperature=t, repeat=1)) for t in day.TEMPERATURES
        ]
        unchanged = [
            {k: v for k, v in request.items() if k != "temperature"} for request in requests
        ]
        assert unchanged[0] == unchanged[1] == unchanged[2]
        assert unchanged[0]["reasoning"] == {"effort": "none"}
        assert "text" not in unchanged[0]  # No strict JSON API enforcement.


@pytest.mark.parametrize("repeats", [0, 11, 1.5, True, "5"])
def test_invalid_repeats(repeats):
    with pytest.raises(ValidationError):
        day.Selection(repeats=repeats)


def test_price_grade():
    good = json.dumps({"answer": "4494", "explanation": "4204 + 290 = 4494"})
    assert day.evaluate(good, "price")["correct"]
    mismatch = day.evaluate(good.replace('"4494"', '"3759"'), "price")
    assert mismatch["format_valid"] and not mismatch["correct"]
    for text in (
        '{"answer":"4494","answer":"3759","explanation":"x"}',
        '{"answer":4494,"explanation":"x"}',
        "[]",
        "null",
        "broken",
    ):
        assert not day.evaluate(text, "price")["format_valid"]


def test_names_grade():
    names = ["Зелёный друг", "Домашний сад", "Лист заботы", "Тихий рост", "Цветочный дом"]
    row = day.evaluate(json.dumps({"names": names}), "names")
    assert row["format_valid"] and row["correct"] is None
    assert (
        row["normalized"]
        == day.evaluate(json.dumps({"names": [n.upper() for n in reversed(names)]}), "names")[
            "normalized"
        ]
    )
    for bad in [
        names[:4],
        names + ["Шестое имя"],
        [names[0]] * 5,
        ["Зелёный-друг", *names[1:]],
        [None, *names[1:]],
        ["Green Friend", *names[1:]],
    ]:
        assert not day.evaluate(json.dumps({"names": bad}), "names")["format_valid"]


@pytest.mark.parametrize("status", ["completed", "incomplete", "refusal", "empty", "api_error"])
def test_execute_status_and_boundary(monkeypatch, status):
    raw = {"usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}}
    mock = AsyncMock(
        return_value=reasoning.ReasoningResult(
            raw, '{"answer":"4494","explanation":"x"}', day.MODEL, status, 1.5
        )
    )
    monkeypatch.setattr(reasoning, "complete", mock)
    config = day.RunInput(task="price", temperature=1.2, repeat=10)
    result = asyncio.run(day.execute(config, Settings(_env_file=None)))
    mock.assert_awaited_once_with(day.payload(config), Settings(_env_file=None), timeout_seconds=60)
    assert result["correct"] is (True if status == "completed" else None)
    assert result["usage"] == (None if status == "api_error" else raw["usage"])


def test_page_routes_and_demo_guard(monkeypatch):
    monkeypatch.setenv("AIAC_LLM_MODE", "demo")
    with TestClient(app) as client:
        page = client.get("/weeks/1/days/4")
        assert page.status_code == 200
        assert 'id="temperature-form"' in page.text
        assert 'min="1" max="10"' in page.text
        assert client.post("/weeks/1/days/4/plan", json={}).json()["calls"] == 30
        assert client.post("/weeks/1/days/4/plan", json={"repeats": 11}).status_code == 422
        assert (
            client.post(
                "/weeks/1/days/4/runs", json={"task": "price", "temperature": 0, "repeat": 1}
            ).status_code
            == 503
        )
        assert (
            client.post(
                "/weeks/1/days/4/runs", json={"task": "price", "temperature": 0.5, "repeat": 1}
            ).status_code
            == 422
        )
