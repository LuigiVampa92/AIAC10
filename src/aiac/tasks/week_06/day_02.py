"""Неделя 6, день 2: условие ещё не получено."""

from aiac.config import Settings
from aiac.llm.client import ModelResult, generate


async def run(prompt: str, settings: Settings) -> ModelResult:
    # Пока только техническая проверка подключения, не реализация задания.
    return await generate(prompt, settings)
