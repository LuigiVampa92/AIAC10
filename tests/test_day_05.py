import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from aiac.config import Settings
from aiac.llm import catalog, reasoning
from aiac.tasks.week_01 import day_05 as day
from aiac.web.app import app


def selection(**kwargs):
    return day.Selection(
        **{role: {"model": model} for role, model in day.DEFAULTS.items()}, **kwargs
    )


def test_plan_and_dynamic_models():
    plan = day.plan(selection())
    assert plan["calls"] == 15
    assert day.plan(selection(repeats=10))["calls"] == 150
    assert [r["role"] for r in plan["runs"][:3]] == list(day.ROLES)
    config = selection()
    config.small = day.ModelSpec(model="gpt-4.1-mini-2025-04-14")
    changed = day.plan(config)
    assert changed["models"]["small"]["rates"]["input"] == 0.4
    assert changed["models"]["small"]["effort"] is None
    assert changed["models"]["small"]["model"] == config.small.model
    run = day.RunInput(**changed["runs"][0])
    assert "reasoning" not in day.payload(run)
    low = day.payload(day.RunInput(**plan["runs"][0]))
    assert low["reasoning"] == {"effort": "low"}
    for task in plan["tasks"]:
        requests = [day.payload(day.RunInput(**r)) for r in plan["runs"] if r["task"] == task["id"]]
        assert len({r["input"] for r in requests}) == 1
        assert "temperature" not in requests[0]


@pytest.mark.parametrize("repeats", [0, 11, True, 1.5])
def test_invalid_config(repeats):
    with pytest.raises(ValidationError):
        selection(repeats=repeats)


def test_same_models_and_unknown_task_rejected():
    with pytest.raises(ValidationError):
        day.Selection(**{role: {"model": "same"} for role in day.ROLES})
    with pytest.raises(ValueError):
        day.payload(day.RunInput(role="small", spec={"model": "custom"}, task="bad", repeat=1))


def test_cached_cost_and_reference_rates():
    rates = day.Rates(**day.default_rates("gpt-6-luna"))
    usage = {
        "input_tokens": 1000,
        "output_tokens": 400,
        "input_tokens_details": {"cached_tokens": 200},
    }
    assert day.cost(usage, rates) == pytest.approx(0.000282)
    custom = day.Rates(input=1, cached=0.5, output=2, source="manual", date="2026-11-09")
    assert day.cost(usage, custom) == pytest.approx(0.0017)
    for bad in [
        None,
        {},
        {**usage, "input_tokens": -1},
        {**usage, "input_tokens_details": {}},
        {**usage, "input_tokens_details": {"cached_tokens": 1001}},
        {**usage, "cache_creation_input_tokens": 1},
    ]:
        assert day.cost(bad, rates) is None
    assert day.cost(usage, None) is None
    assert day.default_rates("gpt-6-luna-new-snapshot") is None
    with pytest.raises(ValidationError):
        day.Rates(input=float("nan"), cached=0, output=1, source="manual", date="2026-11-09")


@pytest.mark.parametrize("status", ["completed", "incomplete", "refusal", "api_error"])
def test_execute(monkeypatch, status):
    usage = {"input_tokens": 10, "output_tokens": 20, "input_tokens_details": {"cached_tokens": 0}}
    monkeypatch.setattr(
        reasoning,
        "complete",
        AsyncMock(
            return_value=reasoning.ReasoningResult(
                {"usage": usage},
                '{"answer":"4494","explanation":"ok"}',
                "served-model",
                status,
                1.2,
            )
        ),
    )
    config = day.RunInput(role="small", spec={"model": "gpt-6-luna"}, task="price", repeat=1)
    row = asyncio.run(day.execute(config, Settings(_env_file=None)))
    assert row["model"] == "served-model" and row["requested_model"] == "gpt-6-luna"
    assert row["correct"] is (status == "completed")
    assert (row["cost_usd"] is None) is (status == "api_error")


