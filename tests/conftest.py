import httpx
import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--live-openai",
        action="store_true",
        default=False,
        help="Allow explicitly selected live OpenAI tests (paid API calls).",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--live-openai"):
        return
    skip = pytest.mark.skip(reason="Live API disabled; requires explicit --live-openai")
    for item in items:
        if item.get_closest_marker("live_openai"):
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def no_real_api(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.node.get_closest_marker("live_openai") and request.config.getoption("--live-openai"):
        return
    monkeypatch.setenv("AIAC_LLM_MODE", "demo")
    monkeypatch.setenv("AIAC_LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("AIAC_LLM_MODEL", "")
    monkeypatch.setenv("AIAC_DAY_02_MODEL", "")

    async def blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("Real network calls are forbidden in offline tests")

    monkeypatch.setattr(httpx.AsyncClient, "send", blocked)

    def blocked_sync(*args: object, **kwargs: object) -> None:
        raise AssertionError("Real network calls are forbidden in offline tests")

    # TestClient uses an in-process transport, so it does not need real HTTP access.
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked_sync)
