import json
import base64
from fastapi.responses import Response
from typing import Literal
from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.context import get_context
from app.db import get_session
from app.db import SessionLocal
from starlette.concurrency import run_in_threadpool
from app.errors import AppError
from app import workflow_file_service as service
router = APIRouter(tags=['workflow-files'])

async def read(file):
    value = await file.read(2 * 1024 * 1024 + 1)
    if len(value) > 2 * 1024 * 1024: raise AppError('FILE_LIMIT', '工作流文件超过2MiB限制', 413)
    return value

@router.post('/workflows/file-preview')
async def preview(file: UploadFile = File(...), session=Depends(get_session), context=Depends(get_context)):
    return service.preview(await read(file), file.filename or '', session, context)

@router.get('/workflows/file-resources')
def resources(session=Depends(get_session), context=Depends(get_context)):
    return service.catalog(session, context)

@router.post('/workflows/file-confirm')
async def confirm(file: UploadFile = File(...), preview_token: str = Form(..., max_length=4000), source_hash: str = Form(..., max_length=64),
    idempotency_key: str = Form(..., min_length=1, max_length=100), binding_resolutions: str = Form('[]', max_length=100000),
    name: str | None = Form(None, max_length=200), session=Depends(get_session), context=Depends(get_context)):
    try: selections = json.loads(binding_resolutions)
    except (ValueError, RecursionError): raise AppError('INVALID_BINDING', '资源选择格式无效', 422) from None
    content = await read(file)
    filename = file.filename or ''
    # A synchronous SQLite writer wait on the async API loop prevents the first
    # request's transaction middleware from committing, stalling every request.
    # Confirmation owns an independent Session entirely inside one worker.
    current = context.model_copy(deep=True)
    def persist():
        with SessionLocal() as isolated:
            try:
                result = service.confirm(content, filename, preview_token, source_hash, idempotency_key, selections, name, isolated, current)
                isolated.commit()
                return result
            except BaseException:
                isolated.rollback()
                raise
    return await run_in_threadpool(persist)

class ExportBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source: Literal['editor', 'saved']
    workflow: dict | None = None
    workflow_id: str | None = Field(None, max_length=160)
    expected_sha256: str | None = Field(None, pattern='^[a-f0-9]{64}$')
    format: Literal['native-json', 'native-yaml', 'dify-yaml'] = 'native-json'
    target_version: Literal['0.4.0'] = '0.4.0'
    model_bindings: dict = Field(default_factory=dict, max_length=100)
    report_only: bool = False
    transport: Literal['base64', 'attachment'] = 'base64'
    @model_validator(mode='after')
    def exclusive(self):
        if self.source == 'saved' and (not self.workflow_id or self.workflow is not None) or self.source == 'editor' and (self.workflow is None or self.workflow_id is not None):
            raise ValueError('请选择当前编辑稿或已保存流程')
        return self

@router.post('/workflows/file-export')
def export(body: ExportBody, session=Depends(get_session), context=Depends(get_context)):
    result = service.export(body, session, context)
    if body.report_only:
        return {'blocked': result['report']['blocked'], 'export_report': result['report'],
            'media_type': 'application/json' if body.format == 'native-json' else 'application/yaml',
            'extension': '.json' if body.format == 'native-json' else '.yaml'}
    if body.transport == 'base64':
        return result
    if result['file'] is None:
        raise AppError('FILE_EXPORT_BLOCKED', '该格式存在未解决的兼容或资源问题，请先查看报告', 422,
            details={'export_report': result['report']})
    file = result['file']
    return Response(base64.b64decode(file['content_base64']), media_type=file['media_type'],
        headers={'Content-Disposition': 'attachment; filename="' + file['filename'] + '"', 'Cache-Control': 'no-store'})
