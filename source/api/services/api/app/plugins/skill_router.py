from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from app.context import get_context
from app.db import get_session
from app.errors import AppError
from .skill_packages import MAX_UPLOAD, SkillDraft, parse_upload
from .permissions import require_page
from . import skill_service as service

router = APIRouter(prefix='/plugins/skills', tags=['skills'])


class Revision(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_revision: int = Field(ge=1)


class DraftUpdate(Revision):
    draft: SkillDraft


async def upload(request):
    body = bytearray()
    async for part in request.stream():
        if len(body)+len(part) > MAX_UPLOAD:
            raise AppError('PAYLOAD_TOO_LARGE', '技能上传超过1MiB', 413)
        body.extend(part)
    return bytes(body)


@router.post('/preview')
async def preview(request: Request, filename: str = Query(max_length=150),
                  context=Depends(get_context), session=Depends(get_session, scope='function')):
    require_page(context, session, 'page.plugins')
    return parse_upload(filename, await upload(request))


@router.post('/upload', status_code=201)
async def create(request: Request, filename: str = Query(max_length=150),
                 context=Depends(get_context), session=Depends(get_session, scope='function')):
    require_page(context, session, 'page.plugins')
    parsed = parse_upload(filename, await upload(request))
    return service.create_skill(draft=parsed['draft'], upload_sha256=parsed['upload_sha256'], context=context, session=session)


@router.get('')
def listing(context=Depends(get_context), session=Depends(get_session, scope='function')):
    return service.list_skills(context=context, session=session)


@router.get('/{skill_id}')
def detail(skill_id: str, context=Depends(get_context), session=Depends(get_session, scope='function')):
    return service.read_skill(skill_id=skill_id, context=context, session=session)


@router.put('/{skill_id}')
def update(skill_id: str, body: DraftUpdate, context=Depends(get_context), session=Depends(get_session, scope='function')):
    return service.update_skill(skill_id=skill_id, draft=body.draft.model_dump(mode='json'),
        expected_revision=body.expected_revision, context=context, session=session)


@router.post('/{skill_id}/publish')
def publish(skill_id: str, body: Revision, context=Depends(get_context), session=Depends(get_session, scope='function')):
    return service.publish_skill(skill_id=skill_id, expected_revision=body.expected_revision, context=context, session=session)


@router.post('/{skill_id}/archive')
def archive(skill_id: str, body: Revision, context=Depends(get_context), session=Depends(get_session, scope='function')):
    return service.archive_skill(skill_id=skill_id, expected_revision=body.expected_revision, context=context, session=session)


@router.get('/{skill_id}/versions/{version_id}')
def version(skill_id: str, version_id: str, context=Depends(get_context), session=Depends(get_session, scope='function')):
    return service.resolve_skill_version(skill_id=skill_id, version_id=version_id, context=context, session=session)