def test_catalog_without_generation(monkeypatch):
    class Page:
        def __aiter__(self):
            async def values():
                for model in (
                    "gpt-4.1-mini",
                    "gpt-6-luna",
                    "gpt-4.1-mini",
                    "gpt-image-1",
                    "text-embedding-3-small",
                ):
                    yield SimpleNamespace(id=model)

            return values()

    client = AsyncMock()
    client.__aenter__.return_value.models.list.return_value = Page()
    factory = Mock(return_value=client)
    monkeypatch.setattr(catalog, "AsyncOpenAI", factory)
    settings = Settings(_env_file=None, aiac_llm_mode="live", openai_api_key="fake")
    assert asyncio.run(catalog.list_models(settings)) == ["gpt-4.1-mini", "gpt-6-luna"]
    client.__aenter__.return_value.responses.create.assert_not_called()
    assert factory.call_args.kwargs["max_retries"] == 0


def test_routes_and_demo(monkeypatch):
    monkeypatch.setenv("AIAC_LLM_MODE", "demo")
    with TestClient(app) as client:
        assert "models-form" in client.get("/weeks/1/days/5").text
        assert client.get("/weeks/1/days/5/models").status_code == 503
        plan = client.post("/weeks/1/days/5/plan", json=selection().model_dump()).json()
        assert plan["calls"] == 15
        assert client.post("/weeks/1/days/5/runs", json=plan["runs"][0]).status_code == 503
        monkeypatch.setattr(
            "aiac.web.app.list_models", AsyncMock(return_value=["old", "gpt-6-luna"])
        )
        data = client.get("/weeks/1/days/5/models")
        assert data.json()["models"][0] == {"id": "old", "rates": None}
        assert data.headers["cache-control"] == "no-store"


def test_text_catalog_filter():
    for model in (
        "gpt-6-luna",
        "gpt-4.1-mini-2025-04-14",
        "gpt-4o",
        "o3-mini",
        "o1",
        "chatgpt-4o-latest",
        "gpt-oss-120b",
        "ft:gpt-4.1-mini:org:custom:id",
    ):
        assert catalog.is_text_model(model), model
    for model in (
        "gpt-image-1",
        "gpt-4o-audio-preview",
        "gpt-realtime",
        "gpt-4o-mini-tts",
        "gpt-4o-transcribe",
        "text-embedding-3-small",
        "omni-moderation-latest",
        "whisper-1",
        "sora-2",
        "gpt-4o-search-preview",
        "unknown-family",
        "ft:gpt-4o-audio-preview:org:custom:id",
    ):
        assert not catalog.is_text_model(model), model


def test_model_choice_is_visible_before_task_details():
    with TestClient(app) as client:
        html = client.get("/weeks/1/days/5").text
    assert 'id="models-reload"' not in html
    assert html.index('id="models-small"') < html.index("<summary>Пять задач v2 и эталоны")
    assert html.index("</form>") < html.index("<summary>Пять задач v2 и эталоны")


def test_settings_are_automatic_and_cannot_be_overridden():
    for field, value in (("effort", "max"), ("rates", {})):
        with pytest.raises(ValidationError):
            day.ModelSpec(model="gpt-6-luna", **{field: value})
    for model in ("gpt-6-luna", "gpt-6.1-sol", "gpt-6-astra", "o3-mini"):
        assert day.reasoning_effort(model) == "low"
    for model in ("gpt-4.1-mini", "gpt-4o", "o1-mini", "unknown-family"):
        assert day.reasoning_effort(model) is None
    for model in ("gpt-4.1", "gpt-4.1-mini-2025-04-14", "gpt-4o", "gpt-4o-mini"):
        assert day.default_rates(model) is not None
    assert day.default_rates("ft:gpt-4.1-mini:org:model:id") is None


def test_page_has_no_manual_reasoning_or_price_fields():
    with TestClient(app) as client:
        html = client.get("/weeks/1/days/5").text
    assert 'id="models-small-effort"' not in html
    assert 'id="models-small-input"' not in html
    assert 'id="models-small-info"' in html
