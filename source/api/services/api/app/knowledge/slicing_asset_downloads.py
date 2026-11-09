"""Authenticated original image bytes, bound to a real committed recipe Run."""
import hashlib
from io import BytesIO
import os
from urllib.parse import urlsplit, quote
from PIL import Image
from app.errors import AppError
from app.db import get_resource
from .slicing_contracts import fail
from .slicing_parser import parse_document


def occurrence_url(plan, occurrence, *, context, session):
    from app.reporting.document_artifacts import issued_state
    effect, _, run = issued_state(context, session)
    if (effect.node_key != f'semantic:{plan["job_id"]}:finalize'
            or run.get('semantic_job', {}).get('job_id') != plan['job_id']):
        fail('图片URL须绑定本任务真实签发的最终提交步骤', 'SLICING_ASSET_URL_UNAVAILABLE')
    value = os.environ.get('FINANCE_KNOWLEDGE_PUBLIC_BASE_URL', '').rstrip('/')
    parsed = urlsplit(value)
    if (parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.scheme == 'http' and parsed.hostname not in ('localhost', '127.0.0.1', '::1')):
        fail('请配置受信知识附件公共入口；未生成假URL或公开上传', 'SLICING_ASSET_URL_UNAVAILABLE')
    oid = occurrence['occurrence_id']; aid = occurrence['asset_id']
    if plan['registry']['occurrences'].get(oid) != occurrence or aid not in plan['registry']['assets']: fail('图片出现未绑定固定来源')
    from .slicing_public_image_cache import lookup
    existing_public=lookup(plan,plan['registry']['assets'][aid])
    if existing_public is not None:return existing_public
    parts = [plan['document_id'], plan['document_version_id'], effect.run_id, oid, aid]
    doc, version, run, occ, asset = [quote(v, safe='') for v in parts]
    return f'{value}/api/v1/knowledge/documents/{doc}/versions/{version}/runs/{run}/images/{occ}/{asset}/download'


def install():
    from .slicing_assets import register_occurrence_url_resolver
    register_occurrence_url_resolver(occurrence_url)


def authorized_image_source(*, document_id, version_id, run_id, context, session):
    from app.execution.semantic_jobs import load_plan
    from .slicing_document import source_guard
    from .slicing_finalizer import authorize_export
    from . import service
    from app.data_access import _unrestricted
    root = service.entry('knowledge_document', document_id, context, session)
    version = service.checked_version(version_id, root, context, session)
    if not _unrestricted(service.version_decision(version, root, context, session)):
        raise AppError('DATA_SCOPE_READ_DENIED', '完整图片需要当前全文版本授权', 403)
    if version.get('status') == 'published':
        # A library reader gets no access to the producer's private Run. Verify
        # its committed witness internally, under the reader's source rights.
        from .slicing_retrieval import verified_published_version
        file = service.raw_file({**root, 'file_id': version['file_id']}, context, session)
        authorize_export(None, root, file, context=context, session=session, version_id=version_id)
        kind = 'document' if file['id'].startswith('document_') else 'knowledge_file'
        if not _unrestricted(service.decision(session, kind, file['id'], context, 'download_original')):
            raise AppError('DATA_SCOPE_READ_DENIED', '图片原件下载需要当前原件下载权限', 403)
        if version.get('portable_execution_run_id') != run_id or not verified_published_version(version, context=context, session=session):
            raise AppError('NOT_FOUND', '图片版本缺少真实已提交来源凭据', 404)
        registry = version['portable_registry']
        import base64
        content = base64.b64decode(file['content_base64'], validate=True)
        if (hashlib.sha256(content).hexdigest() != registry['source_sha256'] or version['source_sha256'] != registry['source_sha256']):
            fail('已发布图片的固定原件已变化', 'SLICING_SOURCE_CHANGED')
    else:
        run = get_resource(session, 'run', run_id, context)
        if run.get('status') != 'succeeded': raise AppError('NOT_FOUND', '图片处理尚未完成提交', 404)
        plan = load_plan(session, context, run, completed_read=True)['plan']
        if plan['document_id'] != document_id or plan['document_version_id'] != version_id:
            raise AppError('NOT_FOUND', '图片不属于此文档版本', 404)
        root, file, content = source_guard(plan, context=context, session=session, completed=True)
        registry = plan['registry']
        authorize_export(plan, root, file, context=context, session=session, version_id=version_id)
    kind = 'document' if file['id'].startswith('document_') else 'knowledge_file'
    if not _unrestricted(service.decision(session, kind, file['id'], context, 'download_original')): raise AppError('DATA_SCOPE_READ_DENIED', '图片原件下载需要当前原件下载权限', 403)
    from .execution_scope import scope_layers, require_sources_in_scope
    refs = [('knowledge_base', root['knowledge_base_id']), ('knowledge_document', document_id), (kind, file['id'])]
    require_sources_in_scope(sources=refs, layers=scope_layers(knowledge=True), context=context, session=session, version_id=version_id)
    return root, file, content, registry


def checked_image(*, document_id, version_id, run_id, occurrence_id, asset_id, context, session):
    root, file, content, registry = authorized_image_source(document_id=document_id, version_id=version_id, run_id=run_id, context=context, session=session)
    occurrence = registry['occurrences'].get(occurrence_id)
    if not occurrence or occurrence['asset_id'] != asset_id or asset_id not in registry['assets']:
        raise AppError('NOT_FOUND', '图片出现或资源不存在', 404)
    found = {}
    def capture(identifier, image):
        if identifier == asset_id: found.setdefault(identifier, image)
    # Reading a cached image never starts OCR, conversion or a model request.
    if file['format'] in ('doc', '.doc'): fail('DOC图片须使用已持久化转换资产，禁止下载时重做转换', 'SLICING_ASSET_UNAVAILABLE')
    parsed = parse_document(content, file['format'], file_id=file['id'], version_sha256=file['sha256'], perform_ocr=False, asset_sink=capture,image_layout_version=registry.get('image_layout_version'))
    expected = registry['assets'][asset_id]
    image = found.get(asset_id)
    current = parsed['occurrences'].get(occurrence_id)
    if (image is None or current is None or current['asset_id'] != asset_id or current['locator'] != occurrence['locator']
            or hashlib.sha256(image).hexdigest() != expected['sha256'] or len(image) != expected['size_bytes']): fail('实际图片、出现位置或原始来源已变化', 'SLICING_SOURCE_CHANGED')
    with Image.open(BytesIO(image)) as opened:
        mime = Image.MIME.get(opened.format, 'application/octet-stream')
        extension = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/gif': '.gif', 'image/bmp': '.bmp', 'image/tiff': '.tif', 'image/webp': '.webp'}.get(mime)
        if extension is None or opened.size != (expected['width'], expected['height']): fail('图片尺寸或下载格式不符')
    return {'content': image, 'mime': mime, 'filename': asset_id + extension, 'sha256': expected['sha256']}
