"""Transient server scope forwarding. No identity/session/scope is checkpointed here."""
from contextlib import contextmanager
from contextvars import ContextVar
from app.errors import AppError
from .data_store import decision

_execution = ContextVar("business_node_execution", default=())


@contextmanager
def bind_execution(execution):
    token = _execution.set(_execution.get() + (execution,))
    try:
        yield
    finally:
        _execution.reset(token)


def scope_layers(explicit=None, *, knowledge=False):
    result = []
    inherited = [(execution.knowledge_scope if knowledge else execution.agent_scope) for execution in _execution.get()]
    for layer in inherited + [explicit]:
        if layer is None:
            continue
        if not isinstance(layer, list) or not layer or any(not isinstance(item, dict) or
            not isinstance(item.get("kind"), str) or not isinstance(item.get("resource_id"), str) for item in layer):
            raise AppError("NOT_FOUND", "资源不存在或不在智能体范围内", 404)
        result.append(layer)
    return result


def require_sources_in_scope(*, sources, layers, context, session, version_id=None):
    """Each server scope layer is an AND boundary; central policy matches each OR list."""
    for layer in layers:
        usable = [item for item in layer if item.get("version_id") in (None, version_id)]
        matched = False
        for kind, resource_id in sources:
            try:
                decision(session, kind, resource_id, context, agent_scope=usable)
                matched = True
                break
            except AppError as exc:
                if exc.status_code != 404:
                    raise
        if not matched:
            raise AppError("NOT_FOUND", "资源不存在或不在智能体范围内", 404)


def table_version(table, selected, layers):
    pins = None
    for layer in layers:
        matching = [item for item in layer if (item["kind"], item["resource_id"]) in
            (("database", table["database_id"]), ("data_table", table["id"]))]
        if not matching:
            # Central alias/version matching is checked after resolving the version.
            continue
        if any(item.get("version_id") is None for item in matching):
            continue
        versions = {item["version_id"] for item in matching}
        pins = versions if pins is None else pins & versions
    if pins is not None:
        if not pins or selected is not None and selected not in pins:
            raise AppError("NOT_FOUND", "固定版本不在智能体范围内", 404)
        if selected is None:
            if len(pins) != 1:
                raise AppError("INVALID_BINDING", "请明确选择智能体范围内的数据版本", 422)
            selected = next(iter(pins))
    return selected
