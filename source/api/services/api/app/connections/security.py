"""Exact-origin allowlist, address checks, and bounded secret redaction."""

import asyncio
import ipaddress
import socket
from typing import Any
from urllib.parse import unquote, urlsplit

from app.plugins.errors import PluginError


def origin(url: str) -> str:
    try:
        p = urlsplit(url)
        if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
            raise ValueError()
        host = p.hostname.lower()
        port = p.port or (443 if p.scheme == "https" else 80)
        formatted = f"[{host}]" if ":" in host else host
        return f"{p.scheme}://{formatted}:{port}"
    except ValueError as exc:
        raise PluginError("VALIDATION_ERROR", "连接地址须为无凭据的 HTTP(S) 地址", 422) from exc


def validate_base_url(url: str) -> str:
    origin(url)
    p = urlsplit(url)
    decoded = p.path
    for _ in range(6):
        after = unquote(decoded)
        if after == decoded:
            break
        decoded = after
    if (p.query or p.fragment or "\\" in url or "\\" in decoded or "%" in decoded
            or any(part in {".", ".."} for part in decoded.split("/")) or decoded.startswith("//")
            or any(ord(c) < 32 for c in url + decoded)):
        raise PluginError("VALIDATION_ERROR", "连接基址路径无效，不能包含凭据、跳转或路径逃逸", 422)
    return url.rstrip("/")


async def approved_address(url: str, allowed_origins: list[str] | set[str]) -> str:
    requested = origin(url)
    if requested not in {origin(item) for item in allowed_origins}:
        raise PluginError("CONNECTION_BLOCKED", "目标不在实例管理员允许的地址列表内", 403)
    p = urlsplit(url)
    try:
        rows = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(
                p.hostname, p.port or (443 if p.scheme == "https" else 80), type=socket.SOCK_STREAM
            ), timeout=3,
        )
        addresses = sorted({row[4][0] for row in rows})
        if not addresses:
            raise ValueError()
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
                ip = ip.ipv4_mapped
            if (ip.is_link_local or ip.is_multicast or ip.is_unspecified or (ip.is_reserved and not ip.is_loopback)
                    or str(ip) in {"100.100.100.200", "fd00:ec2::254"}):
                raise PluginError("CONNECTION_BLOCKED", "禁止云元数据、链路本地及保留地址", 403)
        return addresses[0]
    except PluginError:
        raise
    except (OSError, ValueError, TimeoutError) as exc:
        raise PluginError("TOOL_ERROR", "无法解析允许的目标地址", 502) from exc


SENSITIVE_KEYS = {"authorization", "proxy-authorization", "x-api-key", "api_key", "apikey",
                  "token", "access_token", "refresh_token", "password", "secret", "credential", "credential_ref"}


def redact(value: Any, secrets: tuple[str, ...] = ()) -> Any:
    if isinstance(value, dict):
        return {redact(str(k), secrets): "[REDACTED]" if str(k).lower() in SENSITIVE_KEYS else redact(v, secrets)
                for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, secrets) for v in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
    return value
