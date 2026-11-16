"""One atomically stored workspace containing isolated branches and checkpoints."""

from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from aiac.agents.storage import Conversation, HistoryError, JsonHistory
from aiac.agents.strategies import StrategyConversation

MAX_BRANCHES = 8
MAX_CHECKPOINTS = 8


class Checkpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str
    source_branch: str
    state: StrategyConversation


class Workspace(Conversation):
    active_branch: str = "main"
    branches: dict[str, StrategyConversation] = Field(
        default_factory=lambda: {"main": StrategyConversation()}
    )
    checkpoints: dict[str, Checkpoint] = Field(default_factory=dict)


class AttachedWriter:
    """Persist a child mutation together with its parent, under the parent's lock."""

    def __init__(self, source: JsonHistory, parent: Conversation, child: Conversation) -> None:
        self.source, self.parent, self.child = source, parent, child

    def save(self, state: Conversation, name: str = "conversation.json") -> None:
        if state is not self.child or name != "conversation.json":
            raise HistoryError("Некорректная запись состояния ветки.")
        self.source.save(self.parent)


def current(workspace: Workspace) -> StrategyConversation:
    if workspace.active_branch not in workspace.branches:
        raise HistoryError("Активная ветка не найдена.")
    return workspace.branches[workspace.active_branch]


def checkpoint(workspace: Workspace, name: str) -> str:
    branch = current(workspace)
    if branch.status not in ("empty", "completed"):
        raise HistoryError("Checkpoint доступен только после завершённого хода.")
    if len(workspace.checkpoints) >= MAX_CHECKPOINTS:
        raise HistoryError("Достигнут лимит 8 checkpoint. Создайте новое рабочее пространство.")
    key = uuid4().hex
    workspace.checkpoints[key] = Checkpoint(
        name=name, source_branch=workspace.active_branch, state=branch.model_copy(deep=True)
    )
    return key


def fork_pair(workspace: Workspace, key: str) -> tuple[str, str]:
    if key not in workspace.checkpoints:
        raise HistoryError("Checkpoint не найден.")
    if len(workspace.branches) + 2 > MAX_BRANCHES:
        raise HistoryError("Достигнут лимит 8 веток. Создайте новое рабочее пространство.")
    saved = workspace.checkpoints[key]
    ids = (uuid4().hex, uuid4().hex)
    for suffix, branch_id in zip(("A", "B"), ids, strict=True):
        child = saved.state.model_copy(deep=True)
        child.id = branch_id
        child.name = f"{saved.name} · {suffix}"
        child.parent_branch, child.checkpoint_id = saved.source_branch, key
        workspace.branches[branch_id] = child
    workspace.active_branch = ids[0]
    return ids


def select(workspace: Workspace, branch_id: str) -> None:
    if branch_id not in workspace.branches:
        raise HistoryError("Ветка не найдена.")
    workspace.active_branch = branch_id
