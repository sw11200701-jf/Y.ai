"""Generate exact DTO/OpenAPI handoff from code, without starting any server."""
import json
from pathlib import Path
from fastapi import FastAPI
from . import schemas
from .management_router import router as management_router
from .runtime_router import router as runtime_router
from . import public_schemas
from .public_contract import runtime_openapi


def export(directory=None,revision='v3'):
    root=Path(directory) if directory else Path(__file__).parent
    app=FastAPI(title='Application API V1 domain contract',version='1.0.0')
    app.include_router(management_router,prefix='/api/v1');app.include_router(runtime_router,prefix='/api/open/v1')
    spec=app.openapi()
    runtime=runtime_openapi()
    spec['paths'].update(runtime['paths'])
    spec['components']['schemas'].update(runtime['components']['schemas'])
    spec['components'].setdefault('securitySchemes',{}).update(runtime['components']['securitySchemes'])
    # Validation schemas are the same strict Pydantic DTOs used by routes.
    names=['ApplicationCreate','ApplicationPatch','ApplicationView','Source','VersionPreview','VersionPublish','VersionView','ActivateVersion',
        'ClientCreate','ClientPatch','ClientView','KeyCreate','KeyRotate','KeyMetadata','KeyReceipt','Quotas','ResourceConstraints','RuntimePolicy',
        'WorkflowInvocation','ChatInvocation','Message','PartitionAction','RuntimeAuthority','ReleaseBundleV1','ResourceOption','OptionBlocker','ClientOptionsView',
        'TokenCounts','UsageView','TimingView','CallView','CallPage']
    dtos={name:getattr(schemas,name).model_json_schema() for name in names}
    for name in ('RunReceipt','ApplicationInfo','ApplicationParameters','FileMetadata','ConversationView','TraceView','ErrorEnvelope','ProgressEvent','ResultEvent','StreamError','OpenAPIContract'):
        dtos[name]=getattr(public_schemas,name).model_json_schema()
    for filename,value in [(f'openapi-domain-{revision}-20261027.json',spec),(f'dto-domain-{revision}-20261027.json',dtos),(f'openapi-runtime-{revision}-20261027.json',runtime)]:
        (root/filename).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return len(spec['paths']),len(dtos)


if __name__=='__main__':print('paths,DTOs:',*export())
