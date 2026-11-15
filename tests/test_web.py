from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from aiac.llm.client import ModelResult
from aiac.tasks.registry import TASKS
from aiac.web.app import app


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def test_health_and_index(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}
    response = client.get("/")
    assert response.status_code == 200
    assert response.text.count('class="card"') == 35


@pytest.mark.parametrize(("week", "day"), list(TASKS))
def test_each_day_has_page_and_separate_module(client: TestClient, week: int, day: int) -> None:
    task = TASKS[week, day]
    assert task.document.is_file()
    response = client.get(task.url)
    assert response.status_code == 200
    assert "Решение и проверка" not in response.text
    assert "Повторная валидация по запросу" not in response.text
    assert "Критерии приёмки" not in response.text
    assert 'action="' + task.url + '"' in response.text
    run = client.post(task.url, data={"prompt": "Привет"})
    if (week == 1 and day in (2, 3, 4, 5)) or (week == 2 and day in (1, 2)):
        assert run.status_code == 422
        assert "Используйте форму эксперимента" in run.text
        return
    assert run.status_code == 200
    assert "API не вызывался" in run.text


@pytest.mark.parametrize("url", ["/weeks/0/days/1", "/weeks/8/days/1", "/weeks/1/days/6"])
def test_invalid_day(client: TestClient, url: str) -> None:
    assert client.get(url).status_code == 404
    assert client.post(url, data={"prompt": "test"}).status_code == 404


@pytest.mark.parametrize("prompt", ["", "   ", "x" * 4001])
def test_invalid_prompt(client: TestClient, prompt: str) -> None:
    assert client.post("/weeks/1/days/1", data={"prompt": prompt}).status_code == 422


def test_output_is_escaped(client: TestClient) -> None:
    response = client.post("/weeks/1/days/1", data={"prompt": "<script>alert(1)</script>"})
    assert "<script>" not in response.text
    assert "&lt;script&gt;" in response.text


def test_missing_credentials_is_safe(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIAC_LLM_MODE", "live")
    response = client.post("/weeks/1/days/1", data={"prompt": "test"})
    assert response.status_code == 503
    assert "нужны OPENAI_API_KEY" in response.text


def test_first_day_debug_empty_and_demo(client: TestClient) -> None:
    page = client.get("/weeks/1/days/1")
    assert "Запрос к API · JSON" in page.text
    assert "Ответ API · JSON" in page.text
    demo = client.post("/weeks/1/days/1", data={"prompt": "hello"})
    assert "Ответ API отсутствует" in demo.text
    assert "Запрос к API не отправлялся" in demo.text


def test_model_information_is_read_only(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AIAC_LLM_MODE", "live")
    monkeypatch.setenv("AIAC_LLM_MODEL", "test-display-model")
    page = client.get("/weeks/1/days/1")
    assert "test-display-model" in page.text
    assert "<strong>openai</strong>" in page.text
    assert 'name="model"' not in page.text
    assert "ChatGPT Plus" not in page.text
    assert "Заготовки не являются выполненными заданиями" not in page.text


def test_first_day_debug_json_is_escaped(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_run(self: object, prompt: str, settings: object) -> ModelResult:
        return ModelResult(
            text="answer",
            mode="live",
            model="test-model",
            request_json='{"input": "<script>request</script>"}',
            response_json='{"output": "<script>response</script>", "usage": {}}',
        )

    monkeypatch.setattr("aiac.tasks.registry.Task.run", fake_run)
    response = client.post("/weeks/1/days/1", data={"prompt": "hello"})
    assert response.status_code == 200
    assert "<script>" not in response.text
    assert "&lt;script&gt;request&lt;/script&gt;" in response.text
    assert "&lt;script&gt;response&lt;/script&gt;" in response.text
