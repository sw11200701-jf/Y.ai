"""Server-only, run-bound message attachments. This is not a KB search scope.

Only the bridge mints this context from the admitted message. Business adapters
may narrow it and obtain a permit for a same-byte standalone document. Neither
the capability nor its document permit is accepted in JSON/checkpoint state.
"""
import base64
import hashlib
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from app.errors import AppError

_seal = object()
_layers = ContextVar("execution_message_attachments", default=())


def _deny():
    raise AppError("NOT_FOUND", "附件不属于本次消息或无权访问", 404)


@dataclass(frozen=True)
class MessageAttachmentInput:
    run_id: str
    principal_id: str
    tenant_id: str
    workspace_id: str
    refs: tuple[tuple[str, str], ...]
    _trusted: object = field(repr=False, compare=False)

    def _check(self, context, session, attachment_id):
        from app.auth import validate_context
        from app.db import get_resource
        if self._trusted is not _seal or not any(layer is self for layer in _layers.get()):
            _deny()
        validate_context(context, session)
        identity = (context.operator_id, context.tenant_id, context.workspace_id)
        if identity != (self.principal_id, self.tenant_id, self.workspace_id):
            _deny()
        # Every enclosing message boundary is an AND restriction. A child can
        # never introduce a different run, caller, ID or admitted checksum.
        expected = dict(self.refs).get(attachment_id)
        if expected is None:
            _deny()
        for layer in _layers.get():
            if (layer.run_id, layer.principal_id, layer.tenant_id, layer.workspace_id) != (self.run_id, *identity):
                _deny()
            if layer._trusted is not _seal or dict(layer.refs).get(attachment_id) != expected:
                _deny()
        get_resource(session, "run", self.run_id, context)
        return expected

    def load(self, *, attachment_id, context, session):
        """Fresh actual attachment ACL, confined path and bytes checksum check."""
        from app.attachments import load_attachment
        expected = self._check(context, session, attachment_id)
        metadata, content = load_attachment(attachment_id=attachment_id, context=context, session=session)
        if metadata["sha256"] != expected:
            raise AppError("ATTACHMENT_CHANGED", "附件与本次消息固定的内容不一致", 409)
        return metadata, content

    def document(self, *, attachment_id, file_id, context, session):
        """Bind only the imported copy of these exact bytes, never an arbitrary KB."""
        permit = MessageDocumentPermit(self, attachment_id, file_id, _seal)
        permit.authorize(file_id=file_id, context=context, session=session)
        return permit


@dataclass(frozen=True)
class MessageDocumentPermit:
    source: MessageAttachmentInput
    attachment_id: str
    file_id: str
    _trusted: object = field(repr=False, compare=False)

    def authorize(self, *, file_id, context, session):
        from app.execution.effects import get_effect_context
        from app.data_access import authorize_data_resource, _unrestricted
        effect = get_effect_context()
        if self._trusted is not _seal or file_id != self.file_id or effect is None:
            _deny()
        if (effect.run_id, effect.principal_id, effect.tenant_id, effect.workspace_id) != (
                self.source.run_id, self.source.principal_id, self.source.tenant_id, self.source.workspace_id):
            _deny()
        metadata, _ = self.source.load(attachment_id=self.attachment_id, context=context, session=session)
        if metadata["media_kind"] != "document":
            raise AppError("MODEL_CAPABILITY_UNAVAILABLE", "图片和语音不能作为文档附件提取", 503)
        # Preserve actual document policy including maintain and full-row/column
        # requirements. Only the configured KB selector is a separate lane.
        use = authorize_data_resource(session=session, context=context, kind="document", resource_id=file_id)
        maintain = authorize_data_resource(session=session, context=context, kind="document", resource_id=file_id, action="maintain")
        if not _unrestricted(use) or not _unrestricted(maintain):
            raise AppError("DATA_SCOPE_WRITE_DENIED", "附件原文提取需要完整使用和维护范围", 403)
        import json
        from app.data_models import DataResourceLink
        doc = json.loads(use.resource.payload)
        if (use.resource.kind != "document" or use.resource.resource_id != file_id
                or session.get(DataResourceLink, use.resource.id) is not None
                or doc.get("knowledge_base_id") or doc.get("sha256") != metadata["sha256"]):
            _deny()
        try:
            digest = hashlib.sha256(base64.b64decode(doc["content_base64"], validate=True)).hexdigest()
        except (KeyError, ValueError) as exc:
            raise AppError("ATTACHMENT_CHANGED", "附件导入副本不可验证", 409) from exc
        if digest != metadata["sha256"]:
            raise AppError("ATTACHMENT_CHANGED", "附件导入副本与原件不一致", 409)
        return doc


def scope_for_run(run, context):
    """Called only by the bridge, using its freshly authorized stored run."""
    message = run.get("message", {})
    ids = message.get("attachment_ids", [])
    metadata = {item["id"]: item for item in message.get("attachments", [])}
    if not isinstance(ids, list) or len(ids) > 8 or len(set(ids)) != len(ids):
        _deny()
    if any(aid not in metadata or not isinstance(metadata[aid].get("sha256"), str) for aid in ids):
        _deny()
    return MessageAttachmentInput(run["id"], context.operator_id, context.tenant_id, context.workspace_id,
                                  tuple((aid, metadata[aid]["sha256"]) for aid in ids), _seal)


@contextmanager
def bind_message_attachments(scope):
    if not isinstance(scope, MessageAttachmentInput) or scope._trusted is not _seal:
        _deny()
    token = _layers.set(_layers.get() + (scope,))
    try:
        yield scope
    finally:
        _layers.reset(token)


@contextmanager
def select_message_attachments(execution, attachment_ids):
    """Compiler narrows to this node's declared input, without widening parents."""
    parent = execution.message_attachments
    if (not isinstance(parent, MessageAttachmentInput) or parent._trusted is not _seal
            or not any(layer is parent for layer in _layers.get()) or not isinstance(attachment_ids, list)):
        _deny()
    if len(set(attachment_ids)) != len(attachment_ids) or not set(attachment_ids) <= dict(parent.refs).keys():
        _deny()
    refs = dict(parent.refs)
    selected = MessageAttachmentInput(parent.run_id, parent.principal_id, parent.tenant_id, parent.workspace_id,
                                       tuple((aid, refs[aid]) for aid in attachment_ids), _seal)
    execution.message_attachments = selected
    try:
        with bind_message_attachments(selected):
            yield selected
    finally:
        execution.message_attachments = parent


def get_message_attachment_input():
    """04 adapter obtains the current trusted input; absence never means unlimited."""
    return _layers.get()[-1] if _layers.get() else None
