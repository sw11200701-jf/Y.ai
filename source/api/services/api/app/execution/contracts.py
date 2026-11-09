"""Canonical parent/Agent bridge. Only ExecutionState is checkpointed."""
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol, TypedDict, TYPE_CHECKING, Annotated
from sqlalchemy.orm import Session
from app.contracts import BusinessContext
if TYPE_CHECKING:
    from app.execution.message_inputs import MessageAttachmentInput


def merge_branch_refs(left, right):
    return {**(left or {}), **(right or {})}


class ExecutionState(TypedDict):
    state_ref: str
    branch_refs: Annotated[dict[str, str], merge_branch_refs]


@dataclass
class ExecutionContext:
    run_id: str
    engine_key: str
    principal_id: str
    tenant_id: str
    workspace_id: str
    bridge: "SubgraphBridge"


@dataclass
class NodeExecution:
    context: BusinessContext
    session: Session
    run_id: str
    node_key: str
    execution_id: str
    state: dict
    invoke: Callable[..., Awaitable[Any]]
    agent_scope: list[dict] | None = None
    knowledge_scope: list[dict] | None = None
    deadline: str = ""
    budget: dict = field(default_factory=dict)
    message_attachments: "MessageAttachmentInput | None" = None
    model_context_reservation: dict | None = None


class SubgraphBridge(Protocol):
    def read(self, state: ExecutionState) -> dict: ...

    async def step(self, state: ExecutionState, *, node_key: str,
                   operation: Callable[[NodeExecution], Awaitable[dict]],
                   side_effect: bool = False,
                   preflight: Callable[[NodeExecution], Awaitable[None]] | None = None) -> ExecutionState: ...


ACTIVE_STATUSES = frozenset({"queued", "running", "resuming"})
WAITING_STATUSES = frozenset({"paused", "waiting_approval", "outcome_unknown"})
TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled", "interrupted"})
RUN_STATUSES = tuple(sorted(ACTIVE_STATUSES | WAITING_STATUSES | TERMINAL_STATUSES))


def capabilities(run: dict, *, is_actor: bool, may_approve: bool = False) -> dict:
    status = run["status"]
    durable = run.get("engine", {}).get("name") == "langgraph"
    return {"is_terminal": status in TERMINAL_STATUSES,
            "is_active": status in ACTIVE_STATUSES,
            "can_cancel": is_actor and status not in TERMINAL_STATUSES,
            "can_resume": is_actor and durable and status in {"paused", "waiting_approval"},
            "can_approve": may_approve and status == "waiting_approval",
            "can_reconcile": False}
