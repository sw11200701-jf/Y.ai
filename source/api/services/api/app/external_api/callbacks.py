"""Explicit central dependency injection; no fallback engine or identity."""
from app.errors import AppError

_callbacks={}
NAMES=frozenset({'prepare_release','provision_principal','authorize_resources','submit','get_run','events','cancel','resume','upload','download',
                 'trace','conversation','file_metadata','client_options'})


def configure(**callbacks):
    if not set(callbacks)<=NAMES or any(not callable(value) for value in callbacks.values()):raise ValueError('Invalid application callbacks')
    _callbacks.update(callbacks)


def callback(name):
    value=_callbacks.get(name)
    if value is None:raise AppError('APP_API_NOT_READY','应用API中央接点尚未就绪',503)
    return value
