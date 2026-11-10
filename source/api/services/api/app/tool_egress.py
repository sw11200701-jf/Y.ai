"""Workspace tool-origin policy, bounded by the deployment egress list."""
import json
from urllib.parse import urlsplit
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from app.connections.security import origin, validate_base_url
from app.db import get_session
from app.errors import AppError
from app.plugins.errors import PluginError
from app.settings import settings

KIND = "tool_origin_policy"
IDENTIFIER = "workspace"
router = APIRouter(tags=["tool-network-policy"])


def _origins(values):
    result = set()
    for value in values:
        try:
            result.add(origin(value.strip()))
        except PluginError:
            # Malformed entries grant no network access.
            continue
    return result


def configured_tool_origins():
    return sorted(_origins(settings.tool_allowed_origins))


def _policy(session, tenant, workspace):
    from app.platform_api import _row
    row = _row(session, KIND, IDENTIFIER, tenant, workspace)
    return json.loads(row.payload) if row else None


def effective_tool_origins(session, tenant, workspace):
    ceiling = set(configured_tool_origins())
    current = _policy(session, tenant, workspace)
    return sorted(ceiling & set(current["origins"])) if current is not None else sorted(ceiling)


def origins_for_context(session, context):
    trusted = getattr(context, "allowed_tool_origins", context.allowed_origins)
    if trusted is None:
        trusted = configured_tool_origins()
    return sorted(set(effective_tool_origins(session, context.tenant_id, context.workspace_id)) & _origins(trusted))


def _validate_origin(value):
    try:
        normalized = validate_base_url(value.strip())
        parsed = urlsplit(normalized)
        if parsed.path or parsed.port == 0 or "?" in value or "#" in value or any(c.isspace() for c in value.strip()):
            raise ValueError()
        return origin(normalized)
    except (ValueError, PluginError):
        raise AppError("VALIDATION_ERROR", "填写无路径、无凭据的 HTTP 或 HTTPS 服务源地址", 422) from None


class OriginPolicyWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    origins: list[str] = Field(max_length=128)
    expected_revision: int = Field(ge=0)


def _administrator(request, session):
    from app.rbac import super_context, require_permission
    context = super_context(request, session)
    require_permission(context, session, "page.plugins")
    return context


def _view(session, context):
    current = _policy(session, context.tenant_id, context.workspace_id)
    return {"deployment_origins": configured_tool_origins(),
            "origins": effective_tool_origins(session, context.tenant_id, context.workspace_id),
            "revision": current["revision"] if current else 0}


@router.get("/connections/origin-policy")
def get_origin_policy(request: Request, session=Depends(get_session)):
    return _view(session, _administrator(request, session))


@router.put("/connections/origin-policy")
def put_origin_policy(body: OriginPolicyWrite, request: Request, session=Depends(get_session)):
    from app.platform_api import _put, write_lock
    from app.rbac import audit
    write_lock(session)
    context = _administrator(request, session)
    current = _view(session, context)
    if current["revision"] != body.expected_revision:
        raise AppError("TOOL_ORIGIN_POLICY_CHANGED", "服务地址清单已变化，请刷新后再保存", 409)
    values = sorted({_validate_origin(value) for value in body.origins})
    if not set(values) <= set(current["deployment_origins"]):
        raise AppError("TOOL_ORIGIN_OUTSIDE_DEPLOYMENT", "地址超出部署允许范围，请由部署管理员配置后再添加", 403)
    revision = body.expected_revision + 1
    _put(session, KIND, IDENTIFIER, {"origins": values, "revision": revision},
         context.tenant_id, context.workspace_id, "__system__")
    audit(session, context, "tool_origin_policy.update", KIND, IDENTIFIER, {"revision": revision, "origin_count": len(values)})
    return _view(session, context)
