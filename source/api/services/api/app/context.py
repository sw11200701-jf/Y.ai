from app.contracts import BusinessContext
from app.settings import settings
from fastapi import Request
from app.errors import AppError

def get_context(request: Request) -> BusinessContext:
    context=getattr(request.state,"business_context",None)
    if context is None: raise AppError("AUTH_REQUIRED","Authentication required",401)
    if getattr(context,"principal_type","human")!="human":
        from app.machine_auth import is_open_path
        if is_open_path(request.url.path):return get_runtime_context(request)
        raise AppError("AUTH_SCHEME_NOT_ALLOWED","此入口只接受人类会话",401)
    return context

def get_runtime_context(request: Request) -> BusinessContext:
    context=getattr(request.state,"business_context",None)
    if context is None or getattr(context,"principal_type","human")!="service":raise AppError("APP_KEY_INVALID","需要有效的应用Key",401)
    return context
