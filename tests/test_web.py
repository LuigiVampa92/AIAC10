from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from aiac.tasks.registry import TASKS
from aiac.web.app import app


@pytest.fixture(autouse=True)
def no_real_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIAC_LLM_MODE", "demo")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("AIAC_OPENAI_MODEL", "")

    async def blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("Real network calls are forbidden in tests")

    monkeypatch.setattr(httpx.AsyncClient, "send", blocked)


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
    assert "ожидает условия" in response.text
    assert 'action="' + task.url + '"' in response.text
    run = client.post(task.url, data={"prompt": "Привет"})
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
    monkeypatch.setenv("AIAC_LLM_MODE", "openai")
    response = client.post("/weeks/1/days/1", data={"prompt": "test"})
    assert response.status_code == 503
    assert "нужны OPENAI_API_KEY" in response.text
