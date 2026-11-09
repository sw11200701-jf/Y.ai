"""Versioned passive guidance resources using ordinary current-caller resource ACL."""
import hashlib
import json
from sqlalchemy import select, update
from app.db import Resource, get_resource, list_resources, new_id, save_resource, utc_now
from app.errors import AppError
from .permissions import require_page, require_tool_access
from .skill_packages import SkillDraft, canonical, package_hash


def create_skill(*, draft, context, session, upload_sha256=None):
    require_page(context, session, 'page.plugins')
    value = SkillDraft.model_validate(draft).model_dump(mode='json')
    result = {'id': new_id('skill'), 'resource_type': 'passive_skill', 'draft': value,
              'content_sha256': package_hash(value), 'status': 'draft', 'revision': 1,
              'executable': False, 'tools': [], 'created_at': utc_now(), 'updated_at': utc_now()}
    if upload_sha256:
        result['upload_sha256'] = upload_sha256
    save_resource(session, 'skill', result['id'], result, context)
    return result


def read_skill(*, skill_id, context, session):
    require_tool_access(context, session)
    return get_resource(session, 'skill', skill_id, context)


def list_skills(*, context, session):
    require_tool_access(context, session)
    return {'items': [{key: value for key, value in skill.items() if key != 'draft'} | {
        'name': skill['draft']['name'], 'description': skill['draft']['description']}
        for skill in list_resources(session, 'skill', context)]}


def _cas(*, skill_id, expected_revision, context, session, transform):
    require_page(context, session, 'page.plugins')
    old = get_resource(session, 'skill', skill_id, context, access='write')
    if type(expected_revision) is not int or old.get('revision') != expected_revision:
        raise AppError('SKILL_REVISION_CONFLICT', '技能已被修改，请刷新后再保存', 409)
    row = session.scalar(select(Resource).where(Resource.kind == 'skill', Resource.resource_id == skill_id,
        Resource.tenant_id == context.tenant_id, Resource.workspace_id == context.workspace_id))
    if row is None:
        raise AppError('SKILL_REVISION_CONFLICT', '技能已被删除或修改，请刷新后再保存', 409)
    before = row.payload
    if json.loads(before) != old:
        raise AppError('SKILL_REVISION_CONFLICT', '技能已被修改，请刷新后再保存', 409)
    value = transform(old)
    value.update(revision=expected_revision+1, updated_at=utc_now())
    count = session.execute(update(Resource).where(Resource.id == row.id, Resource.payload == before).values(
        payload=canonical(value), updated_at=value['updated_at']), execution_options={'synchronize_session': False}).rowcount
    if count != 1:
        raise AppError('SKILL_REVISION_CONFLICT', '技能并发修改冲突，请刷新后再保存', 409)
    session.expire(row)
    return value


def update_skill(*, skill_id, draft, expected_revision, context, session):
    parsed = SkillDraft.model_validate(draft).model_dump(mode='json')
    def change(old):
        if old['status'] == 'archived':
            raise AppError('SKILL_ARCHIVED', '已归档技能不能修改，请新建技能', 409)
        return {**old, 'draft': parsed, 'content_sha256': package_hash(parsed), 'status': 'draft'}
    return _cas(skill_id=skill_id, expected_revision=expected_revision, context=context, session=session, transform=change)


def publish_skill(*, skill_id, expected_revision, context, session):
    def change(old):
        if old['status'] == 'archived':
            raise AppError('SKILL_ARCHIVED', '已归档技能不能发布', 409)
        draft = SkillDraft.model_validate(old['draft']).model_dump(mode='json')
        content_hash = package_hash(draft)
        version_id = 'sv_' + hashlib.sha256((skill_id+':'+content_hash).encode()).hexdigest()
        return {**old, 'status': 'published', 'published_version_id': version_id}
    root = _cas(skill_id=skill_id, expected_revision=expected_revision, context=context, session=session, transform=change)
    identifier = root['published_version_id']
    existing = session.scalar(select(Resource).where(Resource.kind == 'skill_version', Resource.resource_id == identifier,
        Resource.tenant_id == context.tenant_id, Resource.workspace_id == context.workspace_id))
    if existing is None:
        version = {'id': identifier, 'skill_id': skill_id, 'content_sha256': root['content_sha256'],
            'draft': root['draft'], 'published_at': utc_now(), 'executable': False, 'tools': [], 'status': 'published'}
        save_resource(session, 'skill_version', identifier, version, context)
    else:
        version = json.loads(existing.payload)
        if (version['skill_id'] != skill_id or version['content_sha256'] != root['content_sha256']
                or package_hash(version['draft']) != root['content_sha256']):
            raise AppError('SKILL_VERSION_MISMATCH', '技能已发布快照与当前内容不一致', 409)
    return {'skill': root, 'version': version}


def archive_skill(*, skill_id, expected_revision, context, session):
    return _cas(skill_id=skill_id, expected_revision=expected_revision, context=context, session=session,
        transform=lambda old: {**old, 'status': 'archived'})


def resolve_skill_version(*, skill_id, version_id, context, session):
    """Read guidance only. Caller must ALSO authorize its actual tool/model bindings."""
    root = read_skill(skill_id=skill_id, context=context, session=session)
    if root['status'] == 'archived':
        raise AppError('SKILL_ARCHIVED', '技能已归档，不能继续装载指导资料', 409)
    # Parent read permission is the authority for this immutable child; no generic
    # version lookup can bypass parent revocation or discover another parent's text.
    row = session.scalar(select(Resource).where(Resource.kind == 'skill_version', Resource.resource_id == version_id,
        Resource.tenant_id == context.tenant_id, Resource.workspace_id == context.workspace_id))
    if row is None:
        raise AppError('NOT_FOUND', '技能已发布版本不存在或无权访问', 404)
    version = json.loads(row.payload)
    if version['skill_id'] != skill_id:
        raise AppError('NOT_FOUND', '技能已发布版本不存在或无权访问', 404)
    if version['status'] != 'published' or package_hash(version['draft']) != version['content_sha256']:
        raise AppError('SKILL_VERSION_MISMATCH', '技能版本内容或状态不一致', 409)
    return version
