"""Atomic, local JSON conversation storage with a cross-process writer lock."""

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from aiac.config import ROOT


class HistoryError(Exception):
    """Safe storage error that may be shown in the UI."""


class Conversation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: int = 1
    id: str = Field(default_factory=lambda: uuid4().hex, pattern=r"^[a-f0-9]{32}$")
    model: str = ""
    status: str = "empty"
    history: list[dict[str, Any]] = Field(default_factory=list)
    turns: list[dict[str, Any]] = Field(default_factory=list)
    tools: list[str] = Field(
        default_factory=lambda: ["fixed_exchange", "calculator", "current_time"]
    )


class JsonHistory:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def path(self, name: str) -> Path:
        path = self.directory / name
        if not path.resolve().is_relative_to(ROOT.resolve()):
            raise HistoryError("Каталог истории должен находиться внутри проекта.")
        if path.is_symlink():
            raise HistoryError("Файлы истории не должны быть символическими ссылками.")
        return path

    @contextmanager
    def lock(self) -> Iterator[None]:
        path = self.path(".lock")
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with path.open("a") as stream:
                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise HistoryError(
                        "Диалог занят другим запросом. Дождитесь завершения."
                    ) from None
                try:
                    yield
                finally:
                    fcntl.flock(stream, fcntl.LOCK_UN)
        except OSError:
            raise HistoryError("Не удалось открыть или сохранить локальную историю.") from None

    def load(self) -> Conversation:
        path = self.path("conversation.json")
        try:
            if not path.exists():
                return Conversation()
            if path.stat().st_size > 16_000_000:
                raise HistoryError("Файл истории слишком большой. Он оставлен без изменений.")
            state = Conversation.model_validate_json(path.read_text(encoding="utf-8"))
            if state.version != 1:
                raise HistoryError("Неизвестная версия истории. Файл оставлен без изменений.")
            return state
        except (OSError, ValueError, ValidationError):
            raise HistoryError("Не удалось прочитать историю. Файл не перезаписан.") from None

    def save(self, state: Conversation, name: str = "conversation.json") -> None:
        target = self.path(name)
        data = state.model_dump_json(indent=2)
        if len(data.encode("utf-8")) > 16_000_000:
            raise HistoryError("Достигнут предел файла истории. Запрос остановлен.")
        temporary: Path | None = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.directory, delete=False
            ) as f:
                temporary = Path(f.name)
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, target)
        except OSError:
            raise HistoryError("Не удалось сохранить историю. Продолжение остановлено.") from None
        finally:
            if temporary and temporary.exists():
                temporary.unlink()

    def new(self) -> Conversation:
        old = self.load()
        if old.turns:
            self.save(old, f"archive-{old.id}.json")
        state = Conversation()
        self.save(state)
        return state
