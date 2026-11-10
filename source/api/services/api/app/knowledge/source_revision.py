"""Replace a document source while keeping immutable originals and knowledge versions."""
import hashlib

from app.db import utc_now
from app.errors import AppError
from app.imports.documents import import_document
from app.platform_api import write_lock
from app.rbac import audit
from . import service as s
from .lifecycle import _no_processing


def etag(value):
    return '"' + s.digest(value) + '"'


def check_etag(value, expected):
    if expected is None:
        raise AppError('PRECONDITION_REQUIRED', '请先读取资源并在 If-Match 中提交当前 ETag', 428)
    if expected != etag(value):
        raise AppError('KNOWLEDGE_REVISION_CONFLICT', '资源已更新，请重新读取后再提交', 412)


async def replace_original(*, document_id, filename, content, profile, expected, context, session):
    write_lock(session)
    root = s.entry('knowledge_document', document_id, context, session, write=True)
    s.require_full_document_maintenance(root, context, session)
    old = s.raw_file(root, context, session, write=True)
    name = filename.replace('\\', '/').split('/')[-1][:150]
    if (old['sha256'] == hashlib.sha256(content).hexdigest() and old['filename'] == name
            and old.get('upload_profile', 'legacy') == profile):
        return root, False
    check_etag(root, expected)
    _no_processing('knowledge_document', root, context, session, include_unknown=True)
    uploaded = await import_document(filename=filename, content=content, knowledge_base_id=root['knowledge_base_id'],
                                     profile=profile, context=context, session=session, deduplicate=False)
    source = s.get_resource(session, 'document', uploaded['id'], context, access='write')
    source['knowledge_document_id'] = document_id
    s.save_resource(session, 'document', source['id'], source, context)
    root.update(file_id=source['id'], name=source['filename'], draft_version_id=None, updated_at=utc_now())
    if root['status'] != 'published':
        root['status'] = 'uploaded'
    root.pop('last_processing', None)
    s.save_resource(session, 'knowledge_document', document_id, root, context)
    s.link(session, context, 'document', source['id'], 'knowledge_document', document_id)
    audit(session, context, 'knowledge_original_replace', 'knowledge_document', document_id,
          {'previous_file_id': old['id'], 'file_id': source['id']})
    return root, True
