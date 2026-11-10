"""Model endpoint checks extend, rather than relax, the existing gateway rules."""
import asyncio
import ipaddress
from urllib.parse import urlsplit
import httpx
from app.connections.security import approved_address, validate_base_url
from app.plugins.errors import PluginError
from app.errors import AppError
from .public_resolver import POLICY,MODE,resolve_public

MODEL_BASE_PATHS = frozenset(('', '/v1', '/api/v3', '/compatible-mode/v1'))


def validate_model_url(value):
    try:
        normalized = validate_base_url(value)
        parsed = urlsplit(normalized)
        original = urlsplit(value)
        allowed_paths = MODEL_BASE_PATHS | {path + '/' for path in MODEL_BASE_PATHS}
        if (original.path not in allowed_paths or parsed.port == 0
                or any(c.isspace() for c in value) or '?' in value or '#' in value
                or '@' in original.netloc or any(c in original.netloc for c in '%{}<>')):
            raise ValueError()
        if parsed.hostname in ('metadata.google.internal', 'metadata.goog', 'instance-data.ec2.internal'):
            raise AppError('CONNECTION_BLOCKED', '禁止云元数据地址', 403)
        try:
            ip = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            return normalized
        blocked_address(ip)
        return normalized
    except PluginError as exc:
        raise AppError(exc.code, '模型服务地址须为无凭据、无查询参数的HTTP(S)基址', 422) from None
    except (ValueError, TypeError):
        raise AppError('VALIDATION_ERROR', '模型服务地址只支持HTTP(S)基址和/v1、/api/v3、/compatible-mode/v1路径', 422) from None


def blocked_address(ip):
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if (ip.is_link_local or ip.is_multicast or ip.is_unspecified or (ip.is_reserved and not ip.is_loopback)
            or str(ip) in {'100.100.100.200', 'fd00:ec2::254'}):
        raise AppError('CONNECTION_BLOCKED', '禁止云元数据、链路本地及保留地址', 403)
    return ip


def validate_model_address_policy(config):
    """Pure URL/literal-IP admission. Hostname DNS is rechecked at send time."""
    return validate_model_endpoint(config.base_url, config.allow_private_network)


def validate_model_endpoint(value, allow_private_network):
    url = validate_model_url(value)
    parsed = urlsplit(url)
    if parsed.scheme == 'http' and not allow_private_network:
        raise AppError('CONNECTION_BLOCKED', '公网模型必须使用 HTTPS；仅可为经授权的内网模型使用 HTTP', 403)
    host = parsed.hostname
    try:
        address=ipaddress.ip_address(host)
    except ValueError:
        return url  # No DNS, socket, credentials or provider request in admission.
    address=blocked_address(address)
    enforce_model_transport(parsed, address, allow_private_network)
    return url


def enforce_model_transport(parsed, address, allow_private_network):
    if address.is_global and parsed.scheme == 'http':
        raise AppError('CONNECTION_BLOCKED', '公网模型必须使用 HTTPS', 403)
    if not address.is_global and not allow_private_network:
        raise AppError('CONNECTION_BLOCKED', '内网或本机模型地址需要管理员明确允许', 403)


async def approved_model_address(config):
    url = validate_model_address_policy(config)
    parsed = urlsplit(url)
    base = f'{parsed.scheme}://{parsed.netloc}'
    if POLICY.mode==MODE and parsed.scheme=='https' and not config.allow_private_network:
        try:
            ipaddress.ip_address(parsed.hostname)
        except ValueError:
            # Only explicit PUBLIC-ONLY HTTPS HOSTNAMES use the trusted mode.
            # Private-authorized names, literals and HTTP keep the whole old
            # path. Never disclose an explicitly internal name to public DNS.
            from app.connections.security import origin
            if origin(base)!=origin(url):
                raise AppError('CONNECTION_BLOCKED','模型精确origin不符',403)
            result=await resolve_public(parsed.hostname,selected=POLICY)
            addresses=result['addresses']
            for value in addresses:
                ip=blocked_address(ipaddress.ip_address(value))
                if not ip.is_global:
                    raise AppError('CONNECTION_BLOCKED','可信解析不接受内网、Fake-IP或非全球地址',403)
            return addresses[0] # Existing caller pins IP; original Host/SNI/TLS.
    try:
        # Existing exact origin, bounded DNS and forbidden address checks remain.
        await approved_address(base, [base])
        rows = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(
            parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80), type=1), timeout=3)
        addresses = sorted({row[4][0] for row in rows}, key=lambda value: (ipaddress.ip_address(value).version, int(ipaddress.ip_address(value))))
        if not addresses:
            raise OSError()
        for value in addresses:
            ip = blocked_address(ipaddress.ip_address(value))
            enforce_model_transport(parsed, ip, config.allow_private_network)
        return addresses[0]  # Caller MUST connect to this IP, retaining original Host/SNI.
    except PluginError as exc:
        raise AppError(exc.code, '模型服务地址被连接策略拒绝或无法解析', exc.status_code) from None
    except (OSError, ValueError, TimeoutError):
        raise AppError('MODEL_ERROR', '无法解析已配置的模型服务地址', 502) from None


async def test_connection(config, *, context, session):
    """No inference or customer prompt. Bounded GET to an administrator endpoint."""
    address = await approved_model_address(config)
    from .service import managed_row, PROVIDER
    _, current = managed_row(session, context, PROVIDER, config.model_config_id, write=True)
    if config.config_version != f'p{current["revision"]}':
        raise AppError('MODEL_VERSION_CHANGED', '平台配置在连通检查期间变化，请重试', 409)
    parsed = urlsplit(config.base_url)
    host = f'[{address}]' if ':' in address else address
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    target = f'{parsed.scheme}://{host}:{port}{parsed.path.rstrip("/")}/models'
    headers = {'Host': parsed.netloc, 'Accept': 'application/json'}
    if config.secret_ref:
        from app.secrets import read_secret
        try:
            token = read_secret(config.secret_ref)
        except Exception:
            token = None
        if not token or '\r' in token or '\n' in token:
            raise AppError('MODEL_CREDENTIAL_MISSING', '平台认证密钥不可用', 409)
        headers['Authorization'] = 'Bearer ' + token
    try:
        async with asyncio.timeout(config.timeout_seconds), httpx.AsyncClient(trust_env=False,
                follow_redirects=False, timeout=config.timeout_seconds) as client:
            async with client.stream('GET', target, headers=headers, extensions={'sni_hostname': parsed.hostname}) as response:
                if response.status_code not in (200, 404, 405, 501):
                    raise AppError('MODEL_ERROR', '平台连通测试失败，请核对地址和认证', 502)
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 262144:
                        raise AppError('MODEL_ERROR', '平台连通响应超过限制', 502)
                listing_supported = response.status_code == 200
    except AppError:
        raise
    except (TimeoutError, httpx.TimeoutException):
        raise AppError('MODEL_TIMEOUT', '平台连通测试超时', 504) from None
    except Exception:
        raise AppError('MODEL_ERROR', '平台连通测试未完成', 502) from None
    return {'connected': True, 'inference_verified': False, 'test_kind': 'connection',
            'model_listing_supported': listing_supported,
            'message': ('平台模型列表地址已响应；尚未验证模型推理' if listing_supported else
                        '地址已响应，但未提供模型列表；尚未验证认证和推理，请手动添加模型后做推理测试')}
