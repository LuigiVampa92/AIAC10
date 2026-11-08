from dataclasses import dataclass
from importlib import import_module
from pathlib import Path

from aiac.config import ROOT, Settings
from aiac.llm.client import ModelResult


@dataclass(frozen=True)
class Task:
    week: int
    day: int

    @property
    def document(self) -> Path:
        return ROOT / "docs" / "tasks" / f"week_{self.week:02}" / f"day_{self.day:02}.md"

    @property
    def url(self) -> str:
        return f"/weeks/{self.week}/days/{self.day}"

    async def run(self, prompt: str, settings: Settings) -> ModelResult:
        module = import_module(f"aiac.tasks.week_{self.week:02}.day_{self.day:02}")
        result: ModelResult = await module.run(prompt, settings)
        return result


TASKS = {(week, day): Task(week, day) for week in range(1, 8) for day in range(1, 6)}
