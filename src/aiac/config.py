from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    aiac_llm_mode: Literal["demo", "live"] = "demo"
    aiac_llm_provider: str = "openai"
    aiac_llm_model: str = ""
    aiac_day_02_model: str = ""
    openai_api_key: SecretStr | None = None
