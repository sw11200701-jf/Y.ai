"""Connection CRUD using the project's local credential adapter."""

import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from sqlalchemy.orm import Session

from app.contracts import BusinessContext
from app.db import get_resource, new_id, save_resource, utc_now
from app.errors import AppError
from app.plugins.errors import PluginError
from app.plugins.specs import StrictModel
from app.secrets import read_secret, store_secret
from .security import approved_address, origin, validate_base_url
from app.plugins.permissions import require_page


class ConnectionWrite(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(min_length=1, max_length=500)
    auth_type: Literal["none", "api_key", "bearer"] = "none"
    secret: SecretStr | None = None
    api_key_header: str = "X-API-Key"
    allowed_hosts: list[str] = Field(default_factory=list, max_length=1)
    enabled: bool = True

    @field_validator("api_key_header")
    @classmethod
    def safe_header(cls, value: str) -> str:
        if not re.fullmatch(r"[Xx]-[A-Za-z0-9-]{1,60}", value):
            raise ValueError("API Key 须使用 X- 开头的自定义请求头")
        return value

    @model_validator(mode="after")
    def safe_target(self):
        try:
            self.base_url = validate_base_url(self.base_url)
        except PluginError as exc:
            raise ValueError(exc.message) from exc
        host = urlsplit(self.base_url).hostname
        if not self.allowed_hosts:
            self.allowed_hosts = [host]
        if self.allowed_hosts != [host]:
            raise ValueError("allowed_hosts 必须精确匹配连接基址主机")
        if self.secret is not None:
            value = self.secret.get_secret_value()
            if not value or len(value) > 4096 or "\r" in value or "\n" in value:
                raise ValueError("凭据长度或格式无效")
        return self


def public_connection(payload: dict) -> dict:
    # Explicit allowlist also avoids accidentally exposing future private fields.
    fields = ("id", "name", "base_url", "auth_type", "api_key_header", "allowed_hosts", "enabled", "created_at", "updated_at")
    return {**{key: payload[key] for key in fields if key in payload},
            "credential_configured": bool(payload.get("secret_ref"))}


async def save_connection(data: ConnectionWrite, context: BusinessContext, session: Session,
                          connection_id: str | None = None) -> dict:
    require_page(context, session, "page.plugins")
    old = get_resource(session, "connection", connection_id, context, access="write") if connection_id else {}
    resource_id = connection_id or new_id("conn")
    from app.settings import settings
    allowed = getattr(context, "allowed_tool_origins", getattr(context, "allowed_origins", None))
    if allowed is None:
        allowed = getattr(settings, "tool_allowed_origins", None)
    try:
        await approved_address(data.base_url, allowed if allowed is not None else [origin(data.base_url)])
    except PluginError as exc:
        raise AppError("CONNECTION_BLOCKED", exc.message, status_code=403) from exc
    ref = old.get("secret_ref")
    if data.auth_type == "none":
        ref = None
    elif data.secret is not None:
        ref = new_id("credential")
        try:
            store_secret(ref, data.secret.get_secret_value())
        except Exception as exc:
            raise AppError("SERVICE_UNAVAILABLE", "本机凭据适配器写入失败", status_code=503) from exc
    elif not ref or old.get("auth_type") != data.auth_type or old.get("base_url") != data.base_url:
        raise AppError("VALIDATION_ERROR", "新鉴权方式或目标地址需要显式配置凭据", status_code=422)
    payload = {**data.model_dump(exclude={"secret"}), "id": resource_id, "secret_ref": ref,
               "created_at": old.get("created_at", utc_now()), "updated_at": utc_now()}
    save_resource(session, "connection", resource_id, payload, context)
    return public_connection(payload)
