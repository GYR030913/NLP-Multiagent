"""Small structural contracts between orchestration and teammate modules."""

from typing import Any, Protocol, TypedDict, runtime_checkable


class MessageEnvelope(TypedDict):
    seq: int
    time: str
    kind: str
    sender: str
    to: str
    task: str
    text: str


class HumanRequest(TypedDict, total=False):
    id: str
    task: str
    kind: str
    status: str
    created_at: str
    question: str
    summary: str
    artifacts: list[str]
    action: dict[str, Any]


@runtime_checkable
class ToolGateway(Protocol):
    def needs_approval(self, name: str) -> bool: ...

    def execute(
        self,
        state: dict[str, Any],
        agent: str,
        name: str,
        args: dict[str, Any],
    ) -> Any: ...

    def validate_artifacts(
        self,
        state: dict[str, Any],
        artifact_ids: list[str],
    ) -> None: ...


@runtime_checkable
class MemoryGateway(Protocol):
    def recall(
        self,
        agent: str,
        query: str,
        limit: int = 6,
    ) -> list[dict[str, Any]]: ...

    def remember(
        self,
        agent: str,
        text: str,
        task: str,
        outcome: str,
    ) -> None: ...

    def snapshot(self) -> dict[str, str]: ...


@runtime_checkable
class HumanInteractionGateway(Protocol):
    def request(self, task: dict[str, Any], kind: str, **payload: Any) -> str: ...

    def answer(
        self,
        request_id: str,
        text: str = "",
        approved: bool | None = None,
    ) -> None: ...
