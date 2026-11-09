"""Transient trusted model authority; never attached to SDK models or graph state."""

from contextlib import contextmanager
from contextvars import ContextVar
from app.errors import AppError

_access = ContextVar("trusted_model_access", default=None)


@contextmanager
def bind_model_access(*, context, session):
    if context is None or session is None:
        raise AppError("AUTH_REQUIRED", "模型调用需要当前已登录用户的有效会话", 401)
    token = _access.set((context, session))
    try:
        yield
    finally:
        _access.reset(token)


def current_model_access():
    value = _access.get()
    if value is None:
        raise AppError("AUTH_REQUIRED", "模型调用缺少可信运行上下文", 401)
    return value
