"""Downloadable, runtime-only OpenAPI generated from the registered routes."""
from copy import deepcopy

from fastapi import Depends, Request
from fastapi.responses import JSONResponse

from app.db import get_session
from app.platform_api import PREFIX, _human, manage, runtime


def references(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == '$ref' and isinstance(child, str) and child.startswith('#/components/schemas/'):
                yield child.rsplit('/', 1)[-1]
            else:
                yield from references(child)
    elif isinstance(value, list):
        for child in value:
            yield from references(child)


def contract(app):
    source = app.openapi()
    paths = {path: deepcopy(operations) for path, operations in source['paths'].items() if path.startswith(PREFIX + '/')}
    for path, operations in paths.items():
        for operation in operations.values():
            operation['security'] = [] if path == PREFIX + '/token' else [{'PlatformBearer': []}]
    available = source.get('components', {}).get('schemas', {})
    schemas = {}
    pending = list(references(paths))
    while pending:
        name = pending.pop()
        if name not in schemas:
            schemas[name] = available[name]
            pending.extend(references(available[name]))
    return {
        'openapi': source['openapi'],
        'info': {'title': '章谱平台 API', 'version': '1.0',
                 'description': '工作流与 Agent 固定版本调用，授权知识库维护和检索，以及结构化数据库和数据表管理。'},
        'servers': [{'url': '/'}], 'paths': paths,
        'components': {'securitySchemes': {'PlatformBearer': {'type': 'http', 'scheme': 'bearer',
            'description': '通过 POST /api/platform/v1/token 交换的短令牌'}}, 'schemas': schemas},
    }


@manage.get('/platform/contract', summary='下载平台 API 的 OpenAPI JSON')
def download_contract(request: Request, session=Depends(get_session)):
    _human(request, session, 'platform.directory')
    return JSONResponse(contract(request.app), headers={'Content-Disposition': 'attachment; filename="zhangpu-platform-openapi.json"'})


@runtime.get('/openapi.json', summary='读取平台 API 的 OpenAPI JSON')
def read_contract(request: Request):
    return contract(request.app)
