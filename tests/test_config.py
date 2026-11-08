import pytest
from pydantic import ValidationError

from aiac.config import Settings


def test_live_provider_and_model_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIAC_LLM_MODE", "live")
    monkeypatch.setenv("AIAC_LLM_PROVIDER", "openai")
    monkeypatch.setenv("AIAC_LLM_MODEL", "test-model")
    settings = Settings(_env_file=None)
    assert settings.aiac_llm_mode == "live"
    assert settings.aiac_llm_provider == "openai"
    assert settings.aiac_llm_model == "test-model"


def test_old_mode_is_rejected_without_fallback() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, aiac_llm_mode="openai")
