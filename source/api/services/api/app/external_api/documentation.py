"""Human read-only contract endpoint, generated from the actual runtime routes."""
from urllib.parse import urlsplit
from app.errors import AppError
from app.settings import settings
from . import service
from .bridge import _bundle
from .public_contract import runtime_openapi
from .storage import load


def public_base_url():
    value=settings.application_public_base_url
    if not value:return None,'not_configured'
    try:
        parts=urlsplit(value)
        if parts.port is not None and not 1<=parts.port<=65535:raise ValueError()
    except (ValueError,TypeError):raise AppError('APP_BASE_URL_INVALID','服务器应用调用地址配置无效',503) from None
    secure=parts.scheme=='https'
    local=parts.scheme=='http' and parts.hostname in {'localhost','127.0.0.1','::1'} and settings.development_http
    if not parts.hostname or not (secure or local) or any(c.isspace() or ord(c)<32 for c in value) or parts.username or parts.password or parts.query or parts.fragment or parts.path!='/api/open/v1':
        raise AppError('APP_BASE_URL_INVALID','服务器应用调用地址配置无效',503)
    return value,'configured' if secure else 'development'


def application_contract(session,context,app_id,version_id,client_id):
    app=service.application(session,context,app_id,'applications.read')
    client=service.client(session,app_id,client_id)
    if version_id not in load(client.version_ids):raise AppError('APP_VERSION_UNAVAILABLE','调用方未允许此固定版本',422)
    version=service._version(session,app_id,version_id);bundle=_bundle(version)
    url,url_status=public_base_url()
    spec=runtime_openapi(application_id=app_id,version_id=version_id,kind=app.kind,input_schema=bundle['input_schema'],output_schema=bundle['output_schema'],
        public_base_url=url,scopes=load(client.scopes),external_user_mode=client.external_user_mode,runtime_policy=bundle['runtime_policy'])
    spec['x-application-contract']={'application_id':app_id,'version_id':version_id,'client_id':client_id,'application_status':app.status,'client_status':client.status,
        'public_base_url':url,'public_base_url_status':url_status,'scope_semantics':'client_ceiling_key_may_be_narrower',
        'source':'actual_runtime_routes_and_pydantic','contract_version':'v3'}
    return spec
